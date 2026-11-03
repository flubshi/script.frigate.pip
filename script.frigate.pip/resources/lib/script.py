# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Entry point for RunScript, Addons.ExecuteAddon and the Programs menu.

The script itself does very little: it forwards the command to the background
service via JSON-RPC (JSONRPC.NotifyAll). Examples:

    RunScript(script.frigate.pip)                                    choose a camera
    RunScript(script.frigate.pip,camera=front_door)                  show a camera
    RunScript(script.frigate.pip,camera=front_door+garden,duration=60)
    RunScript(script.frigate.pip,action=toggle)                      for remote keys
    RunScript(script.frigate.pip,action=hide)
    RunScript(script.frigate.pip,action=fullscreen,camera=garden)    camera in the Kodi player
    RunScript(script.frigate.pip,action=swap)                        camera <-> previous playback
    RunScript(script.frigate.pip,action=roi,camera=garden)           define the image section
    RunScript(script.frigate.pip,camera=garden,full=1)               ignore the saved section
    RunScript(script.frigate.pip,action=setup)                       setup wizard
"""
import json

import xbmc
import xbmcgui

from .common import (
    ADDON_ID,
    HOME_WINDOW,
    MSG_PREFIX,
    PROP_RUNNING,
    log,
    log_debug,
    notify,
    parse_duration,
    split_cameras,
    tr,
)

SERVICE_ACTIONS = ('show', 'hide', 'toggle', 'select', 'fullscreen', 'swap', 'roi', 'setup')
LOCAL_ACTIONS = ('trustcert', 'test')
TRUE_VALUES = ('1', 'true', 'yes', 'on')


def parse_args(args):
    """Parse RunScript arguments -> (action, cameras, duration, full_picture)."""
    action, cameras, duration, full = None, [], None, False
    for token in args:
        token = (token or '').strip()
        if token.startswith('?'):
            token = token[1:]
        if not token:
            continue
        key, sep, value = token.partition('=')
        if not sep:
            # RunScript splits "camera=a,b" into ["camera=a", "b"]
            cameras.extend(split_cameras(token))
            continue
        key, value = key.strip().lower(), value.strip()
        if key == 'action':
            action = value.lower()
        elif key in ('camera', 'cameras', 'cam'):
            cameras.extend(split_cameras(value))
        elif key == 'duration':
            duration = parse_duration(value)
        elif key == 'full':
            full = value.lower() in TRUE_VALUES
    cameras = split_cameras(cameras)
    if action is None:
        action = 'show' if cameras else 'select'
    return action, cameras, duration, full


def send_command(action, data):
    request = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'JSONRPC.NotifyAll',
        'params': {'sender': ADDON_ID, 'message': MSG_PREFIX + action, 'data': data},
    }
    result = xbmc.executeJSONRPC(json.dumps(request))
    log_debug('Command sent: %s %s -> %s' % (action, data, result))


def main(argv):
    action, cameras, duration, full = parse_args(argv[1:])
    if action in LOCAL_ACTIONS:
        from . import tools
        if action == 'trustcert':
            tools.trust_certificate()
        else:
            tools.connection_test()
        return
    if action not in SERVICE_ACTIONS:
        notify(tr('unknown_action', action=action), xbmcgui.NOTIFICATION_WARNING)
        return
    if xbmcgui.Window(HOME_WINDOW).getProperty(PROP_RUNNING) != '1':
        log('Background service is not running, command "%s" dropped' % action)
        notify(tr('service_not_running'), xbmcgui.NOTIFICATION_ERROR)
        return
    data = {}
    if cameras:
        data['camera'] = '+'.join(cameras)
    if duration is not None:
        data['duration'] = duration
    if full:
        data['full'] = True
    send_command(action, data)
