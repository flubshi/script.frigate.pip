# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Show a camera in the Kodi video player (full screen) and return afterwards.

Flow:
  1. Remember the current playback (file + position, or PVR channel)
  2. Play the camera's RTSP stream (go2rtc in Frigate)
  3. When the camera stream ends (or the swap key is pressed), resume the
     previous playback - unless the user has started something else meanwhile.

All methods run in the main thread of the service (player callbacks and tick()).
"""
import time

import xbmc
import xbmcgui

from .common import log, log_warning, notify, tr

START_TIMEOUT = 25.0     # s until the camera stream must be playing
STOP_CHECK = 1.5         # s after a stop, check whether something else is playing
RESUME_TIMEOUT = 30.0
MIN_RESUME_POS = 10.0    # s, below this position no seeking happens


class _Player(xbmc.Player):
    def __init__(self, controller):
        xbmc.Player.__init__(self)
        self._ctl = controller

    def onAVStarted(self):
        self._ctl.on_event('started')

    def onPlayBackStopped(self):
        self._ctl.on_event('stopped')

    def onPlayBackEnded(self):
        self._ctl.on_event('ended')

    def onPlayBackError(self):
        self._ctl.on_event('error')


class LiveStream(object):
    """State machine for camera full screen and resuming the previous playback."""

    def __init__(self):
        self.player = _Player(self)
        self.state = None

    @property
    def active(self):
        return self.state is not None

    @property
    def showing_camera(self):
        """Is a camera stream playing (or starting) right now?"""
        st = self.state
        if st is None or st['phase'] not in ('starting', 'playing'):
            return False
        return self._is_playing() or st['phase'] == 'starting'

    @property
    def camera(self):
        return self.state['camera'] if self.state else None

    def back(self):
        """Go back from the camera stream to the previous playback (swap key)."""
        st = self.state
        if st is None or st['phase'] not in ('starting', 'playing', 'ended'):
            return False
        if st.get('prev'):
            self._resume()
        else:
            log('Camera stream stopped (no previous playback)')
            self.state = None
            try:
                self.player.stop()
            except Exception:
                pass
        return True

    # ------------------------------------------------------------ Start
    def start(self, camera, url, safe_url, resume):
        prev = self._capture() if resume else None
        if self.state is not None and self.state.get('prev') and prev is None:
            prev = self.state['prev']        # camera -> other camera: keep the original playback
        title = tr('live_title', camera=camera)
        item = xbmcgui.ListItem(label=title, path=url)
        try:
            item.getVideoInfoTag().setTitle(title)
        except Exception:
            pass
        self.state = {'camera': camera, 'url': url, 'prev': prev, 'phase': 'starting',
                      't': time.monotonic(), 'check_at': None}
        log('Switching to camera stream %s (%s)%s' % (
            camera, safe_url, (', then back to "%s"' % prev['title']) if prev else ''))
        self.player.play(url, item)

    def _capture(self):
        try:
            if not self.player.isPlaying():
                return None
            path = self.player.getPlayingFile()
        except Exception:
            return None
        if not path:
            return None
        if self.state is not None and path == self.state.get('url'):
            return None
        live = path.startswith('pvr://channels/')
        position = 0.0
        if not live:
            try:
                position = float(self.player.getTime())
            except Exception:
                position = 0.0
        title = xbmc.getInfoLabel('Player.Title') or path.rsplit('/', 1)[-1]
        return {'path': path, 'time': position, 'live': live, 'title': title}

    # ------------------------------------------------------------ events
    def on_event(self, event):
        st = self.state
        if st is None:
            return
        now = time.monotonic()
        phase = st['phase']
        if phase == 'starting':
            if event == 'started':
                st['phase'] = 'playing'
                log('Camera stream %s is playing' % st['camera'])
            elif event == 'error':
                self._failed()
            else:
                # may be the stop of the previous playback -> check shortly
                st['check_at'] = now + STOP_CHECK
        elif phase == 'playing':
            if event in ('stopped', 'ended', 'error'):
                st['phase'] = 'ended'
                st['check_at'] = now + STOP_CHECK
        elif phase == 'resuming':
            if event == 'started':
                prev = st['prev']
                if not prev['live'] and prev['time'] >= MIN_RESUME_POS:
                    try:
                        self.player.seekTime(max(0.0, prev['time'] - 2.0))
                    except Exception:
                        log_warning('Seeking to %.0f s failed' % prev['time'])
                self.state = None
            elif event == 'error':
                self.state = None
            # 'stopped'/'ended' belong to the replaced camera stream here -> ignore

    def tick(self):
        st = self.state
        if st is None:
            return
        now = time.monotonic()
        phase = st['phase']
        if phase == 'starting':
            if st['check_at'] is not None and now >= st['check_at']:
                st['check_at'] = None
                if not self._is_playing():
                    self._failed()
                    return
            if now - st['t'] > START_TIMEOUT:
                self._failed()
        elif phase == 'ended':
            if now >= st['check_at']:
                if self._is_playing():
                    log('Something else is playing after the camera stream - not resuming')
                    self.state = None
                else:
                    self._resume()
        elif phase == 'resuming':
            if now - st['t'] > RESUME_TIMEOUT:
                self.state = None

    # ------------------------------------------------------------ internal
    def _is_playing(self):
        try:
            return self.player.isPlaying()
        except Exception:
            return False

    def _failed(self):
        st = self.state
        log_warning('Camera stream %s could not be started' % st['camera'])
        notify(tr('live_failed', camera=st['camera']), xbmcgui.NOTIFICATION_ERROR)
        if self._is_playing():
            self.state = None
        else:
            self._resume()

    def _resume(self):
        st = self.state
        prev = st.get('prev')
        if not prev:
            self.state = None
            return
        log('Resuming "%s"%s' % (prev['title'], '' if prev['live'] else ' at %.0f s' % prev['time']))
        st['phase'] = 'resuming'
        st['t'] = time.monotonic()
        item = xbmcgui.ListItem(label=prev['title'], path=prev['path'])
        try:
            self.player.play(prev['path'], item)
        except Exception:
            log_warning('Resuming %s failed' % prev['path'])
            self.state = None
