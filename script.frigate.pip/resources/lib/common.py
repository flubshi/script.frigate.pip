# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Shared helpers for the Kodi side: logging, settings, notifications, paths."""
import json
import os
import traceback
from urllib.parse import quote

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import messages
from .frigate import TLS_PIN, FrigateClient, normalize_fp
from .messages import tr

ADDON_ID = 'script.frigate.pip'
MSG_PREFIX = 'frigatepip_'
PROP_RUNNING = ADDON_ID + '.running'
HOME_WINDOW = 10000
HEADING = 'Frigate PiP'

# Characters that typically get into passwords through autocorrection (e.g. "..." -> "…")
AUTOCORRECT_CHARS = '…“”„‘’‚–—«»'

POS_TOP_RIGHT = 0
POS_TOP_LEFT = 1
POS_BOTTOM_RIGHT = 2
POS_BOTTOM_LEFT = 3

__all__ = ['tr']


def get_addon():
    return xbmcaddon.Addon(ADDON_ID)


def _install_translator():
    addon = get_addon()

    def translate(string_id):
        return addon.getLocalizedString(string_id)

    messages.set_translator(translate)


_install_translator()


# ------------------------------------------------------------------ logging
def log(msg, level=xbmc.LOGINFO):
    try:
        xbmc.log('[%s] %s' % (ADDON_ID, msg), level)
    except Exception:
        pass


def log_debug(msg):
    log(msg, xbmc.LOGDEBUG)


def log_warning(msg):
    log(msg, xbmc.LOGWARNING)


def log_error(msg):
    log(msg, xbmc.LOGERROR)


def log_exception(context):
    log_error('%s\n%s' % (context, traceback.format_exc()))


def notify(message, icon=None, ms=5000):
    try:
        xbmcgui.Dialog().notification(HEADING, message, icon or xbmcgui.NOTIFICATION_INFO, ms, False)
    except Exception:
        log_exception('Notification failed')


# ------------------------------------------------------------------ paths
def _translate_path(path):
    try:
        return xbmcvfs.translatePath(path)
    except Exception:
        return path


def addon_path():
    return _translate_path(get_addon().getAddonInfo('path'))


def media_path(name):
    return os.path.join(addon_path(), 'resources', 'media', name)


def profile_path(name=''):
    path = _translate_path(get_addon().getAddonInfo('profile'))
    return os.path.join(path, name) if name else path


# ------------------------------------------------------------------ small persistent state
def load_state():
    """Small JSON state file in addon_data (e.g. whether setup was offered)."""
    try:
        with open(profile_path('state.json')) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**values):
    data = load_state()
    data.update(values)
    path = profile_path('state.json')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w') as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        log_exception('Could not write state file')


