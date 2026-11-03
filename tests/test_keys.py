"""Key handling while the overlay is visible."""
import pytest

from resources.lib import keys as k

MENU, FULL = False, True


@pytest.mark.parametrize('action', [k.ACTION_PAUSE, k.ACTION_PLAYER_PLAYPAUSE, k.ACTION_STOP, k.ACTION_VOLUME_UP,
                                    k.ACTION_MUTE, k.ACTION_BUILT_IN_FUNCTION, k.ACTION_MOUSE_MOVE])
@pytest.mark.parametrize('fullscreen', [MENU, FULL])
def test_keys_kodi_handles_itself_are_ignored(action, fullscreen):
    assert k.decide(action, fullscreen, False, k.SWITCH_OK_FULLSCREEN) == (k.IGNORE, None)


@pytest.mark.parametrize('action', [k.ACTION_NAV_BACK, k.ACTION_PREVIOUS_MENU, k.ACTION_MOUSE_RIGHT_CLICK])
def test_back_closes(action):
    assert k.decide(action, FULL, False, k.SWITCH_OK_FULLSCREEN) == (k.CLOSE, None)


def test_ok_switches_only_in_full_screen():
    assert k.decide(k.ACTION_SELECT_ITEM, FULL, False, k.SWITCH_OK_FULLSCREEN) == (k.SWITCH, None)
    assert k.decide(k.ACTION_SELECT_ITEM, MENU, False, k.SWITCH_OK_FULLSCREEN) == (k.HIDE, 'select')


def test_other_switch_keys():
    assert k.decide(k.ACTION_SHOW_INFO, MENU, False, k.SWITCH_INFO) == (k.SWITCH, None)
    assert k.decide(k.ACTION_CONTEXT_MENU, MENU, False, k.SWITCH_CONTEXT) == (k.SWITCH, None)
    assert k.decide(k.ACTION_SELECT_ITEM, FULL, False, k.SWITCH_NONE) == (k.HIDE, 'select')


def test_menu_navigation_steps_aside():
    for action, name in ((k.ACTION_MOVE_DOWN, 'down'), (k.ACTION_CONTEXT_MENU, 'contextmenu'),
                         (k.ACTION_REMOTE_0 + 7, 'number7')):
        assert k.decide(action, MENU, False, k.SWITCH_OK_FULLSCREEN) == (k.HIDE, name)


def test_seeking_in_full_screen_video():
    assert k.decide(k.ACTION_MOVE_LEFT, FULL, False, 0) == (k.BUILTIN, 'PlayerControl(SmallSkipBackward)')
    assert k.decide(k.ACTION_MOVE_RIGHT, FULL, False, 0) == (k.BUILTIN, 'PlayerControl(SmallSkipForward)')
    assert k.decide(k.ACTION_MOVE_UP, FULL, False, 0) == (k.BUILTIN, 'PlayerControl(BigSkipForward)')
    assert k.decide(k.ACTION_MOVE_DOWN, FULL, False, 0) == (k.BUILTIN, 'PlayerControl(BigSkipBackward)')
    assert k.decide(k.ACTION_NEXT_ITEM, FULL, False, 0) == (k.IGNORE, None)


def test_live_tv_channel_switching():
    assert k.decide(k.ACTION_MOVE_UP, FULL, True, 0) == (k.HIDE_GUARDED, 'channelup')
    assert k.decide(k.ACTION_CHANNEL_DOWN, FULL, True, 0) == (k.HIDE_GUARDED, 'channeldown')
    assert k.decide(k.ACTION_SHOW_OSD, FULL, True, 0) == (k.HIDE, 'osd')
