"""A small fake Frigate API for tests.

- HTTPS server with a self-signed certificate and login (like port 8971)
- optional plain HTTP server without login (like port 5000)
- controllable failures: frozen picture, delays, HTTP errors, closed connections
"""
import hashlib
import io
import json
import secrets
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

USER = 'viewer'
PASSWORD = 'correct-horse-battery'

CAMERAS = {
    'front_door': {'enabled': True, 'detect': {'width': 1280, 'height': 720}},
    'garden': {'enabled': True, 'detect': {'width': 640, 'height': 480}},
    'attic': {'enabled': False, 'detect': {'width': 640, 'height': 360}},
}


class State(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.freeze = False          # always deliver the same picture
        self.delay = 0.0             # delay every GET request
        self.fail_status = None      # e.g. 500 for snapshots
        self.stats_status = 200
        self.camera_fps = 5.0
        self.close_conn = False      # refuse keep-alive
        self.users = {USER: PASSWORD}
        self.tokens = set()
        self.counts = {'login': 0, 'snapshot': 0, 'config': 0}
        self.frozen = {}


STATE = State()


def _jpeg(camera, height):
    tick = int(time.time() * 5)
    if STATE.freeze:
        if camera not in STATE.frozen:
            STATE.frozen[camera] = _render(camera, height, tick)
        return STATE.frozen[camera]
    STATE.frozen.pop(camera, None)
    return _render(camera, height, tick)


def _render(camera, height, tick):
    width = int(height * 16 / 9)
    img = Image.new('RGB', (width, height), (tick * 37 % 255, 60, 90))
    ImageDraw.Draw(img).text((10, 10), '%s %d' % (camera, tick), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=60)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _send(self, status, body=b'', ctype='application/json', cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if STATE.close_conn:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not self.server.auth_required:
            return True
        for part in self.headers.get('Cookie', '').split(';'):
            key, _sep, value = part.strip().partition('=')
            if key == 'frigate_token' and value in STATE.tokens:
                return True
        return False

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        if self.path == '/api/login':
            STATE.counts['login'] += 1
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            if STATE.users.get(data.get('user')) == data.get('password'):
                token = secrets.token_urlsafe(24)
                STATE.tokens.add(token)
                return self._send(200, b'{}', cookie='frigate_token=%s; HttpOnly; Path=/; Secure' % token)
            return self._send(401, b'{"message":"Login failed"}')
        self._send(404, b'{}')

    def do_GET(self):
        if STATE.delay:
            time.sleep(STATE.delay)
        if not self._authed():
            return self._send(401, b'{"message":"Unauthorized"}')
        path, _sep, query = self.path.partition('?')
        params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
        if path == '/api/config':
            STATE.counts['config'] += 1
            return self._send(200, json.dumps({'cameras': CAMERAS}).encode())
        if path == '/api/version':
            return self._send(200, b'0.18.2-test', 'text/plain')
        if path == '/api/stats':
            if STATE.stats_status != 200:
                return self._send(STATE.stats_status, b'{"message":"forbidden"}')
            stats = {'cameras': {name: {'camera_fps': STATE.camera_fps} for name in CAMERAS}}
            return self._send(200, json.dumps(stats).encode())
        parts = path.split('/')
        if len(parts) == 4 and parts[3] == 'latest.jpg':
            camera = parts[2]
            STATE.counts['snapshot'] += 1
            if camera not in CAMERAS:
                return self._send(404, b'{"success":false,"message":"Camera not found"}')
            if STATE.fail_status:
                return self._send(STATE.fail_status, b'{"message":"boom"}')
            return self._send(200, _jpeg(camera, int(params.get('h', 360))), 'image/jpeg')
        self._send(404, b'{}')


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # clients closing connections early is expected in these tests


class FakeFrigate(object):
    def __init__(self, workdir, tls=True, auth_required=True):
        self.server = _QuietServer(('127.0.0.1', 0), Handler)
        self.server.auth_required = auth_required
        self.port = self.server.server_address[1]
        self.fingerprint = ''
        if tls:
            cert, key = '%s/cert.pem' % workdir, '%s/key.pem' % workdir
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key,
                            '-out', cert, '-days', '2', '-subj', '/CN=frigate.test'],
                           check=True, capture_output=True)
            der = subprocess.run(['openssl', 'x509', '-in', cert, '-outform', 'DER'],
                                 check=True, capture_output=True).stdout
            self.fingerprint = hashlib.sha256(der).hexdigest()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
