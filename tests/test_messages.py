"""Consistency of the language files and the message catalogue."""
import os
import re

import pytest
import xbmcaddon

from resources.lib import messages

LANG_DIR = os.path.join(os.path.dirname(messages.__file__), os.pardir, 'language')
SETTINGS_XML = os.path.join(os.path.dirname(messages.__file__), os.pardir, 'settings.xml')
LIB_DIR = os.path.dirname(messages.__file__)
PLACEHOLDER = re.compile(r'\{(\w+)\}')


def _po_entries(language):
    path = os.path.join(LANG_DIR, 'resource.language.%s' % language, 'strings.po')
    text = open(path, encoding='utf-8').read()
    return {int(sid): (msgid, msgstr) for sid, msgid, msgstr in re.findall(
        r'msgctxt "#(\d+)"\nmsgid "((?:[^"\\]|\\.)*)"\nmsgstr "((?:[^"\\]|\\.)*)"', text)}


EN = _po_entries('en_gb')
DE = _po_entries('de_de')


def test_both_languages_have_the_same_ids_and_source_texts():
    assert set(EN) == set(DE)
    for sid in EN:
        assert EN[sid][0] == DE[sid][0], sid


def test_english_file_has_no_translations():
    assert all(msgstr == '' for _msgid, msgstr in EN.values())


def test_every_string_is_translated_to_german():
    assert [sid for sid, (_msgid, msgstr) in DE.items() if not msgstr] == []


@pytest.mark.parametrize('sid', sorted(DE))
def test_placeholders_match(sid):
    msgid, msgstr = DE[sid]
    assert sorted(PLACEHOLDER.findall(msgid)) == sorted(PLACEHOLDER.findall(msgstr))


def test_every_key_has_a_string():
    assert [key for key, sid in messages.STRINGS.items() if sid not in EN] == []


def test_settings_reference_existing_strings_only():
    xml = open(SETTINGS_XML, encoding='utf-8').read()
    ids = {int(x) for x in re.findall(r'(?:label|help)="(\d+)"', xml)}
    ids |= {int(x) for x in re.findall(r'<heading>(\d+)</heading>', xml)}
    assert ids - set(EN) == set()


def test_no_unused_strings():
    xml = open(SETTINGS_XML, encoding='utf-8').read()
    used = {int(x) for x in re.findall(r'(?:label|help)="(\d+)"', xml)}
    used |= {int(x) for x in re.findall(r'<heading>(\d+)</heading>', xml)}
    used |= set(messages.STRINGS.values())
    assert sorted(set(EN) - used) == []


def test_every_key_is_used_in_the_code():
    code = ''
    for name in os.listdir(LIB_DIR):
        if name.endswith('.py') and name != 'messages.py':
            code += open(os.path.join(LIB_DIR, name), encoding='utf-8').read()
    unused = [key for key in messages.STRINGS if key != 'decimal_separator' and "'%s'" % key not in code]
    assert unused == []


def test_english_and_localized_text():
    assert messages.english('err_refused', host='h', port=1) == 'Connection refused (h:1)'
    xbmcaddon.LANGUAGE[0] = 'de_de'
    assert messages.tr('err_refused', host='h', port=1) == 'Verbindung abgelehnt (h:1)'
    assert messages.english('err_refused', host='h', port=1) == 'Connection refused (h:1)'


def test_decimal_separator_follows_language():
    assert messages.decimal(4.75) == '4.8'
    xbmcaddon.LANGUAGE[0] = 'de_de'
    assert messages.decimal(4.75) == '4,8'


def test_broken_translation_falls_back_to_english():
    original = messages._translator
    messages.set_translator(lambda sid: 'kaputt {unbekannt}')
    try:
        assert messages.tr('err_refused', host='h', port=1) == 'Connection refused (h:1)'
    finally:
        messages.set_translator(original)


def test_failing_translator_falls_back_to_english():
    original = messages._translator

    def boom(sid):
        raise RuntimeError('Kodi is shutting down')
    messages.set_translator(boom)
    try:
        assert messages.tr('st_live') == 'live'
    finally:
        messages.set_translator(original)
