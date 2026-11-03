"""Integration tests: background service, overlay, keys, camera full screen and zoom."""
import os
import threading
import time

import pytest
import xbmc
import xbmcaddon
import xbmcgui

from conftest import RunningService, wait_for
from resources.lib import keys as k
from resources.lib import service as service_module
from resources.lib.overlay import BORDER_COLORS
from resources.lib.poller import LEVEL_ERROR, LEVEL_OK

GREEN, RED = BORDER_COLORS[LEVEL_OK], BORDER_COLORS[LEVEL_ERROR]
LIVE_COND = 'Pvr.IsPlayingTv | Pvr.IsPlayingRadio'
P = xbmc.Player


def executed(action):
    return any(r.get('method') == 'Input.ExecuteAction' and r['params'] == {'action': action}
               for r in xbmc.JSONRPC)


def open_overlay(svc, **data):
    svc.notify('show', data)
    assert wait_for(svc.visible, 4), 'overlay did not open'
    return svc.session


# ---------------------------------------------------------------- overlay basics
def test_overlay_shows_live_picture_and_closes_after_duration(service):
    sess = open_overlay(service, camera='front_door', duration=2)
    slot = sess.view.slots[0]
    assert wait_for(lambda: slot.border.color == GREEN, 3)
    assert slot.status.label.startswith('live')
    assert wait_for(lambda: len([c for c in slot.img_under.calls if c[0] == 'setImage']) > 2, 3)
    assert sess.pollers[0].transform is None            # no decoding without zoom
    assert 300 <= sess.pollers[0].height <= 720          # resolution matches the overlay size
    assert wait_for(lambda: service.session is None, 4)
    assert not any(p.is_alive() for p in sess.pollers)


def test_first_connection_asks_to_trust_the_certificate(service, frigate):
    xbmcaddon.SETTINGS['tls_fingerprint'] = ''
    service.service.reload_settings()
    xbmcgui.UI.push(('yesno', True))
    open_overlay(service, camera='garden')
    assert xbmcaddon.SETTINGS['tls_fingerprint'].replace(':', '').lower() == frigate.fingerprint
    assert 'SHA-256' in xbmcgui.UI.texts('yesno')[0]


def test_declined_certificate_opens_nothing(service):
    xbmcaddon.SETTINGS['tls_fingerprint'] = ''
    service.service.reload_settings()
    xbmcgui.UI.push(('yesno', False))
    service.notify('show', {'camera': 'garden'})
    time.sleep(1)
    assert service.session is None and xbmcaddon.SETTINGS['tls_fingerprint'] == ''


def test_two_cameras_are_stacked(service):
    sess = open_overlay(service, camera='front_door+garden')
    first, second = (slot.border.rect for slot in sess.view.slots)
    assert first[0] == second[0] and second[1] > first[1] and first[0] + first[2] <= 1280


def test_stall_is_visible_and_recovers(service, frigate_state):
    sess = open_overlay(service, camera='front_door')
    slot = sess.view.slots[0]
    assert wait_for(lambda: slot.border.color == GREEN, 3)
    frigate_state.freeze = True
    assert wait_for(lambda: slot.banner.visible and 'PICTURE FROZEN' in slot.banner.label, 4)
    assert slot.border.color == RED
    assert any('front_door' in n for n in xbmcgui.UI.notifications)
    frigate_state.freeze = False
    assert wait_for(lambda: not slot.banner.visible, 3)


def test_hide_command(service):
    open_overlay(service, camera='garden')
    service.notify('hide')
    assert wait_for(lambda: service.session is None, 4)


def test_unknown_camera_is_reported(service):
    service.notify('show', {'camera': 'garage'})
    assert wait_for(lambda: any('garage' in n for n in xbmcgui.UI.notifications), 3)
    assert service.session is None


def test_outdated_show_command_is_dropped(service):
    cmd = service_module.Command('show', ['garden'])
    cmd.created -= service_module.STALE_AFTER + 1
    service.service.queue.put(cmd)
    time.sleep(0.5)
    assert service.session is None


# ---------------------------------------------------------------- keys
def test_player_keys_do_not_hide_the_pip(service):
    sess = open_overlay(service, camera='garden')
    assert 'Back: close' in sess.view.hint.label
    for action in (k.ACTION_PLAYER_PLAYPAUSE, k.ACTION_VOLUME_UP, k.ACTION_MUTE, k.ACTION_STOP,
                   k.ACTION_BUILT_IN_FUNCTION):
        service.key(action)
    time.sleep(0.2)
    assert xbmc.BUILTINS == [] and xbmc.JSONRPC == [] and sess.visible


