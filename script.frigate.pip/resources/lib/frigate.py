# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Small Frigate API client based on the Python standard library only.

- Login via /api/login, the token cookie is kept in memory only
- Automatic re-login on HTTP 401
- One keep-alive connection per thread
- TLS: certificate pinning (default), system CA verification or, if
  explicitly configured, no verification at all

This module does not import any Kodi module, so it can be tested on its own.
"""
import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
from urllib.parse import quote, urlencode

from . import messages

TLS_PIN = 0
TLS_SYSTEM = 1
TLS_NONE = 2

USER_AGENT = 'script.frigate.pip'
DEFAULT_ASPECT = 16.0 / 9.0
DEFAULT_HTTPS_PORT = 8971
DEFAULT_HTTP_PORT = 5000


# --------------------------------------------------------------------------
# Errors. Each error carries a message key, so it can be shown localized in
# the UI (str(exc)) and in English in the log (exc.log_text).
# --------------------------------------------------------------------------
class FrigateError(Exception):
    title_key = 'title_error'
    definitive = False   # True: retrying does not help without user action

    def __init__(self, key='err_generic', **params):
        self.key = key
        self.params = params
        Exception.__init__(self, messages.english(key, **params))

    def __str__(self):
        return messages.tr(self.key, **self.params)

    @property
    def log_text(self):
        return messages.english(self.key, **self.params)

    @property
    def title(self):
        return messages.tr(self.title_key)


class ConnError(FrigateError):
    title_key = 'title_connection'


class TimeoutErr(FrigateError):
    title_key = 'title_timeout'


class AuthError(FrigateError):
    title_key = 'title_login'
    definitive = True


class CertError(FrigateError):
    title_key = 'title_certificate'
    definitive = True


class CertUntrusted(CertError):
    title_key = 'title_cert_untrusted'

    def __init__(self, fingerprint):
        CertError.__init__(self, 'err_cert_untrusted')
        self.fingerprint = fingerprint


class HTTPStatusError(FrigateError):
    def __init__(self, status, detail=''):
        if detail:
            FrigateError.__init__(self, 'err_http_detail', status=status, detail=detail)
        else:
            FrigateError.__init__(self, 'err_http', status=status)
        self.status = status
        if status == 404:
            self.title_key = 'title_not_found'
            self.definitive = True
        elif status == 403:
            self.title_key = 'title_forbidden'
            self.definitive = True
        else:
            self.title_key = 'title_server'


class CameraNotFound(HTTPStatusError):
    def __init__(self, camera):
        HTTPStatusError.__init__(self, 404)
        self.key = 'err_camera_unknown'
        self.params = {'camera': camera}
        self.args = (messages.english(self.key, **self.params),)


def describe_error(exc):
    """English one-line description of any exception, for log files."""
    if isinstance(exc, FrigateError):
        return exc.log_text
    return '%s: %s' % (exc.__class__.__name__, exc)


# --------------------------------------------------------------------------
# Certificate fingerprints
# --------------------------------------------------------------------------
def normalize_fp(value):
    """Reduce a fingerprint to lower-case hex digits."""
    return ''.join(c for c in (value or '').lower() if c in '0123456789abcdef')


def format_fp(value):
    """Format a fingerprint as AA:BB:CC:..."""
    digits = normalize_fp(value).upper()
    return ':'.join(digits[i:i + 2] for i in range(0, len(digits), 2))


def _insecure_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def make_ssl_context(tls_mode):
    if tls_mode == TLS_SYSTEM:
        return ssl.create_default_context()
    # TLS_PIN checks the fingerprint itself after the handshake, TLS_NONE not at all
    return _insecure_context()


def _network_error(exc, host, port):
    """Translate a socket-level exception into a FrigateError."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TimeoutErr('err_timeout_connect', host=host, port=port)
    if isinstance(exc, ConnectionRefusedError):
        return ConnError('err_refused', host=host, port=port)
    if isinstance(exc, ssl.SSLError):
        return ConnError('err_tls_handshake', reason=exc.reason or exc.__class__.__name__, port=port)
    if isinstance(exc, socket.gaierror):
        return ConnError('err_network', reason=exc.strerror or str(exc))
    return ConnError('err_network', reason=getattr(exc, 'strerror', None) or str(exc) or exc.__class__.__name__)


def fetch_fingerprint(host, port, timeout=5.0):
    """Return the SHA-256 fingerprint (hex) of the server certificate without verifying it."""
    ctx = _insecure_context()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        raise _network_error(exc, host, port)
    if not der:
        raise CertError('err_no_certificate')
    return hashlib.sha256(der).hexdigest()


