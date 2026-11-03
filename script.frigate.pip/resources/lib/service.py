# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Background service.

Keeps the HTTP session (token in memory only) and the camera list warm and
receives commands as JSON-RPC notifications:

    JSONRPC.NotifyAll  sender=<any>  message=frigatepip_show  data={"camera": "front_door", "duration": 30}

Further messages: frigatepip_hide, frigatepip_toggle, frigatepip_select,
frigatepip_fullscreen (camera in the Kodi player), frigatepip_swap (camera <->
previous playback, e.g. for a remote key), frigatepip_roi (define the image
section) and frigatepip_setup (setup wizard).

All dialogs and the overlay run in the main thread of the service.
"""
import json
import os
import queue
import shutil
import threading
import time

import xbmc
import xbmcgui
import xbmcvfs

from .common import (
    ADDON_ID,
    HOME_WINDOW,
    MSG_PREFIX,
    PROP_RUNNING,
    Settings,
    load_state,
    log,
    log_debug,
    log_exception,
    log_warning,
    make_client,
    notify,
    parse_duration,
    profile_path,
    save_state,
    split_cameras,
    tr,
    write_settings,
)
from .frigate import TLS_PIN, FrigateError, camera_meta, describe_error, fetch_fingerprint, format_fp
from .livestream import LiveStream
from .overlay import OverlaySession
from .regions import Region, RegionStore, pil_available, zoom_label

META_MAX_AGE = 600.0      # s, the camera list counts as fresh for this long
WARM_INTERVAL = 1800.0    # s, interval for refreshing login and camera list in the background
WARM_DELAY = 15.0         # s after start
STALE_AFTER = 30.0        # s, older show commands in the queue are dropped
OFFER_SETUP_AFTER = 8.0   # s after start, offer the setup wizard if nothing is configured
TEST_PICTURE_SECONDS = 15

# While one of these is open, the PiP does not come back (it would take away the keys)
RESHOW_BLOCKERS = ('System.HasModalDialog | Window.IsVisible(videoosd) | Window.IsVisible(musicosd) | '
                   'Window.IsVisible(pvrosdchannels) | Window.IsVisible(pvrchannelguide) | '
                   'Window.IsVisible(osdvideosettings) | Window.IsVisible(osdaudiosettings) | '
                   'Window.IsVisible(osdsubtitlesettings)')
# The setup offer waits until Kodi is idle on its home screen or in a menu
SETUP_OFFER_BLOCKERS = ('System.HasModalDialog | Player.HasMedia | System.ScreenSaverActive | '
                        'Window.IsActive(startup)')

# Commands a person triggers on the TV (not Home Assistant); those may open dialogs
INTERACTIVE = ('select', 'toggle', 'swap', 'roi', 'setup')


def _truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class Command(object):
    def __init__(self, action, cameras=None, duration=None, full=False):
        self.action = action
        self.cameras = cameras or []
        self.duration = duration
        self.full = bool(full)
        self.created = time.monotonic()

    @classmethod
    def from_payload(cls, action, payload):
        if isinstance(payload, str):
            payload = {'camera': payload}
        if not isinstance(payload, dict):
            payload = {}
        cams = split_cameras(payload.get('camera') or payload.get('cameras') or '')
        return cls(action, cams, parse_duration(payload.get('duration')), _truthy(payload.get('full', '')))

    def __repr__(self):
        return 'Command(%s, %s, %s%s)' % (self.action, self.cameras, self.duration,
                                          ', whole picture' if self.full else '')


class PipMonitor(xbmc.Monitor):
    def __init__(self, service):
        xbmc.Monitor.__init__(self)
        self._service = service

    def onNotification(self, sender, method, data):
        prefix = 'Other.' + MSG_PREFIX
        if not method.startswith(prefix):
            return
        action = method[len(prefix):]
        try:
            payload = json.loads(data) if data else {}
        except ValueError:
            payload = {}
        log('Command "%s" from %s: %s' % (action, sender, payload))
        try:
            self._service.on_command(Command.from_payload(action, payload))
        except Exception:
            log_exception('Could not handle command')

    def onSettingsChanged(self):
        try:
            self._service.reload_settings()
        except Exception:
            log_exception('Could not reload settings')


def prepare_frame_dir():
    """Folder for the pictures: in RAM (/dev/shm) if possible, else Kodi's temp folder."""
    base = '/dev/shm'
    if os.path.isdir(base) and os.access(base, os.W_OK):
        path = os.path.join(base, ADDON_ID)
    else:
        path = os.path.join(xbmcvfs.translatePath('special://temp'), ADDON_ID)
    shutil.rmtree(path, ignore_errors=True)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        log_exception('Could not create picture folder %s' % path)
    return path