def test_navigation_steps_aside_and_the_pip_comes_back(service):
    sess = open_overlay(service, camera='garden')
    first_view = sess.view
    service.key(k.ACTION_MOVE_DOWN)
    assert wait_for(lambda: sess.hidden and executed('down'), 2)
    assert all(p.is_alive() for p in sess.pollers)       # pictures stay current while hidden
    time.sleep(0.5)
    assert sess.hidden                                    # user is still pressing keys
    xbmc.COND[service_module.RESHOW_BLOCKERS] = True
    xbmc.IDLE[0] = 10
    time.sleep(0.4)
    assert sess.hidden                                    # not over an open Kodi dialog
    xbmc.COND[service_module.RESHOW_BLOCKERS] = False
    assert wait_for(lambda: sess.visible, 2)
    assert sess.view is not first_view and sess.view.hint is None


def test_new_alarm_brings_the_hidden_pip_back_quickly(service):
    sess = open_overlay(service, camera='garden')
    service.key(k.ACTION_CONTEXT_MENU)
    assert wait_for(lambda: sess.hidden and executed('contextmenu'), 2)
    service.notify('show', {'camera': 'garden'})
    time.sleep(0.3)
    assert sess.hidden
    xbmc.IDLE[0] = 1
    assert wait_for(lambda: sess.visible, 2)


def test_toggle_shows_hidden_pip_and_closes_visible_pip(service):
    sess = open_overlay(service, camera='garden')
    service.key(k.ACTION_MOVE_LEFT)
    assert wait_for(lambda: sess.hidden, 2)
    service.notify('toggle')
    assert wait_for(lambda: sess.visible, 2)
    service.notify('toggle')
    assert wait_for(lambda: service.session is None, 3)


def test_back_closes_and_is_not_passed_on(service):
    open_overlay(service, camera='garden')
    service.key(k.ACTION_NAV_BACK)
    assert wait_for(lambda: service.session is None, 3)
    assert xbmc.JSONRPC == []


def test_duration_also_runs_while_hidden(service):
    sess = open_overlay(service, camera='garden', duration=2)
    service.key(k.ACTION_MOVE_UP)
    assert wait_for(lambda: service.session is None, 4)
    assert not sess.alive


def test_full_screen_video_keys(service):
    xbmcgui.CURRENT_WINDOW[0] = k.WINDOW_FULLSCREEN_VIDEO
    sess = open_overlay(service, camera='garden')
    assert 'OK: camera full screen' in sess.view.hint.label
    for action in (k.ACTION_MOVE_LEFT, k.ACTION_MOVE_RIGHT, k.ACTION_MOVE_UP, k.ACTION_MOVE_DOWN):
        service.key(action)
    assert xbmc.BUILTINS == ['PlayerControl(SmallSkipBackward)', 'PlayerControl(SmallSkipForward)',
                             'PlayerControl(BigSkipForward)', 'PlayerControl(BigSkipBackward)']
    assert sess.visible
    assert wait_for(lambda: sess.view.hint is None, 6)    # key hint disappears


def test_live_tv_channel_switch_is_not_doubled(service):
    xbmcgui.CURRENT_WINDOW[0] = k.WINDOW_FULLSCREEN_VIDEO
    xbmc.COND[LIVE_COND] = True
    xbmc.INFO['VideoPlayer.ChannelName'] = 'Channel 1'
    sess = open_overlay(service, camera='garden')
    service.key(k.ACTION_MOVE_UP)
    assert wait_for(lambda: sess.hidden and executed('channelup'), 3)
    xbmc.IDLE[0] = 5
    assert wait_for(lambda: sess.visible, 2)
    xbmc.IDLE[0] = 0
    del xbmc.JSONRPC[:]
    service.key(k.ACTION_MOVE_DOWN)
    xbmc.INFO['VideoPlayer.ChannelName'] = 'Channel 2'    # Kodi switched by itself
    time.sleep(1.8)
    assert not executed('channeldown')


def test_without_reshow_any_key_ends_the_pip(service):
    xbmcaddon.SETTINGS['reshow_s'] = 0
    service.service.reload_settings()
    open_overlay(service, camera='garden')
    service.key(k.ACTION_MOVE_UP)
    assert wait_for(lambda: service.session is None and executed('up'), 3)


