"""Script entry point and the tools from the settings dialog."""
import pytest
import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import script, tools
from resources.lib.common import PROP_RUNNING, Settings


@pytest.mark.parametrize('args, expected', [
    ([], ('select', [], None, False)),
    (['camera=front_door'], ('show', ['front_door'], None, False)),
    (['camera=front_door', 'garden', 'duration=60'], ('show', ['front_door', 'garden'], 60, False)),
    (['camera=front_door+garden'], ('show', ['front_door', 'garden'], None, False)),
    (['?camera=garden'], ('show', ['garden'], None, False)),
    (['action=toggle'], ('toggle', [], None, False)),
    (['action=fullscreen', 'camera=garden'], ('fullscreen', ['garden'], None, False)),
    (['action=swap'], ('swap', [], None, False)),
    (['action=setup'], ('setup', [], None, False)),
    (['camera=garden', 'full=1'], ('show', ['garden'], None, True)),
    (['camera=garden', 'duration=abc'], ('show', ['garden'], None, False)),
])
def test_parse_args(args, expected):
    assert script.parse_args(args) == expected


def test_commands_are_forwarded_to_the_service():
    xbmcgui.Window(10000).setProperty(PROP_RUNNING, '1')
    script.main(['default.py', 'camera=front_door', 'garden', 'duration=45', 'full=true'])
    request = xbmc.JSONRPC[-1]
    assert request['method'] == 'JSONRPC.NotifyAll'
    assert request['params']['message'] == 'frigatepip_show'
    assert request['params']['data'] == {'camera': 'front_door+garden', 'duration': 45, 'full': True}


def test_hint_when_the_service_is_not_running():
    script.main(['default.py', 'camera=garden'])
    assert xbmc.JSONRPC == []
    assert any('Background service is not running' in n for n in xbmcgui.UI.notifications)


def test_unknown_action():
    xbmcgui.Window(10000).setProperty(PROP_RUNNING, '1')
    script.main(['default.py', 'action=explode'])
    assert any('Unknown action' in n for n in xbmcgui.UI.notifications)


class _Progress(object):
    def update(self, *args):
        pass


def test_connection_test_report(configured):
    xbmcaddon.SETTINGS['zoom_enabled'] = True
    lines = []
    tools.run_test(Settings(), lines, _Progress())
    report = '\n'.join(lines)
    for expected in ('[OK] Fingerprint matches', '[OK] Logged in as "viewer"', 'Frigate version: 0.18.2-test',
                     '[OK] Cameras: attic (disabled), front_door, garden', '[OK] Picture front_door',
                     '[OK] Statistics', '[OK] Zoom: Pillow', 'Test finished.'):
        assert expected in report, expected


def test_connection_test_warns_about_autocorrect_and_changed_certificate(configured):
    xbmcaddon.SETTINGS['password'] = 'correct-horse…'
    lines = []
    tools.run_test(Settings(), lines, _Progress())
    assert any('autocorrection' in line for line in lines)
    xbmcaddon.SETTINGS['tls_fingerprint'] = 'AB' * 32
    lines = []
    tools.run_test(Settings(), lines, _Progress())
    assert any('does NOT match' in line for line in lines)


def test_connection_test_without_server():
    lines = []
    tools.run_test(Settings(), lines, _Progress())
    assert lines == ['[ERR] No server configured – run the setup wizard first.']


def test_connection_test_in_german(configured):
    xbmcaddon.LANGUAGE[0] = 'de_de'
    lines = []
    tools.run_test(Settings(), lines, _Progress())
    assert any(line.startswith('[OK] Angemeldet als') for line in lines)


def test_trust_certificate_from_settings(configured, frigate):
    xbmcaddon.SETTINGS['tls_fingerprint'] = ''
    xbmcgui.UI.push(('yesno', True))
    tools.trust_certificate()
    assert xbmcaddon.SETTINGS['tls_fingerprint'].replace(':', '').lower() == frigate.fingerprint
    assert xbmcaddon.OPENED_SETTINGS                      # settings dialog is reopened


def test_trust_certificate_warns_when_it_changed(configured):
    xbmcaddon.SETTINGS['tls_fingerprint'] = 'AB' * 32
    xbmcgui.UI.push(('yesno', False))
    tools.trust_certificate()
    assert 'has CHANGED' in xbmcgui.UI.texts('yesno')[0]
    assert xbmcaddon.SETTINGS['tls_fingerprint'] == 'AB' * 32


def test_rtsp_url_masks_credentials(configured):
    settings = Settings()
    assert settings.rtsp_url('garden') == 'rtsp://rtsp-user:p%40ss%3Aword@127.0.0.1:8554/garden'
    assert settings.rtsp_url('garden', with_credentials=False) == 'rtsp://***@127.0.0.1:8554/garden'
    xbmcaddon.SETTINGS['rtsp_host'] = 'fd00::5'
    assert '[fd00::5]:8554' in Settings().rtsp_url('garden')