class PipService(object):
    def __init__(self):
        self.monitor = PipMonitor(self)
        self.queue = queue.Queue()
        self.session = None
        self._stop = threading.Event()
        self._client_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._meta = None
        self._meta_time = 0.0
        self._trust_declined = None
        self.settings = Settings()
        self.client = make_client(self.settings)
        self._conn_key = self.settings.connection_key()
        self.frame_dir = prepare_frame_dir()
        self.regions = RegionStore(profile_path('regions.json'))
        self.live = LiveStream()
        self._pil_warned = False
        self._last_camera = None
        self._started = time.monotonic()
        self._setup_offer_pending = not self.settings.configured and not load_state().get('setup_offered')

    # ------------------------------------------------------------ settings
    def _current(self):
        with self._client_lock:
            return self.settings, self.client

    def _session_active(self):
        sess = self.session
        return sess is not None and sess.alive

    def reload_settings(self):
        new = Settings()
        old_client = None
        with self._client_lock:
            self.settings = new
            key = new.connection_key()
            if key != self._conn_key:
                old_client = self.client
                self.client = make_client(new)
                self._conn_key = key
                with self._meta_lock:
                    self._meta = None
                    self._meta_time = 0.0
        if old_client is not None:
            log('Connection settings changed - new session to %s' % self.client.describe())
            if not self._session_active():
                old_client.close_all()
        else:
            log_debug('Settings reloaded')

    # ------------------------------------------------------------ main loop
    def run(self):
        log('Service started (picture buffer: %s)' % self.frame_dir)
        home = xbmcgui.Window(HOME_WINDOW)
        home.setProperty(PROP_RUNNING, '1')
        warm = threading.Thread(target=self._keep_warm, name='frigatepip-warm')
        warm.daemon = True
        warm.start()
        try:
            while not self.monitor.abortRequested():
                try:
                    cmd = self.queue.get_nowait()
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    try:
                        self._execute(cmd)
                    except Exception:
                        log_exception('Command %r failed' % cmd)
                sess = self.session
                if sess is not None and not sess.alive:
                    self.session = None
                elif sess is not None and sess.hidden:
                    try:
                        due = sess.reshow_due(xbmc.getGlobalIdleTime(),
                                              xbmc.getCondVisibility(RESHOW_BLOCKERS))
                    except Exception:
                        due = False
                    if due:
                        self._present(sess)
                try:
                    self.live.tick()
                except Exception:
                    log_exception('Camera stream control failed')
                if self._setup_offer_pending:
                    self._maybe_offer_setup()
                if self.monitor.waitForAbort(0.1):
                    break
        finally:
            self._stop.set()
            home.clearProperty(PROP_RUNNING)
            sess = self.session
            if sess is not None:
                sess.request_close()
                sess.join(5.0)
            try:
                self.client.close_all()
            except Exception:
                pass
            shutil.rmtree(self.frame_dir, ignore_errors=True)
            log('Service stopped')

    # ------------------------------------------------------------ setup wizard
    def _maybe_offer_setup(self):
        """Offer the setup wizard once, shortly after installation."""
        if self.settings.configured:
            self._setup_offer_pending = False
            return
        if time.monotonic() - self._started < OFFER_SETUP_AFTER or self._session_active():
            return
        try:
            busy = xbmc.getCondVisibility(SETUP_OFFER_BLOCKERS)
        except Exception:
            busy = True
        if busy:
            return
        self._setup_offer_pending = False
        save_state(setup_offered=True)
        if xbmcgui.Dialog().yesno('Frigate PiP', tr('wiz_offer')):
            self._run_setup()

    def _run_setup(self):
        sess = self.session
        if sess is not None and sess.alive:
            sess.request_close()
            sess.join(5.0)
        from .wizard import SetupWizard
        wizard = SetupWizard(Settings())
        values = wizard.run()
        if values is None:
            log('Setup wizard cancelled')
            notify(tr('wiz_cancelled'))
            return False
        write_settings(values)
        save_state(setup_offered=True)
        self._trust_declined = None
        self.reload_settings()
        log('Setup wizard saved the configuration')
        camera = values.get('default_camera') or (wizard.cameras[0] if wizard.cameras else '')
        if camera and xbmcgui.Dialog().yesno('Frigate PiP', tr('wiz_test_now', camera=camera)):
            self._show([camera], TEST_PICTURE_SECONDS)
        else:
            notify(tr('wiz_saved'))
        return True

    def _offer_setup_now(self):
        """Not configured yet: for commands from the TV, ask whether to start the wizard."""
        if xbmcgui.Dialog().yesno('Frigate PiP', tr('not_configured_ask')):
            self._run_setup()

    # ------------------------------------------------------------ commands
    def on_command(self, cmd):
        """Called from onNotification (main thread). Acts immediately while an overlay is open."""
        sess = self.session
        active = sess is not None and sess.alive and not sess.closing
        if active:
            if cmd.action == 'hide':
                sess.request_close()
                return
            if cmd.action == 'toggle':
                if sess.hidden:
                    sess.show_soon(0)          # hidden -> show again right away
                else:
                    sess.request_close()
                return
            if cmd.action in ('fullscreen', 'swap'):
                if sess.visible:
                    sess.request_switch(cmd.cameras[0] if cmd.cameras else None)
                    return
                sess.request_close()           # hidden: end the session, full screen via the queue
            elif cmd.action == 'show':
                cams = cmd.cameras or self.settings.default_cameras
                if not cams or sess.same_cameras(cams):
                    duration = self.settings.duration if cmd.duration is None else cmd.duration
                    sess.extend(duration)
                    if sess.hidden:
                        sess.show_soon(1.0)    # new alarm: show once no key was pressed for 1 s
                    return
                sess.request_close()           # other camera(s): close the old overlay, open a new one
            elif cmd.action == 'setup':
                sess.request_close()
        elif cmd.action == 'hide':
            return
        self.queue.put(cmd)

    def _execute(self, cmd):
        if cmd.action == 'show' and time.monotonic() - cmd.created > STALE_AFTER:
            log('Dropped outdated show command: %r' % cmd)
            return
        if cmd.action == 'hide':
            if self.session is not None:
                self.session.request_close()
            return
        if cmd.action == 'setup':
            self._run_setup()
            return
        if not self.settings.configured:
            if cmd.action in INTERACTIVE:
                self._offer_setup_now()
            else:
                log_warning('Command "%s" ignored: the add-on is not set up yet' % cmd.action)
                notify(tr('not_configured'), xbmcgui.NOTIFICATION_WARNING)
            return
        if cmd.action == 'fullscreen':
            cams = cmd.cameras or self.settings.default_cameras or self._choose_cameras(allow_all=False)
            if cams:
                self._start_live(cams[0])
            return
        if cmd.action == 'swap':
            # swap key: camera <-> previous playback
            if self.live.showing_camera:
                log('Swap key: back to the previous playback')
                self.live.back()
                return
            cams = (cmd.cameras or self.settings.default_cameras
                    or ([self._last_camera] if self._last_camera else [])
                    or self._choose_cameras(allow_all=False))
            if cams:
                log('Swap key: camera %s in full screen' % cams[0])
                self._start_live(cams[0])
            return
        if cmd.action == 'roi':
            cams = cmd.cameras or self._choose_cameras(allow_all=False)
            if cams:
                self._edit_region(cams[0])
            return
        if cmd.action == 'select':
            cams = self._choose_cameras()
            if not cams:
                return
            override = None
            if self._zoom_ready() and self.settings.ask_region and len(cams) == 1:
                choice = self._choose_region(cams[0])
                if choice is False:
                    return
                override = {cams[0]: choice}
            self._show(cams, cmd.duration, override=override)
            return
        if cmd.action in ('show', 'toggle'):
            cams = cmd.cameras or self.settings.default_cameras or self._choose_cameras()
            if cams:
                self._show(cams, cmd.duration, full=cmd.full)
            return
        log_warning('Unknown command: %s' % cmd.action)

    def _prepare(self):
        """Common checks. Returns (settings, client) or None."""
        settings, _client = self._current()
        if not settings.configured:
            notify(tr('not_configured'), xbmcgui.NOTIFICATION_WARNING)
            return None
        if not self._ensure_trusted(settings):
            return None
        return self._current()

    def _zoom_ready(self):
        if not self.settings.zoom_enabled:
            return False
        if pil_available():
            return True
        if not self._pil_warned:
            self._pil_warned = True
            notify(tr('zoom_needs_pillow'), xbmcgui.NOTIFICATION_WARNING)
            log_warning('Zoom is enabled, but Pillow (PIL) is not installed')
        return False

    def _regions_for(self, cameras, full=False, override=None):
        if full or not self._zoom_ready():
            return {}
        result = {}
        for cam in cameras:
            if override is not None and cam in override:
                region = override[cam]
            else:
                region = self.regions.get(cam)
            if region is not None and not region.is_full:
                result[cam] = region
        return result

    def _show(self, cameras, duration, full=False, override=None):
        prepared = self._prepare()
        if prepared is None:
            return
        settings, client = prepared

        old = self.session
        if old is not None and old.alive:
            old.request_close()
            old.join(5.0)

        meta = self._get_meta(client, cameras)
        if meta:
            unknown = [c for c in cameras if c not in meta]
            if unknown:
                notify(tr('unknown_cameras', cameras=', '.join(unknown)), xbmcgui.NOTIFICATION_WARNING)
                log_warning('Unknown camera(s) %s, known are: %s' % (unknown, ', '.join(sorted(meta))))
            cameras = [c for c in cameras if c in meta]
            if not cameras:
                return

        duration = settings.duration if duration is None else duration
        regions = self._regions_for(cameras, full, override)
        sess = OverlaySession(client, cameras, meta or {}, settings, duration,
                              self.frame_dir, self.monitor, regions)
        self.session = sess
        self._last_camera = cameras[0]
        sess.start()
        self._present(sess)

    def _present(self, sess):
        """Show the overlay window (blocks until it is closed or hidden)."""
        try:
            fullscreen = xbmcgui.getCurrentWindowId() == 12005
        except Exception:
            fullscreen = False
        view = sess.make_view(fullscreen)
        if view is None:
            return
        try:
            view.window.doModal()
        except Exception:
            log_exception('Overlay window failed')
        finally:
            sess.view_closed(view)
        outcome = sess.take_outcome()
        if sess.closing:
            sess.join(15.0)
            if not sess.alive and self.session is sess:
                self.session = None
        self._after_overlay(outcome)

    def _after_overlay(self, outcome):
        if not outcome:
            return
        kind, arg = outcome[0], outcome[1]
        if kind == 'switch':
            self._start_live(arg)
        elif kind == 'redispatch':
            guard = outcome[2] if len(outcome) > 2 else None
            if guard is not None:
                # Has Kodi switched the channel itself already? Then do not switch again.
                xbmc.sleep(1200)
                label, before = guard
                if xbmc.getInfoLabel(label) != before:
                    log_debug('Kodi switched by itself - "%s" not sent again' % arg)
                    return
            else:
                xbmc.sleep(50)
            # The PiP is gone - now the key goes to Kodi as usual (with focus highlighting etc.)
            xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Input.ExecuteAction',
                                            'params': {'action': arg}}))
            log_debug('Key "%s" passed on to Kodi' % arg)

    def _start_live(self, camera):
        settings, _client = self._current()
        if not (settings.rtsp_host or settings.host):
            notify(tr('not_configured'), xbmcgui.NOTIFICATION_WARNING)
            return
        sess = self.session
        if sess is not None and sess.alive:
            sess.request_close()
            sess.join(5.0)
        self._last_camera = camera
        self.live.start(camera, settings.rtsp_url(camera), settings.rtsp_url(camera, with_credentials=False),
                        settings.resume_after)

    # ------------------------------------------------------------ image section
    def _choose_region(self, camera):
        """Returns a Region, None (whole picture) or False (cancelled)."""
        saved = self.regions.get(camera)
        items, values = [], []
        if saved is not None:
            items.append(tr('region_saved_option', zoom=zoom_label(saved.zoom)))
            values.append(saved)
        items.append(tr('region_full_option'))
        values.append(None)
        items.append(tr('region_new_option'))
        values.append('edit')
        index = xbmcgui.Dialog().select(tr('region_heading', camera=camera), items)
        if index < 0:
            return False
        value = values[index]
        if value == 'edit':
            region = self._edit_region(camera)
            return False if region is False else region
        return value

    def _edit_region(self, camera):
        """Open the picker and save the result. Returns Region/None, or False if cancelled."""
        if not self.settings.zoom_enabled:
            notify(tr('zoom_disabled'), xbmcgui.NOTIFICATION_WARNING)
            return False
        if not self._zoom_ready():
            return False
        prepared = self._prepare()
        if prepared is None:
            return False
        _settings, client = prepared
        meta = self._get_meta(client, [camera]) or {}
        if meta and camera not in meta:
            notify(tr('unknown_cameras', cameras=camera), xbmcgui.NOTIFICATION_WARNING)
            return False
        from .picker import RegionPicker
        picker = RegionPicker(client, camera, meta, self.frame_dir, self.regions.get(camera) or Region())
        result = picker.run()
        if result is None:
            log('Image section for %s: cancelled' % camera)
            return False
        try:
            self.regions.set(camera, result)
        except OSError:
            log_exception('Could not save the image section')
        log('Image section for %s: %s' % (camera, 'whole picture' if result.is_full else result.to_dict()))
        if result.is_full:
            notify(tr('region_saved_full', camera=camera))
        else:
            notify(tr('region_saved_zoom', camera=camera, zoom=zoom_label(result.zoom)))
        return None if result.is_full else result

    def _choose_cameras(self, allow_all=True):
        prepared = self._prepare()
        if prepared is None:
            return []
        _settings, client = prepared
        meta = self._get_meta(client, [], force=True)
        if not meta:
            notify(tr('camera_list_unavailable'), xbmcgui.NOTIFICATION_ERROR)
            return []
        names = sorted(n for n, m in meta.items() if m.get('enabled', True))
        if not names:
            notify(tr('no_active_cameras'), xbmcgui.NOTIFICATION_WARNING)
            return []
        with_all = allow_all and len(names) > 1
        items = ([tr('all_cameras', count=len(names))] + names) if with_all else list(names)
        index = xbmcgui.Dialog().select(tr('choose_camera'), items)
        if index < 0:
            return []
        if with_all:
            return names if index == 0 else [names[index - 1]]
        return [names[index]]

    # ------------------------------------------------------------ camera list
    def _get_meta(self, client, need, force=False):
        with self._meta_lock:
            meta, stamp = self._meta, self._meta_time
        fresh = meta is not None and (time.monotonic() - stamp) < META_MAX_AGE
        if meta is not None and not force and all(c in meta for c in need):
            return meta
        if meta is not None and force and fresh:
            return meta
        try:
            meta = camera_meta(client.config())
        except FrigateError as exc:
            log_warning('Camera list not available: %s' % exc.log_text)
            return meta    # possibly outdated or None -> the overlay shows the error
        except Exception:
            log_exception('Camera list not available')
            return meta
        with self._meta_lock:
            self._meta = meta
            self._meta_time = time.monotonic()
        return meta

    def _keep_warm(self):
        if self._stop.wait(WARM_DELAY):
            return
        while not self._stop.is_set():
            settings, client = self._current()
            pinned_ok = (not settings.https) or settings.tls_mode != TLS_PIN or settings.fingerprint
            if settings.configured and pinned_ok:
                try:
                    meta = camera_meta(client.config())
                    with self._meta_lock:
                        self._meta = meta
                        self._meta_time = time.monotonic()
                    log_debug('Camera list refreshed: %s' % ', '.join(sorted(meta)))
                except FrigateError as exc:
                    log_debug('Background refresh failed: %s' % exc.log_text)
                except Exception:
                    log_exception('Background refresh failed')
            if self._stop.wait(WARM_INTERVAL):
                return

    # ------------------------------------------------------------ certificate
    def _ensure_trusted(self, settings):
        if not settings.https or settings.tls_mode != TLS_PIN or settings.fingerprint:
            return True
        try:
            fp = fetch_fingerprint(settings.host, settings.port)
        except FrigateError as exc:
            log_warning('Frigate not reachable: %s' % describe_error(exc))
            notify(tr('frigate_unreachable', error=str(exc)), xbmcgui.NOTIFICATION_ERROR)
            return False
        if self._trust_declined == fp:
            notify(tr('cert_not_confirmed'), xbmcgui.NOTIFICATION_WARNING)
            return False
        ok = xbmcgui.Dialog().yesno(tr('cert_confirm_heading'),
                                    tr('cert_confirm_text', host=settings.host, port=settings.port,
                                       fingerprint=format_fp(fp)))
        if not ok:
            self._trust_declined = fp
            return False
        write_settings({'tls_fingerprint': format_fp(fp)})
        log('Certificate %s stored' % format_fp(fp))
        self.reload_settings()
        return True