# ---------------------------------------------------------------- camera full screen
def test_ok_opens_camera_full_screen_and_resumes_the_movie(service):
    P.CURRENT.update(path='/media/movie.mkv', time=1234.5, playing=True)
    xbmcgui.CURRENT_WINDOW[0] = k.WINDOW_FULLSCREEN_VIDEO
    open_overlay(service, camera='front_door')
    service.key(k.ACTION_SELECT_ITEM)
    assert wait_for(lambda: P.PLAYED and P.PLAYED[-1].startswith('rtsp://'), 3)
    assert P.PLAYED[-1] == 'rtsp://rtsp-user:p%40ss%3Aword@127.0.0.1:8554/front_door'
    P.fire('onPlayBackStopped')                    # the movie stops
    time.sleep(0.2)
    P.fire('onAVStarted')                          # the camera plays
    assert wait_for(lambda: service.service.live.showing_camera, 2)
    P.CURRENT['playing'] = False
    P.fire('onPlayBackStopped')                    # the user stops the camera
    assert wait_for(lambda: P.PLAYED[-1] == '/media/movie.mkv', 4)
    P.fire('onAVStarted')
    assert wait_for(lambda: P.SEEKS and abs(P.SEEKS[-1] - 1232.5) < 0.1, 2)
    assert wait_for(lambda: service.service.live.state is None, 2)


def test_live_tv_is_tuned_again_without_seeking(service):
    P.CURRENT.update(path='pvr://channels/tv/All/pvr.demo_42.pvr', playing=True)
    service.notify('fullscreen', {'camera': 'garden'})
    assert wait_for(lambda: P.PLAYED and P.PLAYED[-1].endswith('/garden'), 3)
    P.fire('onAVStarted')
    time.sleep(0.2)
    P.CURRENT['playing'] = False
    P.fire('onPlayBackEnded')
    assert wait_for(lambda: P.PLAYED[-1].startswith('pvr://channels/'), 4)
    P.fire('onAVStarted')
    time.sleep(0.3)
    assert P.SEEKS == []


def test_no_resume_if_the_user_started_something_else(service):
    P.CURRENT.update(path='/media/a.mkv', time=50.0, playing=True)
    service.notify('fullscreen', {'camera': 'garden'})
    assert wait_for(lambda: P.PLAYED and P.PLAYED[-1].endswith('/garden'), 3)
    P.fire('onAVStarted')
    time.sleep(0.2)
    P.PLAYED.append('/media/b.mkv')
    P.CURRENT.update(path='/media/b.mkv', playing=True)
    P.fire('onPlayBackStopped')
    time.sleep(2.2)
    assert P.PLAYED[-1] == '/media/b.mkv' and service.service.live.state is None


def test_failing_stream_resumes_and_reports(service):
    P.CURRENT.update(path='/media/c.mkv', time=99.0, playing=True)
    service.notify('fullscreen', {'camera': 'garden'})
    assert wait_for(lambda: P.PLAYED and P.PLAYED[-1].endswith('/garden'), 3)
    P.CURRENT['playing'] = False
    P.fire('onPlayBackError')
    assert wait_for(lambda: P.PLAYED[-1] == '/media/c.mkv', 3)
    assert any('could not be started' in n for n in xbmcgui.UI.notifications)


def test_swap_key_toggles_between_camera_and_previous_playback(service):
    P.CURRENT.update(path='/media/series.mkv', time=600.0, playing=True)
    service.service._last_camera = 'garden'
    service.notify('swap')
    assert wait_for(lambda: P.PLAYED and P.PLAYED[-1].endswith('/garden'), 3)
    P.fire('onPlayBackStopped')
    time.sleep(0.1)
    P.fire('onAVStarted')
    assert wait_for(lambda: service.service.live.showing_camera, 2)
    service.notify('swap')
    assert wait_for(lambda: P.PLAYED[-1] == '/media/series.mkv', 3)
    P.fire('onPlayBackStopped')                    # end of the camera stream must not disturb
    P.fire('onAVStarted')
    assert wait_for(lambda: P.SEEKS and abs(P.SEEKS[-1] - 598.0) < 0.1, 2)


def test_swap_with_open_pip_uses_its_camera(service):
    open_overlay(service, camera='front_door')
    service.notify('swap')
    assert wait_for(lambda: service.session is None and P.PLAYED and P.PLAYED[-1].endswith('/front_door'), 3)


