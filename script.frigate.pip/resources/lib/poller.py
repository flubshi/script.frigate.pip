# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Polling threads for camera pictures and Frigate statistics, plus health evaluation.

Stall detection: every downloaded JPEG is hashed. If its content stops
changing, the camera stream (or Frigate) is stuck - even if the HTTP requests
still "succeed". In addition, network errors, hanging requests and
(optionally) camera_fps from /api/stats are evaluated.

This module does not import any Kodi module, so it can be tested on its own.
"""
import hashlib
import os
import threading
import time
from collections import deque

from . import messages
from .frigate import FrigateError, HTTPStatusError, describe_error

LEVEL_CONNECTING = 0
LEVEL_OK = 1
LEVEL_WARN = 2
LEVEL_ERROR = 3

LEVEL_NAMES = {
    LEVEL_CONNECTING: 'CONNECTING',
    LEVEL_OK: 'OK',
    LEVEL_WARN: 'WARNING',
    LEVEL_ERROR: 'ERROR',
}

NO_RESPONSE_AFTER = 2.0   # s, an open request counts as "hanging" after this time
KEEP_FRAMES = 12          # number of picture files kept per camera
FPS_WINDOW = 3.0          # s, time window for the fps display
MAX_BACKOFF = 5.0         # s, longest pause between attempts after errors


def fmt_age(seconds):
    """Human readable age: '3.4 s', '42 s', '5 min', '3 h'."""
    seconds = max(0.0, seconds)
    if seconds < 10:
        return '%s s' % messages.decimal(seconds)
    if seconds < 120:
        return '%d s' % int(seconds)
    if seconds < 7200:
        return '%d min' % int(seconds // 60)
    return '%d h' % int(seconds // 3600)


def _safe_name(camera):
    return ''.join(c if (c.isalnum() or c in '-_') else '_' for c in camera)


class CameraPoller(threading.Thread):
    """Fetches latest.jpg of one camera at a fixed interval and stores new pictures."""

    def __init__(self, client, camera, frame_dir, height=None, interval=0.25,
                 bbox=False, timestamp=True, logger=None, transform=None):
        threading.Thread.__init__(self, name='frigatepip-cam-%s' % camera)
        self.daemon = True
        self.client = client
        self.camera = camera
        self.frame_dir = frame_dir
        self.height = height
        self.interval = max(0.05, float(interval))
        self.bbox = bool(bbox)
        self.timestamp = bool(timestamp)
        self._log = logger or (lambda msg: None)
        self.transform = transform     # e.g. image section/zoom; None = picture unchanged

        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._prefix = '%s_%04x' % (_safe_name(camera), id(self) & 0xffff)
        self._last_hash = None
        self._next_seq = 0
        self._written = deque()
        self._changes = deque(maxlen=64)

        self.started = time.monotonic()
        self.seq = 0
        self.path = None
        self.last_ok = None
        self.last_change = None
        self.last_error = None       # (time, exception)
        self.inflight_since = None
        self.err_count = 0
        self.latency = None

    # ------------------------------------------------------------------ control
    def stop(self):
        self._stop_evt.set()

    def state(self):
        """Snapshot of the current state (thread-safe)."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._changes if now - t <= FPS_WINDOW]
            if len(recent) >= 2 and recent[-1] > recent[0]:
                fps = (len(recent) - 1) / (recent[-1] - recent[0])
            else:
                fps = 0.0
            return {
                'camera': self.camera,
                'started': self.started,
                'seq': self.seq,
                'path': self.path,
                'last_ok': self.last_ok,
                'last_change': self.last_change,
                'last_error': self.last_error,
                'inflight_since': self.inflight_since,
                'err_count': self.err_count,
                'fps': fps,
                'latency': self.latency,
            }

    def cleanup(self):
        """Delete all picture files written by this poller."""
        while self._written:
            path = self._written.popleft()
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------------ thread
    def _store(self, data):
        """Store a new picture. Returns (seq, path), or None if it did not change."""
        # Stall detection always works on the original picture
        digest = hashlib.blake2b(data, digest_size=16).digest()
        if digest == self._last_hash:
            return None
        if self.transform is not None:
            try:
                data = self.transform(data)
            except Exception as exc:
                raise FrigateError('err_crop', reason=describe_error(exc))
        self._next_seq += 1
        path = os.path.join(self.frame_dir, '%s_%d.jpg' % (self._prefix, self._next_seq))
        try:
            with open(path, 'wb') as handle:
                handle.write(data)
        except OSError as exc:
            raise FrigateError('err_store_image', reason=exc.strerror or str(exc))
        self._last_hash = digest
        self._written.append(path)
        while len(self._written) > KEEP_FRAMES:
            old = self._written.popleft()
            try:
                os.remove(old)
            except OSError:
                pass
        return self._next_seq, path

    def run(self):
        try:
            while not self._stop_evt.is_set():
                t0 = time.monotonic()
                with self._lock:
                    self.inflight_since = t0
                try:
                    data = self.client.snapshot(self.camera, height=self.height,
                                                bbox=self.bbox, timestamp=self.timestamp)
                    stored = self._store(data)
                except Exception as exc:   # catch everything: this thread must never die
                    now = time.monotonic()
                    with self._lock:
                        self.inflight_since = None
                        self.err_count += 1
                        count = self.err_count
                        previous = self.last_error
                        self.last_error = (now, exc)
                    if count == 1 or previous is None or describe_error(previous[1]) != describe_error(exc):
                        self._log('%s: request failed: %s' % (self.camera, describe_error(exc)))
                    delay = min(MAX_BACKOFF, max(self.interval, 0.5 * (2 ** min(count - 1, 4))))
                    if getattr(exc, 'definitive', False):
                        delay = MAX_BACKOFF
                    self._stop_evt.wait(delay)
                    continue

                now = time.monotonic()
                with self._lock:
                    recovered = self.err_count > 0
                    self.inflight_since = None
                    self.last_ok = now
                    self.latency = now - t0
                    self.err_count = 0
                    if stored is not None:
                        self.seq, self.path = stored
                        self.last_change = now
                        self._changes.append(now)
                if recovered:
                    self._log('%s: requests work again' % self.camera)

                wait = self.interval - (time.monotonic() - t0)
                if wait > 0:
                    self._stop_evt.wait(wait)
        finally:
            try:
                self.client.drop_connection()
            except Exception:
                pass