# ------------------------------------------------------------------ parsing helpers
def split_cameras(value):
    """'front+garden', 'front,garden', ['front', ...] -> ['front', 'garden'] (no duplicates)."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(split_cameras(item))
    else:
        text = str(value)
        for sep in '+;,|':
            text = text.replace(sep, ' ')
        parts = [p.strip() for p in text.split() if p.strip()]
    seen, result = set(), []
    for part in parts:
        if part not in seen:
            seen.add(part)
            result.append(part)
    return result


def parse_duration(value):
    if value is None or value == '':
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def autocorrect_chars(text):
    """Characters in text that were probably inserted by autocorrection."""
    return sorted({c for c in (text or '') if c in AUTOCORRECT_CHARS})


# ------------------------------------------------------------------ settings
def _get_str(addon, key, default=''):
    try:
        value = addon.getSetting(key)
        return default if value is None else value
    except Exception:
        return default


def _get_int(addon, key, default):
    try:
        return int(addon.getSettingInt(key))
    except Exception:
        try:
            return int(addon.getSetting(key))
        except Exception:
            return default


def _get_bool(addon, key, default):
    try:
        return bool(addon.getSettingBool(key))
    except Exception:
        value = _get_str(addon, key, '').lower()
        if value in ('true', 'false'):
            return value == 'true'
        return default


def _clamp(value, low, high):
    return max(low, min(high, value))


def write_settings(values):
    """Write several settings at once. bool/int/str are stored with the matching type."""
    addon = get_addon()
    for key, value in values.items():
        if isinstance(value, bool):
            addon.setSettingBool(key, value)
        elif isinstance(value, int):
            addon.setSettingInt(key, value)
        else:
            addon.setSetting(key, value)


class Settings(object):
    """Snapshot of all settings."""

    def __init__(self, addon=None):
        a = addon or get_addon()
        self.host = _get_str(a, 'host').strip()
        self.port = _clamp(_get_int(a, 'port', 8971), 1, 65535)
        self.https = _get_bool(a, 'use_https', True)
        self.username = _get_str(a, 'username').strip()
        self.password = _get_str(a, 'password')
        self.tls_mode = _get_int(a, 'tls_mode', TLS_PIN)
        self.fingerprint = _get_str(a, 'tls_fingerprint').strip()

        self.position = _get_int(a, 'position', POS_TOP_RIGHT)
        self.size_pct = _clamp(_get_int(a, 'size_pct', 30), 10, 80)
        self.duration = max(0, _get_int(a, 'duration', 30))
        self.reshow_s = _clamp(_get_int(a, 'reshow_s', 4), 0, 120)
        self.show_hint = _get_bool(a, 'show_hint', True)
        self.switch_key = _get_int(a, 'switch_key', 0)
        self.bbox = _get_bool(a, 'bbox', False)
        self.timestamp = _get_bool(a, 'timestamp', True)
        self.interval_ms = _clamp(_get_int(a, 'interval_ms', 250), 100, 5000)
        self.default_cameras = split_cameras(_get_str(a, 'default_camera'))

        self.warn_ms = _clamp(_get_int(a, 'warn_ms', 1500), 250, 60000)
        self.freeze_ms = _clamp(_get_int(a, 'freeze_ms', 4000), 500, 120000)
        if self.freeze_ms <= self.warn_ms:
            self.freeze_ms = self.warn_ms + 500
        self.notify_errors = _get_bool(a, 'notify_errors', False)
        self.use_stats = _get_bool(a, 'use_stats', True)

        self.zoom_enabled = _get_bool(a, 'zoom_enabled', False)
        self.ask_region = _get_bool(a, 'ask_region', True)

        self.rtsp_host = _get_str(a, 'rtsp_host').strip()
        self.rtsp_port = _clamp(_get_int(a, 'rtsp_port', 8554), 1, 65535)
        self.rtsp_user = _get_str(a, 'rtsp_user').strip()
        self.rtsp_password = _get_str(a, 'rtsp_password')
        self.rtsp_stream = _get_str(a, 'rtsp_stream').strip() or '{camera}'
        self.resume_after = _get_bool(a, 'resume_after', True)

    @property
    def configured(self):
        return bool(self.host)

    def rtsp_url(self, camera, with_credentials=True):
        """RTSP URL of the camera stream; with_credentials=False masks them (for logs)."""
        host = self.rtsp_host or self.host
        if ':' in host and not host.startswith('['):
            host = '[%s]' % host                      # IPv6 literal
        stream = self.rtsp_stream.replace('{camera}', camera)
        auth = ''
        if with_credentials and self.rtsp_user:
            auth = quote(self.rtsp_user, safe='')
            if self.rtsp_password:
                auth += ':' + quote(self.rtsp_password, safe='')
            auth += '@'
        elif self.rtsp_user:
            auth = '***@'
        return 'rtsp://%s%s:%d/%s' % (auth, host, self.rtsp_port, quote(stream, safe='/_-.~'))

    def connection_key(self):
        return (self.host, self.port, self.https, self.username, self.password,
                self.tls_mode, normalize_fp(self.fingerprint))


def make_client(settings):
    return FrigateClient(
        host=settings.host,
        port=settings.port,
        https=settings.https,
        username=settings.username,
        password=settings.password,
        tls_mode=settings.tls_mode,
        fingerprint=settings.fingerprint,
        timeout=5.0,
        logger=log,
    )