class _HTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that verifies the pinned fingerprint BEFORE any data is sent."""

    def __init__(self, host, port, timeout, context, pin, fingerprint):
        http.client.HTTPSConnection.__init__(self, host, port, timeout=timeout, context=context)
        self._pin = pin
        self._fp = normalize_fp(fingerprint)

    def connect(self):
        http.client.HTTPSConnection.connect(self)
        if not self._pin:
            return
        der = self.sock.getpeercert(binary_form=True)
        received = hashlib.sha256(der).hexdigest() if der else ''
        if not self._fp:
            self.close()
            raise CertUntrusted(received)
        if received != self._fp:
            self.close()
            raise CertError('err_cert_changed', fingerprint=format_fp(received))


def _error_text(data):
    """Short error description from a Frigate error response."""
    try:
        obj = json.loads(data.decode('utf-8'))
        if isinstance(obj, dict):
            for key in ('message', 'detail', 'error'):
                if obj.get(key):
                    return str(obj[key])[:120]
    except (ValueError, UnicodeDecodeError, AttributeError):
        pass
    try:
        return data.decode('utf-8', 'replace').strip()[:120]
    except (AttributeError, UnicodeDecodeError):
        return ''


def camera_meta(cfg):
    """Camera information from /api/config: {name: {'enabled', 'aspect', 'width', 'height'}}."""
    result = {}
    cams = cfg.get('cameras') if isinstance(cfg, dict) else None
    if not isinstance(cams, dict):
        return result
    for name, cam in cams.items():
        if not isinstance(cam, dict):
            cam = {}
        detect = cam.get('detect') if isinstance(cam.get('detect'), dict) else {}
        width, height = detect.get('width'), detect.get('height')
        aspect = DEFAULT_ASPECT
        try:
            if width and height:
                aspect = float(width) / float(height)
        except (TypeError, ValueError, ZeroDivisionError):
            aspect = DEFAULT_ASPECT
        if not 0.3 < aspect < 4.0:
            aspect = DEFAULT_ASPECT
        try:
            height = int(height) if height else None
            width = int(width) if width else None
        except (TypeError, ValueError):
            height = width = None
        result[str(name)] = {
            'enabled': cam.get('enabled', True) is not False,
            'aspect': aspect,
            'height': height,
            'width': width,
        }
    return result


class FrigateClient(object):
    """Thread-safe client. Each thread uses its own keep-alive connection."""

    LOGIN_BACKOFF = 30.0

    def __init__(self, host, port, https=True, username='', password='',
                 tls_mode=TLS_PIN, fingerprint='', timeout=5.0, logger=None):
        self.host = host
        self.port = int(port)
        self.https = bool(https)
        self.username = username or ''
        self.password = password or ''
        self.tls_mode = tls_mode
        self.fingerprint = fingerprint or ''
        self.timeout = float(timeout)
        self._log = logger or (lambda msg: None)
        self._ctx = make_ssl_context(tls_mode) if self.https else None

        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._conns = set()

        self._cookie_lock = threading.Lock()
        self._cookies = {}

        self._login_lock = threading.Lock()
        self._gen = 0
        self._auth_off = False
        self._login_block_until = 0.0
        self._login_error = None

    def describe(self):
        return '%s://%s:%d' % ('https' if self.https else 'http', self.host, self.port)

    # ---------------------------------------------------------------- connections
    def _new_conn(self):
        if self.https:
            return _HTTPSConnection(self.host, self.port, self.timeout, self._ctx,
                                    self.tls_mode == TLS_PIN, self.fingerprint)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _get_conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
            self._local.uses = 0
            with self._conns_lock:
                self._conns.add(conn)
        return conn

    def drop_connection(self):
        """Close the connection of the calling thread."""
        conn = getattr(self._local, 'conn', None)
        self._local.conn = None
        self._local.uses = 0
        if conn is not None:
            with self._conns_lock:
                self._conns.discard(conn)
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        """Close the connections of all threads (pending requests are aborted)."""
        with self._conns_lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- cookies
    def _cookie_header(self):
        with self._cookie_lock:
            return '; '.join('%s=%s' % item for item in self._cookies.items())

    def _has_cookies(self):
        with self._cookie_lock:
            return bool(self._cookies)

    def _store_cookies(self, resp):
        try:
            raw_list = resp.headers.get_all('Set-Cookie') or []
        except AttributeError:
            raw_list = []
        for raw in raw_list:
            first, _sep, attrs = raw.partition(';')
            name, sep, value = first.partition('=')
            name = name.strip()
            if not sep or not name:
                continue
            value = value.strip().strip('"')
            attrs_l = attrs.lower().replace(' ', '')
            expired = (not value) or 'max-age=0' in attrs_l
            with self._cookie_lock:
                if expired:
                    self._cookies.pop(name, None)
                else:
                    self._cookies[name] = value

    # ---------------------------------------------------------------- HTTP
    def _raw(self, method, path, body=None, headers=None):
        hdrs = {'User-Agent': USER_AGENT, 'Accept': '*/*'}
        if headers:
            hdrs.update(headers)
        cookie = self._cookie_header()
        if cookie:
            hdrs['Cookie'] = cookie
        for attempt in (0, 1):
            conn = self._get_conn()
            reused = getattr(self._local, 'uses', 0) > 0
            try:
                conn.request(method, path, body=body, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
            except CertError:
                self.drop_connection()
                raise
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest,
                    http.client.ResponseNotReady, http.client.BadStatusLine,
                    ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as exc:
                self.drop_connection()
                if reused and attempt == 0:
                    continue   # the server had already closed the keep-alive connection
                raise ConnError('err_connection_lost', reason=exc.__class__.__name__)
            except (socket.timeout, TimeoutError):
                self.drop_connection()
                raise TimeoutErr('err_no_response', seconds=int(round(self.timeout)))
            except ssl.SSLCertVerificationError as exc:
                self.drop_connection()
                raise CertError('err_cert_invalid', reason=getattr(exc, 'verify_message', None) or str(exc))
            except ssl.SSLError as exc:
                self.drop_connection()
                if reused and attempt == 0 and isinstance(exc, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
                    continue
                raise ConnError('err_tls', reason=exc.reason or exc.__class__.__name__)
            except ConnectionRefusedError:
                self.drop_connection()
                raise ConnError('err_refused', host=self.host, port=self.port)
            except OSError as exc:
                self.drop_connection()
                raise _network_error(exc, self.host, self.port)
            except http.client.HTTPException as exc:
                self.drop_connection()
                raise ConnError('err_protocol', reason=exc.__class__.__name__)
            self._local.uses = getattr(self._local, 'uses', 0) + 1
            self._store_cookies(resp)
            if resp.will_close:
                self.drop_connection()
            return resp.status, resp, data
        raise ConnError('err_connection_lost', reason='retry')

    # ---------------------------------------------------------------- login
    def _login_locked(self):
        if time.monotonic() < self._login_block_until and self._login_error is not None:
            key, params = self._login_error
            raise AuthError(key, **params)
        with self._cookie_lock:
            self._cookies.clear()
        body = json.dumps({'user': self.username, 'password': self.password}).encode('utf-8')
        status, _resp, data = self._raw('POST', '/api/login', body, {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-CSRF-TOKEN': '1',
        })
        if status == 200:
            self._gen += 1
            self._login_error = None
            if self._has_cookies():
                self._auth_off = False
                self._log('Logged in to %s as "%s"' % (self.describe(), self.username))
            else:
                # 200 without token: authentication is disabled in Frigate
                self._auth_off = True
                self._log('Login returned no token cookie - authentication seems to be disabled in Frigate')
            return
        detail = _error_text(data)
        if status in (400, 401, 403):
            key = 'err_login_rejected_detail' if detail else 'err_login_rejected'
        elif status == 429:
            key = 'err_login_rate_limited'
        else:
            key = 'err_login_failed_detail' if detail else 'err_login_failed'
        params = {'status': status}
        if detail and key.endswith('_detail'):
            params['detail'] = detail
        self._login_error = (key, params)
        self._login_block_until = time.monotonic() + self.LOGIN_BACKOFF
        error = AuthError(key, **params)
        self._log('Login failed: %s - next attempt in %d s at the earliest'
                  % (error.log_text, self.LOGIN_BACKOFF))
        raise error

    def login(self):
        """Force a fresh login (e.g. for the connection test)."""
        with self._login_lock:
            self._login_block_until = 0.0
            self._login_locked()

    def _ensure_login(self):
        with self._login_lock:
            if not self._auth_off and not self._has_cookies():
                self._login_locked()
            return self._gen

    def _relogin(self, gen):
        with self._login_lock:
            if self._gen == gen:   # otherwise another thread has already logged in again
                self._auth_off = False
                self._login_locked()

    def request(self, method, path, body=None, headers=None):
        use_auth = bool(self.username)
        gen = self._ensure_login() if use_auth else 0
        status, resp, data = self._raw(method, path, body, headers)
        if status == 401:
            if not use_auth:
                raise AuthError('err_login_required')
            self._relogin(gen)
            status, resp, data = self._raw(method, path, body, headers)
            if status == 401:
                raise AuthError('err_access_denied')
        return status, resp, data

    # ---------------------------------------------------------------- API
    def get_json(self, path):
        status, _resp, data = self.request('GET', path, headers={'Accept': 'application/json'})
        if status != 200:
            raise HTTPStatusError(status, _error_text(data))
        try:
            return json.loads(data.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise FrigateError('err_bad_json', path=path)

    def config(self):
        return self.get_json('/api/config')

    def stats(self):
        return self.get_json('/api/stats')

    def version(self):
        status, _resp, data = self.request('GET', '/api/version')
        if status != 200:
            raise HTTPStatusError(status, _error_text(data))
        return data.decode('utf-8', 'replace').strip().strip('"')

    def snapshot(self, camera, height=None, bbox=False, timestamp=False):
        query = {}
        if height:
            query['h'] = int(height)
        if bbox:
            query['bbox'] = 1
        if timestamp:
            query['timestamp'] = 1
        path = '/api/%s/latest.jpg' % quote(camera, safe='')
        if query:
            path += '?' + urlencode(query)
        status, _resp, data = self.request('GET', path, headers={'Accept': 'image/jpeg'})
        if status == 404:
            raise CameraNotFound(camera)
        if status != 200:
            raise HTTPStatusError(status, _error_text(data))
        if not data.startswith(b'\xff\xd8'):
            raise FrigateError('err_not_jpeg')
        return data


# --------------------------------------------------------------------------
# Server detection for the setup wizard. Never sends credentials.
# --------------------------------------------------------------------------
class ProbeResult(object):
    """What answered at host:port.

    found          True if a Frigate API answered
    auth_required  True if that API requires a login (port 8971)
    fingerprint    certificate fingerprint (HTTPS only)
    version        Frigate version if it could be read without login
    error          FrigateError if nothing usable answered
    """

    def __init__(self, host, port, https):
        self.host = host
        self.port = port
        self.https = https
        self.found = False
        self.auth_required = False
        self.fingerprint = ''
        self.version = ''
        self.error = None

    def __repr__(self):
        return 'ProbeResult(%s://%s:%d found=%s auth=%s error=%s)' % (
            'https' if self.https else 'http', self.host, self.port, self.found,
            self.auth_required, describe_error(self.error) if self.error else None)


def _looks_like_version(text):
    text = text.strip().strip('"')
    return 0 < len(text) <= 40 and text[:1].isdigit() and '.' in text and ' ' not in text


def probe(host, port, https, timeout=4.0):
    """Check whether a Frigate API answers at host:port (HTTPS or HTTP)."""
    result = ProbeResult(host, int(port), bool(https))
    try:
        if https:
            result.fingerprint = fetch_fingerprint(host, port, timeout)
        client = FrigateClient(host, port, https=https, tls_mode=TLS_NONE, timeout=timeout)
        try:
            status, _resp, data = client._raw('GET', '/api/version')
        finally:
            client.close_all()
    except FrigateError as exc:
        result.error = exc
        return result
    body = data.decode('utf-8', 'replace') if isinstance(data, bytes) else ''
    if status == 200 and _looks_like_version(body):
        result.found = True
        result.version = body.strip().strip('"')
    elif status == 401:
        result.found = True
        result.auth_required = True
    else:
        result.error = FrigateError('err_not_frigate', status=status)
    return result


def parse_address(text):
    """Split user input like 'https://host:8971/' into (host, port or None, https or None).

    Returns None if no host could be found.
    """
    text = (text or '').strip()
    https = None
    lower = text.lower()
    if lower.startswith('https://'):
        https, text = True, text[8:]
    elif lower.startswith('http://'):
        https, text = False, text[7:]
    text = text.split('/', 1)[0].split('?', 1)[0].strip()
    if '@' in text:
        text = text.rsplit('@', 1)[1]
    port = None
    if text.startswith('['):                        # [IPv6]:port
        end = text.find(']')
        if end < 0:
            return None
        host, rest = text[1:end], text[end + 1:]
        if rest.startswith(':'):
            port = rest[1:]
    elif text.count(':') == 1:
        host, port = text.split(':', 1)
    else:
        host = text                                 # host name, IPv4 or bare IPv6
    if port is not None:
        if not port.isdigit() or not 0 < int(port) < 65536:
            return None
        port = int(port)
    host = host.strip()
    if not host or any(c.isspace() for c in host):
        return None
    return host, port, https


def probe_candidates(port=None, https=None):
    """Order in which the wizard tries ports/schemes."""
    if port is not None and https is not None:
        return [(port, https)]
    if port is not None:
        return [(port, True), (port, False)]
    if https is True:
        return [(DEFAULT_HTTPS_PORT, True)]
    if https is False:
        return [(DEFAULT_HTTP_PORT, False), (DEFAULT_HTTPS_PORT, False)]
    # 8971 with TLS is Frigate's default; 8971 without TLS if TLS is disabled in Frigate
    return [(DEFAULT_HTTPS_PORT, True), (DEFAULT_HTTPS_PORT, False), (DEFAULT_HTTP_PORT, False)]
