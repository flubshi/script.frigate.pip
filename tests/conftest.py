"""Test setup: fake Kodi modules, fake Frigate servers and common fixtures."""
import os
import sys
import tempfile
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(HERE, os.pardir, 'script.frigate.pip')
sys.path.insert(0, os.path.join(HERE, 'fakes'))
sys.path.insert(0, os.path.abspath(ADDON_DIR))
sys.path.insert(0, HERE)

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402

import fake_frigate  # noqa: E402
import resources.lib.common  # noqa: E402,F401  (installs the Kodi translator like in Kodi)
from resources.lib.frigate import TLS_PIN, format_fp  # noqa: E402


def wait_for(predicate, timeout=3.0, step=0.02):
    """Poll predicate until it returns True or the timeout expires."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


@pytest.fixture(autouse=True)
def kodi():
    """Fresh fake Kodi state for every test."""
    xbmc.reset()
    xbmcgui.reset()
    xbmcaddon.reset()
    yield
    xbmc._abort.set()


@pytest.fixture(scope='session')
def frigate():
    """HTTPS Frigate with login (like port 8971)."""
    server = fake_frigate.FakeFrigate(tempfile.mkdtemp(prefix='frigatepip-cert-'), tls=True, auth_required=True)
    yield server
    server.stop()


@pytest.fixture(scope='session')
def frigate_open():
    """Plain HTTP Frigate without login (like port 5000)."""
    server = fake_frigate.FakeFrigate(None, tls=False, auth_required=False)
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def frigate_state():
    fake_frigate.STATE.reset()
    yield fake_frigate.STATE
    fake_frigate.STATE.reset()


@pytest.fixture
def configured(frigate):
    """Settings of a fully configured add-on pointing at the fake HTTPS Frigate."""
    xbmcaddon.SETTINGS.update({
        'host': '127.0.0.1', 'port': frigate.port, 'use_https': True,
        'username': fake_frigate.USER, 'password': fake_frigate.PASSWORD,
        'tls_mode': TLS_PIN, 'tls_fingerprint': format_fp(frigate.fingerprint),
        'position': 0, 'size_pct': 30, 'duration': 0, 'reshow_s': 4, 'show_hint': True,
        'switch_key': 0, 'bbox': False, 'timestamp': True, 'interval_ms': 150, 'default_camera': '',
        'warn_ms': 1000, 'freeze_ms': 2000, 'notify_errors': True, 'use_stats': True,
        'zoom_enabled': False, 'ask_region': True,
        'rtsp_host': '', 'rtsp_port': 8554, 'rtsp_user': 'rtsp-user', 'rtsp_password': 'p@ss:word',
        'rtsp_stream': '{camera}', 'resume_after': True,
    })
    return xbmcaddon.SETTINGS


class RunningService(object):
    """The background service running in its own 'Kodi main' thread."""

    def __init__(self):
        from resources.lib.service import PipService
        self.service = PipService()
        self.thread = threading.Thread(target=self.service.run, name='kodi-main', daemon=True)
        self.thread.start()

    def notify(self, message, data=None):
        import json
        self.service.monitor.onNotification('test', 'Other.frigatepip_' + message, json.dumps(data or {}))

    @property
    def session(self):
        return self.service.session

    def visible(self):
        sess = self.service.session
        return sess is not None and sess.view is not None and sess.view.window.shown

    def key(self, action_id):
        class _Action(object):
            def getId(self):
                return action_id
        self.service.session.view.window.onAction(_Action())

    def stop(self):
        xbmc._abort.set()
        self.thread.join(10)
        assert not self.thread.is_alive(), 'service did not stop'


@pytest.fixture
def service(configured):
    running = RunningService()
    assert wait_for(lambda: xbmcgui.Window(10000).getProperty('script.frigate.pip.running') == '1')
    yield running
    running.stop()
