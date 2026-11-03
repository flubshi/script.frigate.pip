# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Setup wizard: from installation to the first camera picture.

Steps:
  1. Address of the Frigate server (port and HTTPS are detected automatically)
  2. Confirm the certificate (HTTPS)
  3. Login (if Frigate requires one)
  4. Default camera
  5. Optional: RTSP credentials for the camera full screen

Nothing is saved before the last step, so cancelling at any point leaves the
existing configuration untouched. Credentials are only ever sent after the
server certificate has been confirmed.
"""
import socket

import xbmcgui

from .common import Settings, autocorrect_chars, log, tr
from .frigate import (
    TLS_PIN,
    AuthError,
    FrigateClient,
    FrigateError,
    camera_meta,
    describe_error,
    format_fp,
    parse_address,
    probe,
    probe_candidates,
)

HA_PORT = 8123
DEFAULT_RTSP_PORT = 8554


def _url(host, port, https):
    shown = '[%s]' % host if ':' in host else host
    return '%s://%s:%d' % ('https' if https else 'http', shown, port)


class SetupWizard(object):
    """Runs the dialogs. run() returns a dict of settings to write, or None if cancelled."""

    def __init__(self, settings=None, dialog=None):
        self.settings = settings or Settings()
        self.dialog = dialog or xbmcgui.Dialog()
        self.values = {}
        self.cameras = []
        self.client = None

    # ------------------------------------------------------------------ helpers
    def _ok(self, key, **params):
        self.dialog.ok(tr('wiz_heading'), tr(key, **params))

    def _yesno(self, text, yes_key='wiz_next', no_key='wiz_cancel'):
        return bool(self.dialog.yesno(tr('wiz_heading'), text, nolabel=tr(no_key), yeslabel=tr(yes_key)))

    def _input(self, key, default='', hidden=False):
        kwargs = {'defaultt': default or '', 'type': xbmcgui.INPUT_ALPHANUM}
        if hidden:
            kwargs['option'] = xbmcgui.ALPHANUM_HIDE_INPUT
        return self.dialog.input(tr(key), **kwargs)

    # ------------------------------------------------------------------ flow
    def run(self):
        try:
            if not self._yesno(tr('wiz_intro')):
                return None
            found = self._find_server()
            if found is None:
                return None
            if found.https and not self._confirm_certificate(found):
                return None
            meta = self._login(found)
            if meta is None:
                return None
            self._choose_default_camera(meta)
            if not self._rtsp(found.host):
                return None
            log('Setup wizard finished: %s' % _url(found.host, found.port, found.https))
            return self.values
        finally:
            if self.client is not None:
                self.client.close_all()

    # ------------------------------------------------------------------ step 1: address
    def _default_address(self):
        s = self.settings
        if not s.host:
            return ''
        return _url(s.host, s.port, s.https)

    def _find_server(self):
        default = self._default_address()
        while True:
            text = self._input('wiz_enter_address', default)
            if not text:
                return None
            default = text
            parsed = parse_address(text)
            if parsed is None:
                self._ok('wiz_invalid_address', address=text)
                continue
            host, port, https = parsed
            if port == HA_PORT:
                self._ok('wiz_looks_like_ha')
                continue
            result, tried = self._probe_all(host, port, https)
            if result is None and tried is None:
                return None                       # cancelled in the progress dialog
            if result is not None:
                url = _url(result.host, result.port, result.https)
                log('Setup wizard: Frigate found at %s (login required: %s)' % (url, result.auth_required))
                if not result.https:
                    # No encryption: either no login at all (port 5000) or a password in plain text
                    key = 'wiz_found_plain' if result.auth_required else 'wiz_found_open'
                    if not self._yesno(tr(key, url=url), yes_key='wiz_yes', no_key='wiz_no'):
                        return None
                return result
            details = '[CR]'.join('%s: %s' % (url, error) for url, error in tried)
            if not self._yesno(tr('wiz_not_found', details=details), yes_key='wiz_retry'):
                return None

    def _probe_all(self, host, port, https):
        """Try all candidate ports. Returns (result, tried) or (None, None) if cancelled."""
        candidates = probe_candidates(port, https)
        progress = xbmcgui.DialogProgress()
        progress.create(tr('wiz_heading'), tr('wiz_searching'))
        tried = []
        try:
            for index, (cand_port, cand_https) in enumerate(candidates):
                if progress.iscanceled():
                    return None, None
                url = _url(host, cand_port, cand_https)
                progress.update(int(100 * index / len(candidates)), tr('wiz_trying', url=url))
                result = probe(host, cand_port, cand_https)
                if result.found:
                    return result, tried
                tried.append((url, str(result.error) if result.error else '?'))
                log('Setup wizard: nothing usable at %s (%s)'
                    % (url, describe_error(result.error) if result.error else '?'))
        finally:
            progress.close()
        return None, tried

    # ------------------------------------------------------------------ step 2: certificate
    def _confirm_certificate(self, found):
        text = tr('wiz_cert_text', host=found.host, port=found.port, fingerprint=format_fp(found.fingerprint))
        if not self._yesno(text, yes_key='wiz_yes', no_key='wiz_no'):
            return False
        self.values.update({'tls_mode': TLS_PIN, 'tls_fingerprint': format_fp(found.fingerprint)})
        return True

    # ------------------------------------------------------------------ step 3: login
    def _make_client(self, found, user='', password=''):
        if self.client is not None:
            self.client.close_all()
        self.client = FrigateClient(found.host, found.port, https=found.https, username=user,
                                    password=password, tls_mode=TLS_PIN,
                                    fingerprint=found.fingerprint, timeout=5.0, logger=log)
        return self.client

    def _login(self, found):
        """Returns the camera metadata ({} if none), or None if cancelled."""
        self.values.update({'host': found.host, 'port': found.port, 'use_https': found.https})
        if not found.auth_required:
            self.values.update({'username': '', 'password': ''})
            return self._load_cameras(self._make_client(found))

        same_server = (self.settings.host == found.host and self.settings.port == found.port)
        user = self.settings.username if same_server else ''
        password = self.settings.password if same_server else ''
        while True:
            user = self._input('wiz_enter_user', user)
            if not user:
                return None
            password = self._input('wiz_enter_password', password, hidden=True)
            if not password:
                return None
            suspicious = autocorrect_chars(password)
            if suspicious and not self._yesno(tr('wiz_autocorrect', chars=' '.join(suspicious)),
                                              yes_key='wiz_yes', no_key='wiz_no'):
                password = ''
                continue
            client = self._make_client(found, user, password)
            progress = xbmcgui.DialogProgress()
            progress.create(tr('wiz_heading'), tr('wiz_logging_in'))
            try:
                client.login()
                error = None
            except FrigateError as exc:
                error = exc
            finally:
                progress.close()
            if error is None:
                self.values.update({'username': user, 'password': password})
                return self._load_cameras(client)
            log('Setup wizard: login failed: %s' % describe_error(error))
            if isinstance(error, AuthError):
                self._ok('wiz_login_failed', error=str(error))
                continue
            if not self._yesno(tr('wiz_cameras_failed', error=str(error)), yes_key='wiz_retry'):
                return None

    def _load_cameras(self, client):
        while True:
            progress = xbmcgui.DialogProgress()
            progress.create(tr('wiz_heading'), tr('wiz_loading_cameras'))
            try:
                return camera_meta(client.config())
            except FrigateError as exc:
                error = exc
            finally:
                progress.close()
            log('Setup wizard: camera list failed: %s' % describe_error(error))
            if not self._yesno(tr('wiz_cameras_failed', error=str(error)), yes_key='wiz_retry'):
                return None

    # ------------------------------------------------------------------ step 4: default camera
    def _choose_default_camera(self, meta):
        names = sorted(name for name, info in meta.items() if info.get('enabled', True))
        self.cameras = names
        if not names:
            self._ok('wiz_no_cameras')
            self.values['default_camera'] = ''
            return
        items = [tr('wiz_no_default')] + names
        current = self.settings.default_cameras[0] if self.settings.default_cameras else ''
        preselect = names.index(current) + 1 if current in names else (1 if len(names) == 1 else 0)
        index = self.dialog.select(tr('wiz_default_camera'), items, preselect=preselect)
        self.values['default_camera'] = names[index - 1] if index > 0 else ''

    # ------------------------------------------------------------------ step 5: RTSP
    def _rtsp(self, host):
        """Returns False only if the wizard was cancelled."""
        if not self._yesno(tr('wiz_rtsp_offer'), yes_key='wiz_yes', no_key='wiz_no'):
            return True
        rtsp_host = self.settings.rtsp_host or host
        port = self.settings.rtsp_port or DEFAULT_RTSP_PORT
        try:
            with socket.create_connection((rtsp_host, port), timeout=3):
                pass
        except OSError as exc:
            self._ok('wiz_rtsp_unreachable', host=rtsp_host, port=port,
                     error=getattr(exc, 'strerror', None) or str(exc))
        user = self._input('wiz_rtsp_user', self.settings.rtsp_user)
        password = ''
        if user:
            password = self._input('wiz_rtsp_password', self.settings.rtsp_password, hidden=True)
        self.values.update({'rtsp_user': user or '', 'rtsp_password': password or ''})
        return True
