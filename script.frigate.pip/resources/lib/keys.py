# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Key handling for the overlay.

Kodi treats every Python dialog window as modal: navigation keys end up at the
overlay, and menus behind it cannot be used properly. Kodi does, however, pass
every key the overlay does not consume on to its global handling. That is
where player and volume keys as well as keymap commands (RunScript ...) are
processed - Kodi executes those itself.

Consequences:
- Player/volume keys and keymap commands: do nothing (otherwise they would run twice).
- Back/Exit: close the PiP.
- Full screen key: play the camera stream in the Kodi player.
- Seeking in full screen: directly via PlayerControl (the global handling
  would treat those keys as navigation).
- Live TV up/down: the PiP steps aside and the channel is switched - but only
  if Kodi has not switched it already (HIDE_GUARDED).
- Any other key: the PiP steps aside and the key is passed to Kodi as usual
  (HIDE). After a few seconds without key presses the PiP comes back.

This module does not import any Kodi module, so it can be tested on its own.
"""

# Kodi action IDs (xbmc/input/actions/ActionIDs.h)
ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_PAGE_UP = 5
ACTION_PAGE_DOWN = 6
ACTION_SELECT_ITEM = 7
ACTION_PARENT_DIR = 9
ACTION_PREVIOUS_MENU = 10
ACTION_SHOW_INFO = 11
ACTION_PAUSE = 12
ACTION_STOP = 13
ACTION_NEXT_ITEM = 14
ACTION_PREV_ITEM = 15
ACTION_SHOW_GUI = 18
ACTION_ASPECT_RATIO = 19
ACTION_SHOW_OSD = 24
ACTION_SHOW_SUBTITLES = 25
ACTION_NEXT_SUBTITLE = 26
ACTION_SHOW_PLAYLIST = 33
ACTION_REMOTE_0 = 58
ACTION_REMOTE_9 = 67
ACTION_PLAYER_PLAY = 68
ACTION_PLAYER_FORWARD = 77
ACTION_PLAYER_REWIND = 78
ACTION_VOLUME_UP = 88
ACTION_VOLUME_DOWN = 89
ACTION_MUTE = 91
ACTION_NAV_BACK = 92
ACTION_MOUSE_LEFT_CLICK = 100
ACTION_MOUSE_RIGHT_CLICK = 101
ACTION_MOUSE_MOVE = 107
ACTION_BUILT_IN_FUNCTION = 122
ACTION_CONTEXT_MENU = 117
ACTION_FIRST_PAGE = 159
ACTION_LAST_PAGE = 160
ACTION_MENU = 163
ACTION_CHANNEL_UP = 184
ACTION_CHANNEL_DOWN = 185
ACTION_PLAYER_PLAYPAUSE = 229
ACTION_NOOP = 999

WINDOW_FULLSCREEN_VIDEO = 12005

# Result types
CLOSE = 'close'            # end the PiP
SWITCH = 'switch'          # camera in the Kodi player (full screen)
BUILTIN = 'builtin'        # Kodi built-in, acts directly, PiP stays
HIDE = 'hide'              # hide the PiP, then pass the action (if known) to Kodi
HIDE_GUARDED = 'hide_guarded'  # like HIDE, but only if Kodi has not switched the channel itself
IGNORE = 'ignore'          # do nothing (Kodi handles the key itself, or it is irrelevant)

# Setting "Key for camera full screen"
SWITCH_OK_FULLSCREEN = 0   # OK, but only during full screen video playback
SWITCH_INFO = 1
SWITCH_CONTEXT = 2
SWITCH_NONE = 3

CLOSE_ACTIONS = (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_MOUSE_RIGHT_CLICK)
IGNORE_ACTIONS = (ACTION_MOUSE_MOVE, ACTION_NOOP, ACTION_BUILT_IN_FUNCTION)
# Kodi handles these keys globally itself - even while the overlay is open
KODI_HANDLES = (ACTION_PAUSE, ACTION_PLAYER_PLAY, ACTION_PLAYER_PLAYPAUSE, ACTION_STOP,
                ACTION_PLAYER_FORWARD, ACTION_PLAYER_REWIND, ACTION_VOLUME_UP, ACTION_VOLUME_DOWN,
                ACTION_MUTE)

# Names for JSON-RPC Input.ExecuteAction
ACTION_NAMES = {
    ACTION_MOVE_LEFT: 'left',
    ACTION_MOVE_RIGHT: 'right',
    ACTION_MOVE_UP: 'up',
    ACTION_MOVE_DOWN: 'down',
    ACTION_PAGE_UP: 'pageup',
    ACTION_PAGE_DOWN: 'pagedown',
    ACTION_SELECT_ITEM: 'select',
    ACTION_PARENT_DIR: 'parentdir',
    ACTION_SHOW_INFO: 'info',
    ACTION_NEXT_ITEM: 'skipnext',
    ACTION_PREV_ITEM: 'skipprevious',
    ACTION_SHOW_GUI: 'fullscreen',
    ACTION_ASPECT_RATIO: 'aspectratio',
    ACTION_SHOW_OSD: 'osd',
    ACTION_SHOW_SUBTITLES: 'showsubtitles',
    ACTION_NEXT_SUBTITLE: 'nextsubtitle',
    ACTION_SHOW_PLAYLIST: 'playlist',
    ACTION_CONTEXT_MENU: 'contextmenu',
    ACTION_FIRST_PAGE: 'firstpage',
    ACTION_LAST_PAGE: 'lastpage',
    ACTION_MENU: 'menu',
    ACTION_CHANNEL_UP: 'channelup',
    ACTION_CHANNEL_DOWN: 'channeldown',
}
for _n in range(10):
    ACTION_NAMES[ACTION_REMOTE_0 + _n] = 'number%d' % _n


def is_switch(action_id, fullscreen, switch_key):
    """True if the key opens the camera in full screen."""
    if switch_key == SWITCH_OK_FULLSCREEN:
        return action_id == ACTION_SELECT_ITEM and fullscreen
    if switch_key == SWITCH_INFO:
        return action_id == ACTION_SHOW_INFO
    if switch_key == SWITCH_CONTEXT:
        return action_id == ACTION_CONTEXT_MENU
    return False


def decide(action_id, fullscreen, live, switch_key):
    """Return (result type, argument) for a key press in the overlay."""
    if action_id in IGNORE_ACTIONS or action_id in KODI_HANDLES:
        return (IGNORE, None)
    if action_id in CLOSE_ACTIONS:
        return (CLOSE, None)
    if is_switch(action_id, fullscreen, switch_key):
        return (SWITCH, None)

    if fullscreen:
        # Seeking goes directly to the player - the PiP stays visible
        if action_id == ACTION_MOVE_LEFT:
            return (BUILTIN, 'PlayerControl(SmallSkipBackward)')
        if action_id == ACTION_MOVE_RIGHT:
            return (BUILTIN, 'PlayerControl(SmallSkipForward)')
        if live:
            # Live TV: switching channels only works through Kodi itself
            if action_id in (ACTION_MOVE_UP, ACTION_PAGE_UP, ACTION_NEXT_ITEM, ACTION_CHANNEL_UP):
                return (HIDE_GUARDED, 'channelup')
            if action_id in (ACTION_MOVE_DOWN, ACTION_PAGE_DOWN, ACTION_PREV_ITEM, ACTION_CHANNEL_DOWN):
                return (HIDE_GUARDED, 'channeldown')
        else:
            if action_id == ACTION_MOVE_UP:
                return (BUILTIN, 'PlayerControl(BigSkipForward)')
            if action_id == ACTION_MOVE_DOWN:
                return (BUILTIN, 'PlayerControl(BigSkipBackward)')
            if action_id in (ACTION_NEXT_ITEM, ACTION_PREV_ITEM):
                return (IGNORE, None)     # next/previous item: Kodi does that itself

    # --- everything else: the PiP steps aside, Kodi gets the key as usual
    return (HIDE, ACTION_NAMES.get(action_id))
