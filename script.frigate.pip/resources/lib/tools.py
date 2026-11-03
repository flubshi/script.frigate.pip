# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Actions from the settings dialog: trust the certificate, connection test."""
import socket
import time

import xbmcgui

from .common import (
    HEADING,
    Settings,
    autocorrect_chars,
    get_addon,
    log,
    log_exception,
    make_client,
    notify,
    tr,
    write_settings,
)
from .frigate import (
    TLS_NONE,
    TLS_PIN,
    TLS_SYSTEM,
    FrigateError,
    HTTPStatusError,
    camera_meta,
    fetch_fingerprint,
    format_fp,
    normalize_fp,
)

TLS_MODE_KEYS = {
    TLS_PIN: 'tls_mode_pin',
    TLS_SYSTEM: 'tls_mode_system',
    TLS_NONE: 'tls_mode_none',
}


def _reopen_settings():
    try:
        get_addon().openSettings()
    except Exception:
        pass


def trust_certificate():
    """Fetch the server certificate, show its fingerprint and store it after confirmation."""
    settings = Settings()
    dialog = xbmcgui.Dialog()
    try:
        if not settings.host:
            dialog.ok(HEADING, tr('enter_host_first'))
            return
        if not settings.https:
            dialog.ok(HEADING, tr('https_off_no_cert'))
            return
        try:
            fp = fetch_fingerprint(settings.host, settings.port)
        except FrigateError as exc:
            dialog.ok(HEADING, tr('connection_failed', host=settings.host, port=settings.port, error=str(exc)))
            return
        current = normalize_fp(settings.fingerprint)
        if current == fp and settings.tls_mode == TLS_PIN:
            dialog.ok(HEADING, tr('cert_already_trusted', fingerprint=format_fp(fp)))
            return
        if current and current != fp:
            text = tr('cert_changed_text', fingerprint=format_fp(fp))
        else:
            text = tr('cert_trust_text', host=settings.host, port=settings.port, fingerprint=format_fp(fp))
        if dialog.yesno(HEADING, text):
            write_settings({'tls_fingerprint': format_fp(fp), 'tls_mode': TLS_PIN})
            log('Certificate %s stored' % format_fp(fp))
            notify(tr('cert_saved'))
    except Exception:
        log_exception('Trusting the certificate failed')
        dialog.ok(HEADING, tr('unexpected_error'))
    finally:
        _reopen_settings()


def connection_test():
    """Check TLS, login, camera list, pictures, statistics and RTSP and show a report."""
    settings = Settings()
    lines = []
    progress = xbmcgui.DialogProgress()
    progress.create(HEADING, tr('testing_connection'))
    try:
        run_test(settings, lines, progress)
    except Exception as exc:
        log_exception('Connection test failed')
        lines.append(tr('test_unexpected', error=str(exc)))
    finally:
        progress.close()
    log('Connection test:\n  %s' % '\n  '.join(lines))
    xbmcgui.Dialog().textviewer('%s – %s' % (HEADING, tr('connection_test_heading')), '\n'.join(lines))
    _reopen_settings()


