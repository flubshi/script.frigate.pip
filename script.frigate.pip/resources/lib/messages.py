# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Message catalogue for user-visible texts.

Every text lives in resources/language/*/strings.po. Code refers to texts by a
symbolic key; this module maps the key to the string ID.

- tr(key, ...)       localized text (uses Kodi's translation once a translator
                     has been registered with set_translator, English otherwise)
- english(key, ...)  English text, used for log messages

Placeholders use str.format syntax: "Connection refused ({host}:{port})".
If a translation is broken (e.g. a placeholder was renamed), the English
text is used instead, so a bad translation can never crash the add-on.

This module does not import any Kodi module, so it can be used in tests.
"""
import os
import re
import threading

STRINGS = {
    # ------------------------------------------------------------- general
    'decimal_separator': 32000,
    'not_configured_ask': 32001,
    'not_configured': 32002,
    'unknown_cameras': 32003,
    'zoom_needs_pillow': 32004,
    'zoom_disabled': 32005,
    'region_saved_full': 32006,
    'region_saved_zoom': 32007,
    'choose_camera': 32008,
    'all_cameras': 32009,
    'region_heading': 32010,
    'region_saved_option': 32011,
    'region_full_option': 32012,
    'region_new_option': 32013,
    'camera_list_unavailable': 32014,
    'no_active_cameras': 32015,
    'cert_confirm_heading': 32016,
    'cert_confirm_text': 32017,
    'cert_not_confirmed': 32018,
    'frigate_unreachable': 32019,
    'unknown_action': 32020,
    'service_not_running': 32021,
    'live_failed': 32022,
    'zoom_failed': 32023,
    'camera_error_notification': 32024,
    'cert_changed_text': 32025,
    'cert_trust_text': 32026,
    'cert_already_trusted': 32027,
    'cert_saved': 32028,
    'enter_host_first': 32029,
    'https_off_no_cert': 32030,
    'connection_failed': 32031,
    'unexpected_error': 32032,
    'connection_test_heading': 32033,
    'testing_connection': 32034,
    'live_title': 32035,
    # ------------------------------------------------------------- client errors
    'err_generic': 32600,
    'err_timeout_connect': 32601,
    'err_tls_handshake': 32602,
    'err_refused': 32603,
    'err_network': 32604,
    'err_no_certificate': 32605,
    'err_cert_untrusted': 32606,
    'err_cert_changed': 32607,
    'err_cert_invalid': 32608,
    'err_http': 32609,
    'err_http_detail': 32610,
    'err_camera_unknown': 32611,
    'err_connection_lost': 32612,
    'err_no_response': 32613,
    'err_tls': 32614,
    'err_protocol': 32615,
    'err_login_rejected': 32616,
    'err_login_rejected_detail': 32617,
    'err_login_rate_limited': 32618,
    'err_login_failed': 32619,
    'err_login_failed_detail': 32620,
    'err_login_required': 32621,
    'err_access_denied': 32622,
    'err_bad_json': 32623,
    'err_not_jpeg': 32624,
    'err_store_image': 32625,
    'err_crop': 32626,
    'err_not_frigate': 32627,
    'title_error': 32640,
    'title_connection': 32641,
    'title_timeout': 32642,
    'title_login': 32643,
    'title_certificate': 32644,
    'title_cert_untrusted': 32645,
    'title_not_found': 32646,
    'title_forbidden': 32647,
    'title_server': 32648,
    # ------------------------------------------------------------- status
    'st_connecting': 32700,
    'st_live': 32701,
    'st_live_fps': 32702,
    'st_delayed': 32703,
    'st_frozen': 32704,
    'bn_frozen': 32705,
    'st_no_signal': 32706,
    'bn_no_signal': 32707,
    'st_no_response': 32708,
    'bn_no_response': 32709,
    'st_no_picture': 32710,
    'bn_no_picture': 32711,
    # ------------------------------------------------------------- overlay / picker
    'hint_fullscreen': 32750,
    'hint_menu': 32751,
    'picker_title': 32752,
    'picker_title_error': 32753,
    'picker_help': 32754,
    'picker_zoom': 32755,
    'picker_source_pixels': 32756,
    'picker_full': 32757,
    # ------------------------------------------------------------- connection test
    'test_server': 32800,
    'test_cert_mode': 32801,
    'test_tls_failed': 32802,
    'test_fingerprint': 32803,
    'test_no_pin': 32804,
    'test_pin_mismatch': 32805,
    'test_pin_stored': 32806,
    'test_pin_ok': 32807,
    'test_no_verify': 32808,
    'test_plain_http': 32809,
    'test_autocorrect': 32810,
    'test_non_ascii': 32811,
    'test_login_ok': 32812,
    'test_login_failed': 32813,
    'test_no_login': 32814,
    'test_version': 32815,
    'test_version_failed': 32816,
    'test_cameras_failed': 32817,
    'test_cameras': 32818,
    'test_camera_disabled': 32819,
    'test_snapshot_ok': 32820,
    'test_snapshot_failed': 32821,
    'test_stats': 32822,
    'test_stats_no_data': 32823,
    'test_stats_forbidden': 32824,
    'test_stats_failed': 32825,
    'test_rtsp_ok': 32826,
    'test_rtsp_no_user': 32827,
    'test_rtsp_failed': 32828,
    'test_pillow_ok': 32829,
    'test_pillow_missing': 32830,
    'test_done': 32831,
    'test_unexpected': 32832,
    'test_no_host': 32833,
    'label_user': 32834,
    'label_password': 32835,
    'tls_mode_pin': 32121,
    'tls_mode_system': 32122,
    'tls_mode_none': 32123,
    # ------------------------------------------------------------- setup wizard
    'wiz_heading': 32900,
    'wiz_offer': 32901,
    'wiz_intro': 32902,
    'wiz_enter_address': 32903,
    'wiz_invalid_address': 32904,
    'wiz_searching': 32905,
    'wiz_trying': 32906,
    'wiz_not_found': 32907,
    'wiz_looks_like_ha': 32908,
    'wiz_retry': 32909,
    'wiz_found_plain': 32910,
    'wiz_found_open': 32911,
    'wiz_cert_text': 32912,
    'wiz_enter_user': 32913,
    'wiz_enter_password': 32914,
    'wiz_autocorrect': 32915,
    'wiz_logging_in': 32916,
    'wiz_login_failed': 32917,
    'wiz_loading_cameras': 32918,
    'wiz_no_cameras': 32919,
    'wiz_cameras_failed': 32920,
    'wiz_default_camera': 32921,
    'wiz_no_default': 32922,
    'wiz_rtsp_offer': 32923,
    'wiz_rtsp_user': 32924,
    'wiz_rtsp_password': 32925,
    'wiz_rtsp_unreachable': 32926,
    'wiz_test_now': 32928,
    'wiz_cancelled': 32929,
    'wiz_saved': 32930,
    'wiz_cancel': 32931,
    'wiz_next': 32933,
    'wiz_yes': 32934,
    'wiz_no': 32935,
}

_PO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'language',
                        'resource.language.en_gb', 'strings.po')
_ENTRY = re.compile(r'^msgctxt "#(\d+)"\s*\nmsgid "((?:[^"\\]|\\.)*)"', re.MULTILINE)

_lock = threading.Lock()
_english = None
_translator = None


def _unescape(text):
    return text.replace('\\"', '"').replace('\\\\', '\\')


def load_po(path):
    """Return {string_id: msgid} from a strings.po file (single-line entries only)."""
    with open(path, encoding='utf-8') as handle:
        content = handle.read()
    return {int(sid): _unescape(text) for sid, text in _ENTRY.findall(content)}


def _english_table():
    global _english
    with _lock:
        if _english is None:
            try:
                _english = load_po(_PO_PATH)
            except (OSError, ValueError):
                _english = {}
        return _english


def set_translator(func):
    """Register a function string_id -> localized text ('' if unknown)."""
    global _translator
    _translator = func


def _format(template, fallback, params):
    if params:
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            try:
                return fallback.format(**params)
            except (KeyError, IndexError, ValueError):
                return fallback
    return template


def english(key, **params):
    """English text for a key (for log files)."""
    sid = STRINGS[key]
    template = _english_table().get(sid, key)
    return _format(template, key, params)


def tr(key, **params):
    """Localized text for a key."""
    sid = STRINGS[key]
    fallback = _english_table().get(sid, key)
    template = fallback
    translator = _translator
    if translator is not None:
        try:
            localized = translator(sid)
        except Exception:
            localized = ''
        if localized:
            template = localized
    return _format(template, fallback, params)


def decimal(value, digits=1):
    """Number with the decimal separator of the current language ("4.8" / "4,8")."""
    text = '%.*f' % (digits, value)
    separator = tr('decimal_separator') or '.'
    return text.replace('.', separator) if separator != '.' else text
