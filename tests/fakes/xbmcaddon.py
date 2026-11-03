"""Minimal stand-in for Kodi's xbmcaddon module (tests only)."""
import os
import re
import tempfile

ADDON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, 'script.frigate.pip')
SETTINGS = {}
PROFILE = [tempfile.mkdtemp(prefix='frigatepip-profile-')]
LANGUAGE = ['en_gb']
OPENED_SETTINGS = []
_PO_CACHE = {}


def _po(language):
    if language not in _PO_CACHE:
        path = os.path.join(ADDON_PATH, 'resources', 'language', 'resource.language.%s' % language, 'strings.po')
        text = open(path, encoding='utf-8').read()
        table = {}
        for sid, msgid, msgstr in re.findall(
                r'msgctxt "#(\d+)"\nmsgid "((?:[^"\\]|\\.)*)"\nmsgstr "((?:[^"\\]|\\.)*)"', text):
            un = lambda x: x.replace('\\"', '"').replace('\\\\', '\\')  # noqa: E731
            table[int(sid)] = un(msgstr) or un(msgid)
        _PO_CACHE[language] = table
    return _PO_CACHE[language]


def reset():
    SETTINGS.clear()
    PROFILE[0] = tempfile.mkdtemp(prefix='frigatepip-profile-')
    LANGUAGE[0] = 'en_gb'
    del OPENED_SETTINGS[:]


class Addon(object):
    def __init__(self, addon_id=None):
        pass

    def getAddonInfo(self, key):
        return {'path': ADDON_PATH, 'profile': PROFILE[0], 'id': 'script.frigate.pip'}.get(key, '')

    def getLocalizedString(self, string_id):
        return _po(LANGUAGE[0]).get(string_id, '')

    def getSetting(self, key):
        value = SETTINGS.get(key, '')
        if isinstance(value, bool):
            return 'true' if value else 'false'
        return str(value)

    def getSettingInt(self, key):
        if key not in SETTINGS:
            raise RuntimeError('no such setting')
        return int(SETTINGS[key])

    def getSettingBool(self, key):
        if key not in SETTINGS:
            raise RuntimeError('no such setting')
        return bool(SETTINGS[key])

    def setSetting(self, key, value):
        SETTINGS[key] = value

    def setSettingInt(self, key, value):
        SETTINGS[key] = int(value)

    def setSettingBool(self, key, value):
        SETTINGS[key] = bool(value)

    def openSettings(self):
        OPENED_SETTINGS.append(True)
