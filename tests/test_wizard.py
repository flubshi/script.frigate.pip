"""Setup wizard."""
import socket

import pytest
import xbmcaddon
import xbmcgui

import fake_frigate
from conftest import RunningService, wait_for
from resources.lib.common import Settings
from resources.lib.frigate import TLS_PIN, format_fp
from resources.lib.wizard import SetupWizard

UI = xbmcgui.UI


def closed_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run_wizard():
    return SetupWizard(Settings()).run()


def test_complete_setup_with_login(frigate, frigate_state):
    UI.push(('yesno', True),                                     # intro
            ('input', '127.0.0.1:%d' % frigate.port),             # address
            ('yesno', True),                                     # trust certificate
            ('input', fake_frigate.USER), ('input', fake_frigate.PASSWORD),
            ('select', 2),                                       # default camera: garden
            ('yesno', False))                                    # no RTSP now
    values = run_wizard()
    assert values == {
        'host': '127.0.0.1', 'port': frigate.port, 'use_https': True,
        'tls_mode': TLS_PIN, 'tls_fingerprint': format_fp(frigate.fingerprint),
        'username': fake_frigate.USER, 'password': fake_frigate.PASSWORD, 'default_camera': 'garden',
    }
    camera_dialog = [c for c in UI.calls if c[0] == 'select'][0]
    assert camera_dialog[2] == ['None – ask each time', 'front_door', 'garden']   # disabled camera hidden
    password_dialog = [c for c in UI.calls if c[0] == 'input'][2]
    assert password_dialog[3] == xbmcgui.ALPHANUM_HIDE_INPUT


def test_cancel_at_the_start_changes_nothing():
    UI.push(('yesno', False))
    assert run_wizard() is None


def test_empty_address_cancels():
    UI.push(('yesno', True), ('input', ''))
    assert run_wizard() is None


def test_invalid_address_and_home_assistant_port_are_explained():
    UI.push(('yesno', True), ('input', 'frig ate'), ('ok', True), ('input', 'homeassistant.local:8123'),
            ('ok', True), ('input', ''))
    assert run_wizard() is None
    oks = UI.texts('ok')
    assert 'not a valid address' in oks[0] and 'Home Assistant' in oks[1]


def test_nothing_found_offers_retry():
    port = closed_port()
    UI.push(('yesno', True), ('input', '127.0.0.1:%d' % port), ('yesno', False))
    assert run_wizard() is None
    assert 'No Frigate found' in UI.texts('yesno')[1]
    assert 'https://127.0.0.1:%d' % port in UI.texts('yesno')[1]


def test_declined_certificate_stops(frigate, frigate_state):
    UI.push(('yesno', True), ('input', '127.0.0.1:%d' % frigate.port), ('yesno', False))
    assert run_wizard() is None
    assert frigate_state.counts['login'] == 0


def test_wrong_password_can_be_corrected(frigate):
    UI.push(('yesno', True), ('input', 'https://127.0.0.1:%d' % frigate.port), ('yesno', True),
            ('input', fake_frigate.USER), ('input', 'wrong-password'),
            ('ok', True),                                        # "Login failed"
            ('input', fake_frigate.USER), ('input', fake_frigate.PASSWORD),
            ('select', 1), ('yesno', False))
    values = run_wizard()
    assert values['password'] == fake_frigate.PASSWORD and values['default_camera'] == 'front_door'
    assert 'Login rejected' in UI.texts('ok')[0]


def test_autocorrected_password_is_questioned(frigate):
    UI.push(('yesno', True), ('input', '127.0.0.1:%d' % frigate.port), ('yesno', True),
            ('input', fake_frigate.USER), ('input', 'correct-horse…'),
            ('yesno', False),                                    # "use as entered?" -> no
            ('input', fake_frigate.USER), ('input', fake_frigate.PASSWORD),
            ('select', 0), ('yesno', False))
    values = run_wizard()
    assert values['password'] == fake_frigate.PASSWORD and values['default_camera'] == ''
    assert '…' in UI.texts('yesno')[2]


def test_open_port_warns_and_needs_no_login(frigate_open):
    UI.push(('yesno', True), ('input', 'http://127.0.0.1:%d' % frigate_open.port),
            ('yesno', True),                                     # "no login - continue anyway?"
            ('select', 1), ('yesno', False))
    values = run_wizard()
    assert values['use_https'] is False and values['username'] == '' and 'tls_fingerprint' not in values
    assert 'no login' in UI.texts('yesno')[1]
    assert not [c for c in UI.calls if c[0] == 'input' and 'user name' in c[1]]


def test_rtsp_credentials(frigate):
    UI.push(('yesno', True), ('input', '127.0.0.1:%d' % frigate.port), ('yesno', True),
            ('input', fake_frigate.USER), ('input', fake_frigate.PASSWORD), ('select', 1),
            ('yesno', True),                                     # set up RTSP
            ('ok', True),                                        # port 8554 not reachable in the test
            ('input', 'rtsp-user'), ('input', 'rtsp-secret'))
    values = run_wizard()
    assert values['rtsp_user'] == 'rtsp-user' and values['rtsp_password'] == 'rtsp-secret'


def test_existing_configuration_is_offered_as_default(configured, frigate):
    UI.push(('yesno', True), ('input', ''))
    run_wizard()
    address_dialog = [c for c in UI.calls if c[0] == 'input'][0]
    assert address_dialog[2] == 'https://127.0.0.1:%d' % frigate.port


@pytest.fixture
def fresh_service():
    running = RunningService()
    assert wait_for(lambda: xbmcgui.Window(10000).getProperty('script.frigate.pip.running') == '1')
    yield running
    running.stop()


def test_setup_command_saves_and_shows_a_test_picture(fresh_service, frigate):
    UI.push(('yesno', True), ('input', '127.0.0.1:%d' % frigate.port), ('yesno', True),
            ('input', fake_frigate.USER), ('input', fake_frigate.PASSWORD), ('select', 2), ('yesno', False),
            ('yesno', True))                                     # show a test picture
    fresh_service.notify('setup')
    assert wait_for(fresh_service.visible, 6)
    assert fresh_service.session.cameras == ['garden']
    assert xbmcaddon.SETTINGS['host'] == '127.0.0.1' and xbmcaddon.SETTINGS['port'] == frigate.port
    assert xbmcaddon.SETTINGS['default_camera'] == 'garden'


def test_cancelled_setup_keeps_the_configuration(fresh_service):
    xbmcaddon.SETTINGS['host'] = 'old-host'
    UI.push(('yesno', True), ('input', ''))
    fresh_service.notify('setup')
    assert wait_for(lambda: any('nothing was changed' in n for n in UI.notifications), 4)
    assert xbmcaddon.SETTINGS['host'] == 'old-host'
