"""Frigate API client: TLS pinning, login handling, errors and server detection."""
import socket
import threading

import pytest

import fake_frigate
from resources.lib import frigate as fr


def make_client(server, **overrides):
    args = dict(host='127.0.0.1', port=server.port, https=True, username=fake_frigate.USER,
                password=fake_frigate.PASSWORD, tls_mode=fr.TLS_PIN, fingerprint=server.fingerprint, timeout=5.0)
    args.update(overrides)
    return fr.FrigateClient(**args)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------- certificates
def test_fetch_fingerprint_matches_openssl(frigate):
    assert fr.fetch_fingerprint('127.0.0.1', frigate.port) == frigate.fingerprint


def test_fingerprint_formatting_round_trip(frigate):
    formatted = fr.format_fp(frigate.fingerprint)
    assert formatted.count(':') == 31 and formatted == formatted.upper()
    assert fr.normalize_fp(formatted) == frigate.fingerprint


def test_unconfirmed_certificate_is_rejected_before_credentials_are_sent(frigate, frigate_state):
    client = make_client(frigate, fingerprint='')
    with pytest.raises(fr.CertUntrusted) as info:
        client.config()
    assert info.value.fingerprint == frigate.fingerprint
    assert frigate_state.counts['login'] == 0


def test_changed_certificate_is_rejected_before_credentials_are_sent(frigate, frigate_state):
    client = make_client(frigate, fingerprint='AA' * 32)
    with pytest.raises(fr.CertError) as info:
        client.config()
    assert 'changed' in info.value.log_text
    assert frigate_state.counts['login'] == 0


def test_pinned_fingerprint_may_contain_colons(frigate):
    client = make_client(frigate, fingerprint=fr.format_fp(frigate.fingerprint))
    assert sorted(fr.camera_meta(client.config())) == ['attic', 'front_door', 'garden']


def test_system_ca_mode_rejects_self_signed_certificate(frigate):
    with pytest.raises(fr.CertError):
        make_client(frigate, tls_mode=fr.TLS_SYSTEM, fingerprint='').config()


def test_no_verification_only_when_explicitly_configured(frigate):
    assert make_client(frigate, tls_mode=fr.TLS_NONE, fingerprint='').snapshot('garden')[:2] == b'\xff\xd8'


# ---------------------------------------------------------------- login
def test_keep_alive_and_token_reuse(frigate, frigate_state):
    client = make_client(frigate)
    for _ in range(6):
        client.snapshot('front_door', height=240)
    assert frigate_state.counts['login'] == 1


def test_expired_token_triggers_one_relogin(frigate, frigate_state):
    client = make_client(frigate)
    client.snapshot('garden')
    frigate_state.tokens.clear()
    assert client.snapshot('garden')[:2] == b'\xff\xd8'
    assert frigate_state.counts['login'] == 2