class StatsPoller(threading.Thread):
    """Polls /api/stats to see whether Frigate receives frames from the cameras."""

    def __init__(self, client, interval=5.0, logger=None):
        threading.Thread.__init__(self, name='frigatepip-stats')
        self.daemon = True
        self.client = client
        self.interval = float(interval)
        self._log = logger or (lambda msg: None)
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._fps = {}
        self._time = 0.0
        self.disabled_reason = None

    def stop(self):
        self._stop_evt.set()

    def camera_fps(self):
        """{camera: fps} if recent statistics are available, else {}."""
        with self._lock:
            if time.monotonic() - self._time > 3 * self.interval:
                return {}
            return dict(self._fps)

    def run(self):
        try:
            while not self._stop_evt.is_set():
                try:
                    stats = self.client.stats()
                    cams = stats.get('cameras') if isinstance(stats, dict) else None
                    fps = {}
                    if isinstance(cams, dict):
                        for name, values in cams.items():
                            if isinstance(values, dict) and values.get('camera_fps') is not None:
                                try:
                                    fps[str(name)] = float(values['camera_fps'])
                                except (TypeError, ValueError):
                                    pass
                    with self._lock:
                        self._fps = fps
                        self._time = time.monotonic()
                except HTTPStatusError as exc:
                    if exc.status in (401, 403, 404):
                        self.disabled_reason = exc.log_text
                        self._log('Statistics not available (%s) - stall detection uses pictures only'
                                  % exc.log_text)
                        return
                except Exception:
                    pass   # the camera pollers report connection problems anyway
                self._stop_evt.wait(self.interval)
        finally:
            try:
                self.client.drop_connection()
            except Exception:
                pass


def evaluate(st, now, warn_s, freeze_s, cam_fps=None):
    """Evaluate the health of one camera.

    Returns (level, status text, banner text or None). Texts are localized.
    """
    last_ok = st['last_ok']
    last_change = st['last_change']
    err = st['last_error']
    inflight = st['inflight_since']
    ref = last_change if last_change is not None else st['started']
    age = now - ref
    age_txt = fmt_age(age)

    # 1) the last request failed
    if err is not None and (last_ok is None or err[0] >= last_ok):
        exc = err[1]
        title = exc.title if isinstance(exc, FrigateError) else messages.tr('title_error')
        detail = str(exc) or title
        if getattr(exc, 'definitive', False) or last_change is None or age >= freeze_s:
            return (LEVEL_ERROR, '%s · %s' % (detail, age_txt), '%s · %s' % (title.upper(), age_txt))
        return (LEVEL_WARN, '%s · %s' % (title, age_txt), None)

    hanging = inflight is not None and now - inflight >= NO_RESPONSE_AFTER

    # 2) no picture received yet
    if last_change is None:
        if age >= freeze_s:
            if hanging:
                return (LEVEL_ERROR, messages.tr('st_no_response', age=age_txt),
                        messages.tr('bn_no_response', age=age_txt))
            return (LEVEL_ERROR, messages.tr('st_no_picture', age=age_txt),
                    messages.tr('bn_no_picture', age=age_txt))
        return (LEVEL_CONNECTING, messages.tr('st_connecting'), None)

    # 3) picture too old -> stalled
    if age >= freeze_s:
        if hanging:
            return (LEVEL_ERROR, messages.tr('st_no_response', age=age_txt),
                    messages.tr('bn_no_response', age=age_txt))
        if cam_fps is not None and cam_fps <= 0:
            return (LEVEL_ERROR, messages.tr('st_no_signal', age=age_txt),
                    messages.tr('bn_no_signal', age=age_txt))
        return (LEVEL_ERROR, messages.tr('st_frozen', age=age_txt), messages.tr('bn_frozen', age=age_txt))

    if age >= warn_s:
        return (LEVEL_WARN, messages.tr('st_delayed', age=age_txt), None)

    fps = st.get('fps') or 0.0
    if fps <= 0:
        return (LEVEL_OK, messages.tr('st_live'), None)
    return (LEVEL_OK, messages.tr('st_live_fps', fps=messages.decimal(fps)), None)
