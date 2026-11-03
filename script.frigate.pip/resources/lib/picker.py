# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Image section picker: shows the camera picture large, the frame is set with the remote.

Arrow keys   move the frame
OK           cycle the zoom (1x, 1.5x, 2x, 3x, 4x)
Back         apply and save
Menu/Esc     cancel
"""
import threading
import time
from collections import deque

import xbmcgui

from . import keys
from .common import log, log_exception, media_path, tr
from .frigate import DEFAULT_ASPECT
from .poller import CameraPoller
from .regions import Region, zoom_label

FONT = 'font13'
FRAME_COLOR = '0xFFFFD54F'
SHADE = '0xA6000000'
DIM = '0xF0000000'
LINE = 3
LAG = 0.2


class _PickerWindow(xbmcgui.WindowDialog):
    def __init__(self):
        xbmcgui.WindowDialog.__init__(self)
        self.handler = None

    def onAction(self, action):
        if self.handler is not None:
            try:
                self.handler(action.getId())
            except Exception:
                log_exception('Section picker: key handling failed')


class RegionPicker(object):
    """Full screen dialog to choose the image section of one camera."""

    def __init__(self, client, camera, meta, frame_dir, initial=None):
        self.camera = camera
        self.region = (initial or Region()).copy()
        self.result = None           # Region if applied, None if cancelled
        info = meta.get(camera, {}) if meta else {}
        self.aspect = info.get('aspect', DEFAULT_ASPECT)
        self.src_h = info.get('height')
        self.src_w = info.get('width') or (int(round(self.src_h * self.aspect)) if self.src_h else None)

        self.window = _PickerWindow()
        self.window.handler = self._on_action
        try:
            sw = int(self.window.getWidth()) or 1280
            sh = int(self.window.getHeight()) or 720
        except Exception:
            sw, sh = 1280, 720

        top, bottom = int(sh * 0.09), int(sh * 0.13)
        avail_w, avail_h = int(sw * 0.92), sh - top - bottom
        aw = avail_w
        ah = int(round(aw / self.aspect))
        if ah > avail_h:
            ah = avail_h
            aw = int(round(ah * self.aspect))
        self.area = ((sw - aw) // 2, top, aw, ah)
        ax, ay = self.area[0], self.area[1]
        white = media_path('white.png')

        self.dim = xbmcgui.ControlImage(0, 0, sw, sh, white, colorDiffuse=DIM)
        self.img_under = xbmcgui.ControlImage(ax, ay, aw, ah, '', aspectRatio=0)
        self.img_over = xbmcgui.ControlImage(ax, ay, aw, ah, '', aspectRatio=0)
        self.shades = [xbmcgui.ControlImage(ax, ay, aw, 1, white, colorDiffuse=SHADE) for _ in range(4)]
        self.lines = [xbmcgui.ControlImage(ax, ay, aw, LINE, white, colorDiffuse=FRAME_COLOR) for _ in range(4)]
        self.title = xbmcgui.ControlLabel(ax, 0, aw, top, tr('picker_title', camera=camera), font=FONT,
                                          textColor='0xFFFFFFFF', alignment=0x00000004)
        self.info = xbmcgui.ControlLabel(ax, 0, aw, top, '', font=FONT, textColor='0xFFFFD54F',
                                         alignment=0x00000001 | 0x00000004)
        self.help = xbmcgui.ControlLabel(
            ax, ay + ah, aw, bottom,
            tr('picker_help'),
            font=FONT, textColor='0xFFBDBDBD', alignment=0x00000002 | 0x00000004)
        self.window.addControls([self.dim, self.img_under, self.img_over] + self.shades + self.lines
                                + [self.title, self.info, self.help])

        self.poller = CameraPoller(client, camera, frame_dir, height=None, interval=0.4,
                                   timestamp=False, logger=log)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='frigatepip-picker')
        self._thread.daemon = True
        self._redraw()

    # ------------------------------------------------------------ display
    def _redraw(self):
        ax, ay, aw, ah = self.area
        x0, y0, x1, y1 = self.region.box(aw, ah)
        x0, y0, x1, y1 = ax + int(round(x0)), ay + int(round(y0)), ax + int(round(x1)), ay + int(round(y1))
        rects = [
            (ax, ay, aw, y0 - ay),                 # top
            (ax, y1, aw, ay + ah - y1),            # bottom
            (ax, y0, x0 - ax, y1 - y0),            # left
            (x1, y0, ax + aw - x1, y1 - y0),       # right
        ]
        for ctrl, (x, y, w, h) in zip(self.shades, rects):
            if w > 0 and h > 0:
                ctrl.setPosition(x, y)
                ctrl.setWidth(w)
                ctrl.setHeight(h)
                ctrl.setVisible(True)
            else:
                ctrl.setVisible(False)
        frame = [
            (x0, y0, x1 - x0, LINE),
            (x0, y1 - LINE, x1 - x0, LINE),
            (x0, y0, LINE, y1 - y0),
            (x1 - LINE, y0, LINE, y1 - y0),
        ]
        show_frame = not self.region.is_full
        for ctrl, (x, y, w, h) in zip(self.lines, frame):
            ctrl.setPosition(x, y)
            ctrl.setWidth(max(1, w))
            ctrl.setHeight(max(1, h))
            ctrl.setVisible(show_frame)
        if self.region.is_full:
            text = tr('picker_full')
        else:
            text = tr('picker_zoom', zoom=zoom_label(self.region.zoom))
            if self.src_w and self.src_h:
                text += '  ·  ' + tr('picker_source_pixels', width=int(self.src_w / self.region.zoom),
                                     height=int(self.src_h / self.region.zoom))
        self.info.setLabel(text)

    def _loop(self):
        shown_seq, under_path, history = None, None, deque()
        while not self._stop.wait(0.1):
            try:
                st = self.poller.state()
                now = time.monotonic()
                if st['path'] and st['seq'] != shown_seq:
                    self.img_over.setImage(st['path'], False)
                    shown_seq = st['seq']
                    history.append((now, st['path']))
                target = None
                while history and now - history[0][0] >= LAG:
                    target = history.popleft()[1]
                if target and target != under_path:
                    self.img_under.setImage(target, False)
                    under_path = target
                if st['last_error'] and (st['last_ok'] is None or st['last_error'][0] >= st['last_ok']):
                    self.title.setLabel(tr('picker_title_error', camera=self.camera, error=str(st['last_error'][1])))
            except Exception:
                log_exception('Section picker: display update failed')

    # ------------------------------------------------------------ keys (main thread)
    def _on_action(self, action_id):
        if action_id == keys.ACTION_MOVE_LEFT:
            self.region.move(-1, 0)
        elif action_id == keys.ACTION_MOVE_RIGHT:
            self.region.move(1, 0)
        elif action_id == keys.ACTION_MOVE_UP:
            self.region.move(0, -1)
        elif action_id == keys.ACTION_MOVE_DOWN:
            self.region.move(0, 1)
        elif action_id in (keys.ACTION_SELECT_ITEM, keys.ACTION_MOUSE_LEFT_CLICK):
            self.region.next_zoom()
        elif action_id == keys.ACTION_NAV_BACK:
            self.result = self.region.copy()
            self.window.close()
            return
        elif action_id in (keys.ACTION_PREVIOUS_MENU, keys.ACTION_CONTEXT_MENU, keys.ACTION_MOUSE_RIGHT_CLICK):
            self.result = None
            self.window.close()
            return
        else:
            return
        self._redraw()

    # ------------------------------------------------------------ flow
    def run(self):
        """Blocks (doModal) until applied or cancelled. Returns a Region or None."""
        self.poller.start()
        self._thread.start()
        try:
            self.window.doModal()
        finally:
            self._stop.set()
            self.poller.stop()
            self._thread.join(2.0)
            self.poller.join(6.0)
            self.poller.cleanup()
        return self.result
