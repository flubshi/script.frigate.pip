"""Image section (zoom)."""
import io
import os
import tempfile

from PIL import Image

from resources.lib.regions import Cropper, Region, RegionStore, pil_available, zoom_label


def test_region_is_clamped_to_the_picture():
    region = Region(2.0, 0.9, 0.1)
    assert (region.cx, region.cy) == (0.75, 0.25)
    assert Region(0.5).zoom == 1.0 and Region(20).zoom == 8.0


def test_zoom_cycle_and_move():
    region = Region()
    assert region.is_full
    for expected in (1.5, 2.0, 3.0, 4.0, 1.0):
        region.next_zoom()
        assert region.zoom == expected
    region = Region(2.0).move(10, 0)
    assert region.cx == 0.75


def test_box_and_serialisation():
    region = Region(2.0, 0.25, 0.75)
    assert region.box(640, 360) == (0.0, 180.0, 320.0, 360.0)
    assert Region.from_dict(region.to_dict()) == region
    assert Region.from_dict('nonsense') is None
    assert Region.from_dict({'zoom': 'x'}) is None


def test_zoom_label():
    assert zoom_label(2.0) == '2×' and zoom_label(1.5) == '1.5×'


def test_store_keeps_regions_per_camera():
    path = os.path.join(tempfile.mkdtemp(), 'sub', 'regions.json')
    store = RegionStore(path)
    assert store.get('garden') is None
    store.set('garden', Region(2.0, 0.6, 0.4))
    store.set('front_door', Region(3.0))
    assert RegionStore(path).get('garden') == Region(2.0, 0.6, 0.4)
    store.set('garden', Region())                 # whole picture removes the entry
    assert store.get('garden') is None and store.get('front_door') is not None


def test_store_survives_a_broken_file():
    path = os.path.join(tempfile.mkdtemp(), 'regions.json')
    with open(path, 'w') as handle:
        handle.write('{broken')
    assert RegionStore(path).get('garden') is None


def test_cropper_cuts_the_right_part():
    assert pil_available()
    source = Image.new('RGB', (640, 360), (0, 0, 0))
    source.paste((255, 0, 0), (480, 0, 640, 90))       # red area top right
    buf = io.BytesIO()
    source.save(buf, 'JPEG', quality=95)
    out = Image.open(io.BytesIO(Cropper(Region(4.0, 0.875, 0.125), 320, 180)(buf.getvalue())))
    pixel = out.getpixel((160, 90))
    assert out.size == (320, 180) and pixel[0] > 200 and pixel[1] < 60
