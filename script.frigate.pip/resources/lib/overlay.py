# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Overlay window and session control.

Kodi always treats Python dialogs as modal: while the overlay is visible, all
keys arrive here. keys.decide() determines what happens with them.

All periodic GUI updates run in the session thread (OverlaySession). The main
thread of the service calls doModal() and receives the key presses.
"""
import threading
import time
from collections import deque

import xbmc
import xbmcgui

from . import keys
from .common import (
    POS_BOTTOM_RIGHT,
    POS_TOP_LEFT,
    POS_TOP_RIGHT,
    log,
    log_debug,
    log_exception,
    log_warning,
    media_path,
    notify,
    tr,
)
from .frigate import DEFAULT_ASPECT, describe_error
from .poller import (
    LEVEL_CONNECTING,
    LEVEL_ERROR,
    LEVEL_NAMES,
    LEVEL_OK,
    LEVEL_WARN,
    CameraPoller,
    StatsPoller,
    evaluate,
)
from .regions import zoom_label

FONT = 'font13'            # every skin has to provide it (Kodi skinning guidelines)
BORDER = 3
STATUS_H = 30

BORDER_COLORS = {
    LEVEL_CONNECTING: '0xFF757575',
    LEVEL_OK: '0xFF2E7D32',
    LEVEL_WARN: '0xFFF9A825',
    LEVEL_ERROR: '0xFFD32F2F',
}
TEXT_COLORS = {
    LEVEL_CONNECTING: '0xFFE0E0E0',
    LEVEL_OK: '0xFFFFFFFF',
    LEVEL_WARN: '0xFFFFE082',
    LEVEL_ERROR: '0xFFFF8A80',
}
WHITE = '0xFFFFFFFF'
IMAGE_BG = '0xFF101010'
STATUSBAR_BG = '0xB4000000'
BANNER_BG = '0xD8B71C1C'

ALIGN_LEFT = 0x00000000
ALIGN_RIGHT = 0x00000001
ALIGN_CENTER_X = 0x00000002
ALIGN_CENTER_Y = 0x00000004

UNDERLAY_LAG = 0.15        # s, the lower picture layer lags behind the upper one by this time
HINT_SECONDS = 5.0
HINT_BG = '0xC8000000'
GUARD_LABEL = 'VideoPlayer.ChannelName'


def compute_layout(aspects, position, size_pct, screen_w, screen_h):
    """Rectangles (x, y, w, h) for all cameras, stacked along the chosen edge."""
    count = len(aspects)
    margin = int(round(screen_h * 0.035))
    gap = int(round(screen_h * 0.015))
    slot_w = int(round(screen_w * size_pct / 100.0))

    def heights(width):
        inner = width - 2 * BORDER
        return [int(round(inner / max(0.3, a))) + 2 * BORDER for a in aspects]

    hs = heights(slot_w)
    total = sum(hs) + gap * (count - 1)
    avail = screen_h - 2 * margin
    if total > avail and total > 0:
        scale = float(avail - gap * (count - 1)) / float(sum(hs))
        slot_w = max(80, int(slot_w * scale))
        hs = heights(slot_w)
        total = sum(hs) + gap * (count - 1)

    right = position in (POS_TOP_RIGHT, POS_BOTTOM_RIGHT)
    top = position in (POS_TOP_RIGHT, POS_TOP_LEFT)
    x = screen_w - margin - slot_w if right else margin
    y = margin if top else max(margin, screen_h - margin - total)
    rects = []
    for h in hs:
        rects.append((x, y, slot_w, h))
        y += h + gap
    return rects


class OverlayWindow(xbmcgui.WindowDialog):
    def __init__(self):
        xbmcgui.WindowDialog.__init__(self)
        self.handler = None

    def onAction(self, action):
        handler = self.handler
        if handler is None:
            return
        try:
            handler(action.getId())
        except Exception:
            log_exception('Could not handle key press')


class Slot(object):
    """One camera picture with border, status bar and warning banner."""

    def __init__(self, camera, rect, white, title=None):
        x, y, w, h = rect
        self.camera = camera
        connecting = tr('st_connecting')
        ix, iy = x + BORDER, y + BORDER
        iw, ih = w - 2 * BORDER, h - 2 * BORDER
        sh = max(20, min(STATUS_H, ih // 5))
        pad = 8
        half = iw // 2

        self.border = xbmcgui.ControlImage(x, y, w, h, white, colorDiffuse=BORDER_COLORS[LEVEL_CONNECTING])
        self.background = xbmcgui.ControlImage(ix, iy, iw, ih, white, colorDiffuse=IMAGE_BG)
        # Two picture layers: the upper one gets every new frame immediately, the
        # lower one follows with a delay. While Kodi loads the new frame, the previous
        # one shines through instead of a short black flash (no flicker).
        self.img_under = xbmcgui.ControlImage(ix, iy, iw, ih, '', aspectRatio=2)
        self.img_over = xbmcgui.ControlImage(ix, iy, iw, ih, '', aspectRatio=2)
        self.bar = xbmcgui.ControlImage(ix, iy + ih - sh, iw, sh, white, colorDiffuse=STATUSBAR_BG)
        self.name = xbmcgui.ControlLabel(ix + pad, iy + ih - sh, max(10, half - pad), sh, title or camera,
                                         font=FONT, textColor=WHITE, alignment=ALIGN_LEFT | ALIGN_CENTER_Y)
        self.status = xbmcgui.ControlLabel(ix + half, iy + ih - sh, max(10, iw - half - pad), sh, connecting,
                                           font=FONT, textColor=TEXT_COLORS[LEVEL_CONNECTING],
                                           alignment=ALIGN_RIGHT | ALIGN_CENTER_Y)
        bh = max(30, min(46, ih // 4))
        by = iy + max(0, (ih - sh - bh) // 2)
        self.banner_bg = xbmcgui.ControlImage(ix, by, iw, bh, white, colorDiffuse=BANNER_BG)
        self.banner = xbmcgui.ControlLabel(ix, by, iw, bh, '', font=FONT, textColor=WHITE,
                                           alignment=ALIGN_CENTER_X | ALIGN_CENTER_Y)

        self._shown_seq = None
        self._under_path = None
        self._history = deque()
        self._level = LEVEL_CONNECTING
        self._status_text = connecting
        self._banner_text = None

    def controls(self):
        return [self.border, self.background, self.img_under, self.img_over, self.bar,
                self.name, self.status, self.banner_bg, self.banner]

    def after_add(self):
        self.banner_bg.setVisible(False)
        self.banner.setVisible(False)

    def render(self, st, health, now):
        # --- picture
        seq, path = st['seq'], st['path']
        if path and seq != self._shown_seq:
            self.img_over.setImage(path, False)
            self._shown_seq = seq
            self._history.append((now, path))
        target = None
        while self._history and now - self._history[0][0] >= UNDERLAY_LAG:
            target = self._history.popleft()[1]
        if target and target != self._under_path:
            self.img_under.setImage(target, False)
            self._under_path = target

        # --- status
        level, text, banner = health
        if level != self._level:
            self.border.setColorDiffuse(BORDER_COLORS[level])
        if level != self._level or text != self._status_text:
            self.status.setLabel(text, FONT, TEXT_COLORS[level])
            self._status_text = text
        self._level = level

        if banner != self._banner_text:
            if banner:
                self.banner.setLabel(banner)
                if not self._banner_text:
                    self.banner_bg.setVisible(True)
                    self.banner.setVisible(True)
            else:
                self.banner_bg.setVisible(False)
                self.banner.setVisible(False)
            self._banner_text = banner



class View(object):
    """One visible overlay window. Rebuilt every time the PiP is (re)shown; the
    polling threads of the session simply keep running."""

    def __init__(self, session, hint=None):
        self.window = OverlayWindow()
        self.window.handler = session._on_action
        self.modal_done = threading.Event()
        white = media_path('white.png')
        self.slots = []
        controls = []
        for cam, rect, title in zip(session.cameras, session.rects, session.titles):
            slot = Slot(cam, rect, white, title)
            self.slots.append(slot)
            controls.extend(slot.controls())
        self.hint_bg = self.hint = None
        self.hint_until = 0.0
        if hint and session.rects:
            x, y, w, _h = session.rects[0]
            hx, hy, hw, hh = x + BORDER, y + BORDER, w - 2 * BORDER, STATUS_H
            self.hint_bg = xbmcgui.ControlImage(hx, hy, hw, hh, white, colorDiffuse=HINT_BG)
            self.hint = xbmcgui.ControlLabel(hx, hy, hw, hh, hint, font=FONT, textColor=WHITE,
                                             alignment=ALIGN_CENTER_X | ALIGN_CENTER_Y)
            controls.extend([self.hint_bg, self.hint])
            self.hint_until = time.monotonic() + HINT_SECONDS
        self.window.addControls(controls)
        for slot in self.slots:
            slot.after_add()

    def tick_hint(self, now):
        if self.hint is not None and now >= self.hint_until:
            self.hint_bg.setVisible(False)
            self.hint.setVisible(False)
            self.hint = None


class OverlaySession(threading.Thread):
    """One display session: polling threads + update loop + the currently visible window.

    States:  visible (view set)  ->  hidden (a key went to Kodi)
             ->  visible again after a quiet period  ...  ->  finished
    """

    TICK = 0.05

    def __init__(self, client, cameras, meta, settings, duration, frame_dir, monitor, regions=None):
        threading.Thread.__init__(self, name='frigatepip-overlay')
        self.daemon = True
        self.client = client
        self.cameras = list(cameras)
        self.monitor = monitor
        self.warn_s = settings.warn_ms / 1000.0
        self.freeze_s = settings.freeze_ms / 1000.0
        self.notify_errors = settings.notify_errors
        self.switch_key = settings.switch_key
        self.reshow_s = settings.reshow_s
        self.show_hint = settings.show_hint
        self.regions = dict(regions or {})
        # Result of a key press for the service: ('switch', camera) or ('redispatch', action, guard)
        self._outcome = None

        self._lock = threading.Lock()
        self._close_evt = threading.Event()
        self.alive = True
        now = time.monotonic()
        self._expire_at = now + duration if duration > 0 else None
        self._levels = {}
        self._last_notify = {}
        self.view = None
        self.views_shown = 0
        self._hidden_since = None
        self._force_min_idle = None   # force reshow after this idle time (s)

        probe = OverlayWindow()
        try:
            coord_w = int(probe.getWidth()) or 1280
            coord_h = int(probe.getHeight()) or 720
        except Exception:
            coord_w, coord_h = 1280, 720
        del probe
        try:
            screen_w_px = int(xbmc.getInfoLabel('System.ScreenWidth') or 0) or 1920
            screen_h_px = int(xbmc.getInfoLabel('System.ScreenHeight') or 0) or 1080
        except ValueError:
            screen_w_px, screen_h_px = 1920, 1080

        aspects = [meta.get(c, {}).get('aspect', DEFAULT_ASPECT) for c in self.cameras]
        self.rects = compute_layout(aspects, settings.position, settings.size_pct, coord_w, coord_h)
        self.titles = []
        self.pollers = []
        for cam, rect in zip(self.cameras, self.rects):
            region = self.regions.get(cam)
            if region is not None and region.is_full:
                region = None
            self.titles.append(cam if region is None else '%s · %s' % (cam, zoom_label(region.zoom)))
            inner_w = rect[2] - 2 * BORDER
            inner_h = rect[3] - 2 * BORDER
            px_w = int(inner_w * screen_w_px / float(coord_w))
            px_h = int(inner_h * screen_h_px / float(coord_h))
            cam_h = meta.get(cam, {}).get('height')
            transform = None
            if region is None:
                req_h = min(px_h, int(cam_h)) if cam_h else px_h
            else:
                # request the source resolution the section needs (at most the detect resolution)
                req_h = int(px_h * region.zoom)
                if cam_h:
                    req_h = min(req_h, int(cam_h))
                transform = self._make_cropper(cam, region, px_w, px_h)
            req_h = max(120, req_h - (req_h % 2))
            self.pollers.append(CameraPoller(
                client, cam, frame_dir, height=req_h, interval=settings.interval_ms / 1000.0,
                bbox=settings.bbox, timestamp=settings.timestamp, logger=log, transform=transform))

        self.stats = StatsPoller(client, logger=log) if settings.use_stats else None
        log('Overlay for %s (duration %s, interval %d ms, reshow: %s)' % (
            ', '.join('%s%s' % (c, '' if self.regions.get(c) is None else ' zoom %.1fx' % self.regions[c].zoom)
                      for c in self.cameras),
            ('%d s' % duration) if duration > 0 else 'unlimited', settings.interval_ms,
            ('after %d s idle' % self.reshow_s) if self.reshow_s > 0 else 'off'))

    @staticmethod
    def _make_cropper(camera, region, px_w, px_h):
        try:
            from .regions import Cropper
            return Cropper(region, px_w, px_h)
        except Exception as exc:
            log_warning('%s: image section not possible (%s) - showing the whole picture'
                        % (camera, describe_error(exc)))
            notify(tr('zoom_failed', reason=str(exc)), xbmcgui.NOTIFICATION_WARNING)
            return None

    # ------------------------------------------------------------ state
    @property
    def closing(self):
        return self._close_evt.is_set()

    @property
    def visible(self):
        return self.view is not None

    @property
    def hidden(self):
        return self.alive and not self.closing and self.view is None and self._hidden_since is not None

    def same_cameras(self, cameras):
        return set(cameras) == set(self.cameras)

    def take_outcome(self):
        outcome, self._outcome = self._outcome, None
        return outcome

    # ------------------------------------------------------------ control (main thread)
    def make_view(self, fullscreen=False):
        """Build a new window. None if the session has already ended."""
        with self._lock:
            if self._close_evt.is_set():
                return None
            hint = None
            if self.show_hint and self.views_shown == 0:
                if fullscreen and self.switch_key == keys.SWITCH_OK_FULLSCREEN:
                    hint = tr('hint_fullscreen')
                else:
                    hint = tr('hint_menu')
            view = View(self, hint)
            self.view = view
            self.views_shown += 1
            self._hidden_since = None
            self._force_min_idle = None
        if self.views_shown > 1:
            log_debug('Overlay shown again')
        return view

    def view_closed(self, view):
        view.modal_done.set()
        with self._lock:
            if self.view is view:
                self.view = None
                if not self._close_evt.is_set():
                    self._hidden_since = time.monotonic()

    def reshow_due(self, idle_s, modal_dialog_open):
        """Should the hidden PiP appear again now?"""
        with self._lock:
            if self._close_evt.is_set() or self.view is not None or self._hidden_since is None:
                return False
            if modal_dialog_open:
                return False
            if self._force_min_idle is not None:
                return idle_s >= self._force_min_idle
            return self.reshow_s > 0 and idle_s >= self.reshow_s

    def show_soon(self, min_idle=1.0):
        """Show the hidden PiP again soon (e.g. new alarm from Home Assistant)."""
        with self._lock:
            self._force_min_idle = min_idle

    def request_close(self):
        self._close_evt.set()

    def request_switch(self, camera=None):
        """End the overlay and show the camera in the Kodi player (full screen)."""
        self._outcome = ('switch', camera or self.cameras[0])
        self.request_close()

    def extend(self, duration):
        with self._lock:
            if duration is None:
                return
            if duration <= 0:
                self._expire_at = None
            elif self._expire_at is not None:
                self._expire_at = max(self._expire_at, time.monotonic() + duration)

    # ------------------------------------------------------------ keys (main thread)
    def _on_action(self, action_id):
        try:
            window_id = xbmcgui.getCurrentWindowId()
        except Exception:
            window_id = 0
        fullscreen = window_id == keys.WINDOW_FULLSCREEN_VIDEO
        live = fullscreen and xbmc.getCondVisibility('Pvr.IsPlayingTv | Pvr.IsPlayingRadio')
        kind, arg = keys.decide(action_id, fullscreen, live, self.switch_key)
        log_debug('Key %d (window %d, live=%s) -> %s %s' % (action_id, window_id, live, kind, arg))

        view = self.view
        if kind == keys.CLOSE:
            self.request_close()
            self._close_view(view)
        elif kind == keys.SWITCH:
            self.request_switch()
            self._close_view(view)
        elif kind in (keys.HIDE, keys.HIDE_GUARDED):
            if arg:
                guard = None
                if kind == keys.HIDE_GUARDED:
                    # Remember the channel - if Kodi switches by itself, do not switch twice
                    guard = (GUARD_LABEL, xbmc.getInfoLabel(GUARD_LABEL))
                self._outcome = ('redispatch', arg, guard)
            if self.reshow_s <= 0:
                self.request_close()      # no reshow -> end the session
            self._close_view(view)
        elif kind == keys.BUILTIN:
            xbmc.executebuiltin(arg)

    @staticmethod
    def _close_view(view):
        if view is not None:
            try:
                view.window.close()
            except Exception:
                log_exception('Could not close window')

    # ------------------------------------------------------------ thread
    def run(self):
        try:
            for poller in self.pollers:
                poller.start()
            if self.stats is not None:
                self.stats.start()
            while not self._close_evt.is_set():
                if self.monitor.abortRequested():
                    break
                now = time.monotonic()
                with self._lock:
                    expire_at = self._expire_at
                if expire_at is not None and now >= expire_at:
                    break
                self._tick(now)
                self._close_evt.wait(self.TICK)
        except Exception:
            log_exception('Error in the overlay loop')
        finally:
            self._finish()

    def _tick(self, now):
        cam_fps = self.stats.camera_fps() if self.stats is not None else {}
        view = self.view
        for index, poller in enumerate(self.pollers):
            st = poller.state()
            health = evaluate(st, now, self.warn_s, self.freeze_s, cam_fps.get(poller.camera))
            if view is not None:
                view.slots[index].render(st, health, now)
            self._track(poller.camera, health, now)
        if view is not None:
            view.tick_hint(now)

    def _track(self, camera, health, now):
        level = health[0]
        previous = self._levels.get(camera)
        if level == previous:
            return
        self._levels[camera] = level
        if previous is not None or level != LEVEL_CONNECTING:
            log('%s: status %s -> %s (%s)' % (camera, LEVEL_NAMES.get(previous, '-'),
                                               LEVEL_NAMES[level], health[1]))
        if level == LEVEL_ERROR and self.notify_errors and now - self._last_notify.get(camera, -1e9) >= 30:
            self._last_notify[camera] = now
            notify(tr('camera_error_notification', camera=camera, status=health[1].split(' · ')[0]),
                   xbmcgui.NOTIFICATION_WARNING)

    def _finish(self):
        with self._lock:
            self._close_evt.set()
            view = self.view
        for poller in self.pollers:
            poller.stop()
        if self.stats is not None:
            self.stats.stop()

        # Close the visible window and wait until doModal() has returned in the main
        # thread (a close() before doModal() has started would otherwise be lost).
        if view is not None:
            deadline = time.monotonic() + 10.0
            while True:
                self._close_view(view)
                if view.modal_done.wait(0.5) or time.monotonic() > deadline:
                    break

        # Abort pending requests immediately, then collect the threads
        try:
            self.client.close_all()
        except Exception:
            pass
        for poller in self.pollers:
            poller.join(2.0)
            poller.cleanup()
        if self.stats is not None:
            self.stats.join(2.0)
        self.alive = False
        log('Overlay finished')