# ---------------------------------------------------------------- zoom
def _drive_picker(keys_to_press):
    def run():
        if not wait_for(lambda: any(w.__class__.__name__ == '_PickerWindow' and w.shown
                                    for w in xbmcgui.UI.windows), 5):
            return
        window = [w for w in xbmcgui.UI.windows if w.__class__.__name__ == '_PickerWindow'][-1]
        time.sleep(0.6)

        class _Action(object):
            def __init__(self, action_id):
                self.action_id = action_id

            def getId(self):
                return self.action_id
        for action in keys_to_press:
            window.onAction(_Action(action))
            time.sleep(0.05)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_define_image_section_and_use_it(service):
    xbmcaddon.SETTINGS['zoom_enabled'] = True
    service.service.reload_settings()
    driver = _drive_picker([k.ACTION_SELECT_ITEM, k.ACTION_SELECT_ITEM, k.ACTION_MOVE_RIGHT,
                            k.ACTION_MOVE_RIGHT, k.ACTION_NAV_BACK])
    service.notify('roi', {'camera': 'front_door'})
    driver.join(10)
    store = service.service.regions
    assert wait_for(lambda: store.get('front_door') is not None, 3)
    region = store.get('front_door')
    assert region.zoom == 2.0 and region.cx > 0.5

    sess = open_overlay(service, camera='front_door')
    assert sess.pollers[0].transform is not None
    assert sess.view.slots[0].name.label == 'front_door · 2×'
    assert 600 <= sess.pollers[0].height <= 720          # more source pixels, at most the detect height
    assert wait_for(lambda: sess.pollers[0].state()['path'], 3)
    from PIL import Image
    width, height = Image.open(sess.pollers[0].state()['path']).size
    assert abs(width / height - 16 / 9) < 0.05
    service.key(k.ACTION_NAV_BACK)
    assert wait_for(lambda: service.session is None, 3)

    sess = open_overlay(service, camera='front_door', full=True)
    assert sess.pollers[0].transform is None


def test_zoom_off_ignores_saved_sections(service):
    service.service.regions.set('garden', __import__('resources.lib.regions', fromlist=['Region']).Region(2.0))
    sess = open_overlay(service, camera='garden')
    assert sess.pollers[0].transform is None


def test_manual_selection_asks_for_the_section(service):
    xbmcaddon.SETTINGS['zoom_enabled'] = True
    service.service.reload_settings()
    xbmcgui.UI.push(('select', 2), ('select', 0))   # camera "garden", then "Whole picture"
    service.notify('select')
    assert wait_for(service.visible, 4)
    assert service.session.cameras == ['garden'] and service.session.pollers[0].transform is None
    headings = [c[1] for c in xbmcgui.UI.calls if c[0] == 'select']
    assert headings == ['Frigate PiP – choose camera', 'garden – image section']


# ---------------------------------------------------------------- not configured / setup
@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(service_module, 'OFFER_SETUP_AFTER', 0.3)
    running = RunningService()
    yield running
    running.stop()


def test_setup_is_offered_once_after_installation(unconfigured):
    assert wait_for(lambda: xbmcgui.UI.texts('yesno'), 3)
    assert 'Set it up now?' in xbmcgui.UI.texts('yesno')[0]
    from resources.lib.common import load_state
    assert wait_for(lambda: load_state().get('setup_offered') is True, 2)   # never asked again
    time.sleep(0.5)
    assert len(xbmcgui.UI.texts('yesno')) == 1


def test_setup_offer_waits_while_something_plays(unconfigured):
    xbmc.COND[service_module.SETUP_OFFER_BLOCKERS] = True
    time.sleep(0.8)
    assert xbmcgui.UI.texts('yesno') == []
    xbmc.COND[service_module.SETUP_OFFER_BLOCKERS] = False
    assert wait_for(lambda: xbmcgui.UI.texts('yesno'), 3)


def test_commands_before_setup(unconfigured):
    assert wait_for(lambda: xbmcgui.UI.texts('yesno'), 3)      # first-run offer, declined
    unconfigured.notify('show', {'camera': 'garden'})            # e.g. Home Assistant
    assert wait_for(lambda: any('not set up yet' in n for n in xbmcgui.UI.notifications), 3)
    unconfigured.notify('select')                                # a person on the TV
    assert wait_for(lambda: len(xbmcgui.UI.texts('yesno')) == 2, 3)
    assert 'Start the setup wizard now?' in xbmcgui.UI.texts('yesno')[1]


def test_service_cleans_up_on_shutdown(configured):
    running = RunningService()
    assert wait_for(lambda: xbmcgui.Window(10000).getProperty('script.frigate.pip.running') == '1')
    running.notify('show', {'camera': 'garden'})
    assert wait_for(running.visible, 4)
    frame_dir = running.service.frame_dir
    running.stop()
    assert xbmcgui.Window(10000).getProperty('script.frigate.pip.running') == ''
    assert not os.path.exists(frame_dir)