def test_parallel_threads_relogin_only_once(frigate, frigate_state):
    client = make_client(frigate)
    client.snapshot('garden')
    frigate_state.tokens.clear()
    errors = []

    def burst():
        try:
            for _ in range(8):
                client.snapshot('garden', height=200)
        except Exception as exc:
            errors.append(exc)
        finally:
            client.drop_connection()

    threads = [threading.Thread(target=burst) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert frigate_state.counts['login'] == 2


def test_wrong_password_is_tried_only_once_then_paused(frigate, frigate_state):
    client = make_client(frigate, password='wrong')
    for _ in range(3):
        with pytest.raises(fr.AuthError) as info:
            client.config()
    assert frigate_state.counts['login'] == 1
    assert 'HTTP 401' in info.value.log_text and 'Login failed' in info.value.log_text


def test_forced_login_ignores_the_pause(frigate, frigate_state):
    client = make_client(frigate, password='wrong')
    with pytest.raises(fr.AuthError):
        client.login()
    with pytest.raises(fr.AuthError):
        client.login()
    assert frigate_state.counts['login'] == 2


def test_missing_user_name_on_authenticated_port(frigate):
    with pytest.raises(fr.AuthError) as info:
        make_client(frigate, username='', password='').config()
    assert info.value.key == 'err_login_required'


def test_open_port_without_login(frigate_open):
    client = fr.FrigateClient('127.0.0.1', frigate_open.port, https=False)
    assert client.version() == '0.18.2-test'


# ---------------------------------------------------------------- errors
def test_unknown_camera(frigate):
    with pytest.raises(fr.CameraNotFound) as info:
        make_client(frigate).snapshot('garage')
    assert info.value.status == 404 and info.value.definitive
    assert info.value.log_text == 'Camera "garage" not found'


def test_server_error_is_not_definitive(frigate, frigate_state):
    frigate_state.fail_status = 500
    with pytest.raises(fr.HTTPStatusError) as info:
        make_client(frigate).snapshot('garden')
    assert info.value.status == 500 and not info.value.definitive


def test_server_without_keep_alive(frigate, frigate_state):
    frigate_state.close_conn = True
    client = make_client(frigate)
    assert client.snapshot('garden')[:2] == b'\xff\xd8'
    assert client.snapshot('garden')[:2] == b'\xff\xd8'


def test_closed_port():
    port = free_port()
    with pytest.raises(fr.ConnError) as info:
        fr.FrigateClient('127.0.0.1', port, https=False).version()
    assert info.value.key == 'err_refused'


def test_timeout(frigate, frigate_state):
    frigate_state.delay = 1.5
    with pytest.raises(fr.TimeoutErr):
        make_client(frigate, timeout=0.5).snapshot('garden')


def test_errors_have_localized_text_and_title():
    error = fr.ConnError('err_refused', host='h', port=1)
    assert str(error) == 'Connection refused (h:1)'
    assert error.title == 'No connection'
    assert fr.describe_error(ValueError('x')) == 'ValueError: x'


# ---------------------------------------------------------------- camera metadata
def test_camera_meta_handles_odd_configs():
    meta = fr.camera_meta({'cameras': {
        'a': {'detect': {'width': 1280, 'height': 720}},
        'b': {'enabled': False, 'detect': {'width': 'x', 'height': 0}},
        'c': None,
        'd': {'detect': {'width': 100, 'height': 1000}},
    }})
    assert meta['a'] == {'enabled': True, 'aspect': 1280 / 720, 'height': 720, 'width': 1280}
    assert meta['b']['enabled'] is False and meta['b']['aspect'] == fr.DEFAULT_ASPECT
    assert meta['c']['aspect'] == fr.DEFAULT_ASPECT
    assert meta['d']['aspect'] == fr.DEFAULT_ASPECT          # implausible ratio
    assert fr.camera_meta({}) == {} and fr.camera_meta(None) == {}


# ---------------------------------------------------------------- setup helpers
@pytest.mark.parametrize('text, expected', [
    ('192.168.1.10', ('192.168.1.10', None, None)),
    (' frigate.local ', ('frigate.local', None, None)),
    ('https://frigate.local:8971/', ('frigate.local', 8971, True)),
    ('http://192.168.1.10:5000/api', ('192.168.1.10', 5000, False)),
    ('HTTPS://Frigate.Local', ('Frigate.Local', None, True)),
    ('user:pass@frigate.local:8971', ('frigate.local', 8971, None)),
    ('[fd00::10]:8971', ('fd00::10', 8971, None)),
    ('fd00::10', ('fd00::10', None, None)),
    ('', None),
    ('frigate.local:99999', None),
    ('frigate.local:abc', None),
    ('frig ate', None),
])
def test_parse_address(text, expected):
    assert fr.parse_address(text) == expected


def test_probe_candidates():
    assert fr.probe_candidates() == [(8971, True), (8971, False), (5000, False)]
    assert fr.probe_candidates(port=8971) == [(8971, True), (8971, False)]
    assert fr.probe_candidates(port=1234, https=False) == [(1234, False)]
    assert fr.probe_candidates(https=True) == [(8971, True)]


def test_probe_finds_authenticated_https_frigate(frigate, frigate_state):
    result = fr.probe('127.0.0.1', frigate.port, True)
    assert result.found and result.auth_required and result.fingerprint == frigate.fingerprint
    assert frigate_state.counts['login'] == 0            # never sends credentials


def test_probe_finds_open_frigate(frigate_open):
    result = fr.probe('127.0.0.1', frigate_open.port, False)
    assert result.found and not result.auth_required and result.version == '0.18.2-test'


def test_probe_plain_http_on_tls_port_is_not_found(frigate):
    result = fr.probe('127.0.0.1', frigate.port, False, timeout=2)
    assert not result.found and result.error is not None


def test_probe_closed_port():
    result = fr.probe('127.0.0.1', free_port(), True, timeout=2)
    assert not result.found and isinstance(result.error, fr.ConnError)
