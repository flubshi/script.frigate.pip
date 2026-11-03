# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Image section (zoom) per camera.

Pillow is imported only here and only when needed. If zoom is disabled in
the settings, no picture is ever decoded.

This module does not import any Kodi module, so it can be tested on its own.
"""
import io
import json
import os
import threading

from . import messages

ZOOM_LEVELS = (1.0, 1.5, 2.0, 3.0, 4.0)
MOVE_STEP = 0.10   # share of the section width per key press


def zoom_label(zoom):
    """'2×', '1.5×' (with the decimal separator of the current language)."""
    if abs(zoom - round(zoom)) < 0.05:
        return '%d×' % int(round(zoom))
    return messages.decimal(zoom) + '×'


class Region(object):
    """Section as centre point (0..1) and zoom factor; the aspect ratio is the camera's."""

    def __init__(self, zoom=1.0, cx=0.5, cy=0.5):
        self.zoom = float(zoom)
        self.cx = float(cx)
        self.cy = float(cy)
        self.clamp()

    def clamp(self):
        self.zoom = max(1.0, min(8.0, self.zoom))
        half = 0.5 / self.zoom
        self.cx = min(max(self.cx, half), 1.0 - half)
        self.cy = min(max(self.cy, half), 1.0 - half)
        return self

    @property
    def is_full(self):
        return self.zoom <= 1.0001

    def box(self, width, height):
        """(x0, y0, x1, y1) in pixels of a picture of size width x height."""
        cw, ch = width / self.zoom, height / self.zoom
        x0 = min(max(self.cx * width - cw / 2.0, 0.0), width - cw)
        y0 = min(max(self.cy * height - ch / 2.0, 0.0), height - ch)
        return (x0, y0, x0 + cw, y0 + ch)

    def move(self, dx, dy):
        self.cx += dx * MOVE_STEP / self.zoom
        self.cy += dy * MOVE_STEP / self.zoom
        return self.clamp()

    def next_zoom(self):
        bigger = [z for z in ZOOM_LEVELS if z > self.zoom + 0.001]
        self.zoom = bigger[0] if bigger else ZOOM_LEVELS[0]
        return self.clamp()

    def copy(self):
        return Region(self.zoom, self.cx, self.cy)

    def to_dict(self):
        return {'zoom': round(self.zoom, 3), 'cx': round(self.cx, 4), 'cy': round(self.cy, 4)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        try:
            return cls(data.get('zoom', 1.0), data.get('cx', 0.5), data.get('cy', 0.5))
        except (TypeError, ValueError):
            return None

    def __eq__(self, other):
        return isinstance(other, Region) and self.to_dict() == other.to_dict()

    def __repr__(self):
        return 'Region(%s)' % self.to_dict()


class RegionStore(object):
    """Saved sections, stored as a JSON file in the addon_data folder."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path) as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, camera):
        with self._lock:
            region = Region.from_dict(self._load().get(camera))
        return region if region is not None and not region.is_full else None

    def set(self, camera, region):
        with self._lock:
            data = self._load()
            if region is None or region.is_full:
                data.pop(camera, None)
            else:
                data[camera] = region.to_dict()
            folder = os.path.dirname(self.path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as handle:
                json.dump(data, handle, indent=1, sort_keys=True)
            os.replace(tmp, self.path)


def pil_available():
    """True if Pillow can be imported."""
    try:
        from PIL import Image
        return hasattr(Image, 'open')
    except Exception:
        return False


class Cropper(object):
    """Cuts the section out of a JPEG and scales it to the overlay size."""

    def __init__(self, region, out_w, out_h, quality=85):
        from PIL import Image
        self._image = Image
        resampling = getattr(Image, 'Resampling', Image)
        self._filter = resampling.BILINEAR
        self.region = region.copy()
        self.out_w = max(16, int(out_w))
        self.out_h = max(16, int(out_h))
        self.quality = quality

    def __call__(self, data):
        img = self._image.open(io.BytesIO(data))
        zoom = self.region.zoom
        # The JPEG decoder may decode at a reduced size as long as the section
        # still has at least the target size afterwards (much faster).
        try:
            img.draft('RGB', (int(self.out_w * zoom), int(self.out_h * zoom)))
        except Exception:
            pass
        dw, dh = img.size
        x0, y0, x1, y1 = self.region.box(dw, dh)
        box = (int(round(x0)), int(round(y0)), max(int(round(x0)) + 1, int(round(x1))),
               max(int(round(y0)) + 1, int(round(y1))))
        out = img.convert('RGB').crop(box).resize((self.out_w, self.out_h), self._filter)
        buf = io.BytesIO()
        out.save(buf, 'JPEG', quality=self.quality)
        return buf.getvalue()
