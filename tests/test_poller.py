"""Picture polling and stall detection."""
import os
import tempfile

import pytest

import fake_frigate
from conftest import wait_for
from resources.lib import frigate as fr
from resources.lib import poller as pl

WARN, FREEZE = 0.6, 1.2


@pytest.fixture
def camera(frigate):
    client = fr.FrigateClient('127.0.0.1', frigate.port, https=True, username=fake_frigate.USER,
                              password=fake_frigate.PASSWORD, fingerprint=frigate.fingerprint)
    folder = tempfile.mkdtemp(prefix='frigatepip-frames-')
    poller = pl.CameraPoller(client, 'front_door', folder, height=240, interval=0.1)
    poller.start()
    yield poller, folder
    poller.stop()
    client.close_all()
    poller.join(6)
    poller.cleanup()


def health(poller, fps=None):
    import time
    return pl.evaluate(poller.state(), time.monotonic(), WARN, FREEZE, fps)


def test_normal_operation(camera):
    poller, folder = camera
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK)
    assert wait_for(lambda: poller.state()['fps'] > 2, 2)
    assert health(poller)[1].startswith('live')
    assert 0 < len(os.listdir(folder)) <= pl.KEEP_FRAMES


def test_frozen_picture_turns_yellow_then_red(camera, frigate_state):
    poller, _ = camera
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK)
    frigate_state.freeze = True
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_WARN, 2)
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_ERROR, 2)
    assert 'PICTURE FROZEN' in health(poller)[2]
    assert 'NO SIGNAL' in health(poller, fps=0.0)[2]
    frigate_state.freeze = False
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK, 2)


def test_hanging_request(camera, frigate_state):
    poller, _ = camera
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK)
    frigate_state.delay = 3.0
    assert wait_for(lambda: 'NO RESPONSE' in (health(poller)[2] or ''), 5)
    frigate_state.delay = 0.0
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK, 5)


def test_server_errors_recover(camera, frigate_state):
    poller, _ = camera
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK)
    frigate_state.fail_status = 500
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_WARN, 2)
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_ERROR, 3)
    assert 'SERVER ERROR' in health(poller)[2]
    frigate_state.fail_status = None
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK, 5)


def test_rejected_login_is_an_error_immediately(camera, frigate_state):
    poller, _ = camera
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_OK)
    frigate_state.tokens.clear()
    frigate_state.users = {}
    assert wait_for(lambda: health(poller)[0] == pl.LEVEL_ERROR, 3)
    assert 'LOGIN FAILED' in health(poller)[2]


def test_cleanup_removes_pictures(frigate):
    client = fr.FrigateClient('127.0.0.1', frigate.port, username=fake_frigate.USER,
                              password=fake_frigate.PASSWORD, fingerprint=frigate.fingerprint)
    folder = tempfile.mkdtemp()
    poller = pl.CameraPoller(client, 'garden', folder, interval=0.1)
    poller.start()
    assert wait_for(lambda: os.listdir(folder))
    poller.stop()
    poller.join(6)
    poller.cleanup()
    assert os.listdir(folder) == []


def test_transform_errors_are_reported(frigate):
    client = fr.FrigateClient('127.0.0.1', frigate.port, username=fake_frigate.USER,
                              password=fake_frigate.PASSWORD, fingerprint=frigate.fingerprint)

    def broken(_data):
        raise ValueError('cannot decode')
    poller = pl.CameraPoller(client, 'garden', tempfile.mkdtemp(), interval=0.1, transform=broken)
    poller.start()
    try:
        assert wait_for(lambda: poller.state()['last_error'] is not None)
        assert poller.state()['last_error'][1].key == 'err_crop'
    finally:
        poller.stop()
        poller.join(6)


def test_evaluate_without_any_picture():
    state = {'last_ok': None, 'last_change': None, 'last_error': None, 'inflight_since': None,
             'started': 0.0, 'fps': 0.0}
    assert pl.evaluate(state, 0.5, WARN, FREEZE)[0] == pl.LEVEL_CONNECTING
    level, _text, banner = pl.evaluate(state, 5.0, WARN, FREEZE)
    assert level == pl.LEVEL_ERROR and 'NO PICTURE' in banner


@pytest.mark.parametrize('seconds, text', [(3.44, '3.4 s'), (42, '42 s'), (300, '5 min'), (7300, '2 h')])
def test_fmt_age(seconds, text):
    assert pl.fmt_age(seconds) == text