def run_test(settings, lines, progress):
    """Fill lines with the report. Separate function so it can be tested."""
    if not settings.host:
        lines.append(tr('test_no_host'))
        return
    scheme = 'https' if settings.https else 'http'
    lines.append(tr('test_server', url='%s://%s:%d' % (scheme, settings.host, settings.port)))

    if settings.https:
        mode_key = TLS_MODE_KEYS.get(settings.tls_mode, 'tls_mode_pin')
        lines.append(tr('test_cert_mode', mode=tr(mode_key)))
        progress.update(10, 'TLS')
        try:
            fp = fetch_fingerprint(settings.host, settings.port)
        except FrigateError as exc:
            lines.append(tr('test_tls_failed', error=str(exc)))
            return
        lines.append(tr('test_fingerprint', fingerprint=format_fp(fp)))
        if settings.tls_mode == TLS_PIN:
            stored = normalize_fp(settings.fingerprint)
            if not stored:
                lines.append(tr('test_no_pin'))
                return
            if stored != fp:
                lines.append(tr('test_pin_mismatch'))
                lines.append(tr('test_pin_stored', fingerprint=format_fp(stored)))
                return
            lines.append(tr('test_pin_ok'))
        elif settings.tls_mode == TLS_NONE:
            lines.append(tr('test_no_verify'))
    else:
        lines.append(tr('test_plain_http'))

    for field_key, value in (('label_user', settings.username), ('label_password', settings.password)):
        found = autocorrect_chars(value)
        other = [c for c in value if ord(c) > 127 and c not in found]
        if found:
            lines.append(tr('test_autocorrect', field=tr(field_key), chars=' '.join(found)))
        elif other:
            lines.append(tr('test_non_ascii', field=tr(field_key), count=len(other)))

    client = make_client(settings)
    try:
        progress.update(25, 'Login')
        if settings.username:
            try:
                client.login()
                lines.append(tr('test_login_ok', user=settings.username))
            except FrigateError as exc:
                lines.append(tr('test_login_failed', error=str(exc)))
                return
        else:
            lines.append(tr('test_no_login'))

        progress.update(40, '/api/version')
        try:
            lines.append(tr('test_version', version=client.version()))
        except FrigateError as exc:
            lines.append(tr('test_version_failed', error=str(exc)))

        progress.update(55, '/api/config')
        try:
            meta = camera_meta(client.config())
        except FrigateError as exc:
            lines.append(tr('test_cameras_failed', error=str(exc)))
            meta = {}
        if meta:
            names = [name if m['enabled'] else tr('test_camera_disabled', camera=name)
                     for name, m in sorted(meta.items())]
            lines.append(tr('test_cameras', cameras=', '.join(names)))

        progress.update(70, 'latest.jpg')
        for name in sorted(meta)[:6]:
            start = time.monotonic()
            try:
                data = client.snapshot(name, height=360)
                lines.append(tr('test_snapshot_ok', camera=name, size=len(data) // 1024,
                                ms=int((time.monotonic() - start) * 1000)))
            except FrigateError as exc:
                lines.append(tr('test_snapshot_failed', camera=name, error=str(exc)))

        progress.update(85, '/api/stats')
        try:
            stats = client.stats()
            cams = stats.get('cameras', {}) if isinstance(stats, dict) else {}
            parts = []
            for name in sorted(cams):
                fps = cams[name].get('camera_fps') if isinstance(cams[name], dict) else None
                parts.append('%s %s fps' % (name, fps if fps is not None else '?'))
            lines.append(tr('test_stats', cameras=', '.join(parts) or tr('test_stats_no_data')))
        except HTTPStatusError as exc:
            if exc.status in (401, 403):
                lines.append(tr('test_stats_forbidden'))
            else:
                lines.append(tr('test_stats_failed', error=str(exc)))
        except FrigateError as exc:
            lines.append(tr('test_stats_failed', error=str(exc)))

        progress.update(95, 'RTSP')
        check_rtsp(settings, lines)
        if settings.zoom_enabled:
            try:
                from PIL import Image
                lines.append(tr('test_pillow_ok', version=getattr(Image, '__version__', '?')))
            except Exception:
                lines.append(tr('test_pillow_missing'))
        progress.update(100, '')
        lines.append('')
        lines.append(tr('test_done'))
    finally:
        client.close_all()


def check_rtsp(settings, lines):
    host = settings.rtsp_host or settings.host
    try:
        with socket.create_connection((host, settings.rtsp_port), timeout=3):
            pass
        lines.append(tr('test_rtsp_ok', host=host, port=settings.rtsp_port,
                        url=settings.rtsp_url('<camera>', with_credentials=False)))
        if not settings.rtsp_user:
            lines.append(tr('test_rtsp_no_user'))
    except OSError as exc:
        lines.append(tr('test_rtsp_failed', host=host, port=settings.rtsp_port,
                        error=getattr(exc, 'strerror', None) or str(exc)))
