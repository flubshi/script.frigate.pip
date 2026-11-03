"""Minimal stand-in for Kodi's xbmc module (tests only)."""
import json
import threading
import time

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4

LOG = []
BUILTINS = []
JSONRPC = []
INFO = {}
COND = {}
IDLE = [0]
_abort = threading.Event()


def reset():
    del LOG[:]
    del BUILTINS[:]
    del JSONRPC[:]
    INFO.clear()
    COND.clear()
    IDLE[0] = 0
    _abort.clear()
    Player.reset()


def log(msg, level=LOGDEBUG):
    LOG.append((level, msg))


def executebuiltin(cmd, wait=False):
    BUILTINS.append(cmd)


def executeJSONRPC(request):
    JSONRPC.append(json.loads(request))
    return '{"id":1,"jsonrpc":"2.0","result":"OK"}'


def getInfoLabel(name):
    defaults = {'System.ScreenWidth': '1920', 'System.ScreenHeight': '1080'}
    return INFO.get(name, defaults.get(name, ''))


def getCondVisibility(condition):
    return bool(COND.get(condition, False))


def getGlobalIdleTime():
    return IDLE[0]


def sleep(ms):
    time.sleep(ms / 1000.0)


class Monitor(object):
    def abortRequested(self):
        return _abort.is_set()

    def waitForAbort(self, timeout=None):
        return _abort.wait(timeout)


class Player(object):
    """Fake player: play() 'starts' playback, tests fire the callbacks."""

    CURRENT = {'path': None, 'time': 0.0, 'playing': False}
    INSTANCES = []
    PLAYED = []
    SEEKS = []

    @classmethod
    def reset(cls):
        cls.CURRENT.update(path=None, time=0.0, playing=False)
        del cls.INSTANCES[:]
        del cls.PLAYED[:]
        del cls.SEEKS[:]

    def __init__(self):
        Player.INSTANCES.append(self)

    def isPlaying(self):
        return Player.CURRENT['playing']

    def getPlayingFile(self):
        if not Player.CURRENT['playing']:
            raise RuntimeError('Kodi is not playing any file')
        return Player.CURRENT['path']

    def getTime(self):
        return Player.CURRENT['time']

    def seekTime(self, seconds):
        Player.SEEKS.append(seconds)
        Player.CURRENT['time'] = seconds

    def stop(self):
        Player.CURRENT['playing'] = False

    def play(self, item=None, listitem=None, windowed=False, startpos=-1):
        Player.PLAYED.append(item)
        Player.CURRENT.update(path=item, time=0.0, playing=True)

    @classmethod
    def fire(cls, name):
        for player in list(cls.INSTANCES):
            getattr(player, name)()

    def onAVStarted(self):
        pass

    def onPlayBackStopped(self):
        pass

    def onPlayBackEnded(self):
        pass

    def onPlayBackError(self):
        pass
