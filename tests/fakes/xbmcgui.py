"""Minimal stand-in for Kodi's xbmcgui module (tests only).

Dialog answers are scripted: tests push (kind, value) pairs onto UI.answers.
Every dialog call pops the next answer and checks that the kind matches; if
no answer is queued, the dialog behaves like a user pressing "cancel".
All calls are recorded in UI.calls.
"""
import threading

NOTIFICATION_INFO = 'info'
NOTIFICATION_WARNING = 'warning'
NOTIFICATION_ERROR = 'error'
INPUT_ALPHANUM = 0
ALPHANUM_HIDE_INPUT = 2

_properties = {}
CURRENT_WINDOW = [10000]


class _UI(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with getattr(self, 'lock', threading.Lock()):
            self.answers = []
            self.calls = []
            self.notifications = []
            self.windows = []

    def push(self, *answers):
        with self.lock:
            self.answers.extend(answers)

    def take(self, kind, default):
        with self.lock:
            if not self.answers:
                return default
            expected, value = self.answers.pop(0)
        if expected != kind:
            raise AssertionError('dialog %r opened, but the test expected %r' % (kind, expected))
        return value(self.calls[-1]) if callable(value) else value

    def texts(self, kind=None):
        return [c[1] for c in self.calls if kind is None or c[0] == kind]


UI = _UI()


def reset():
    UI.reset()
    _properties.clear()
    CURRENT_WINDOW[0] = 10000


def getCurrentWindowId():
    return CURRENT_WINDOW[0]


# ---------------------------------------------------------------- controls
class Control(object):
    def __init__(self, x, y, width, height):
        for value in (x, y, width, height):
            assert isinstance(value, int), 'coordinates must be int: %r' % (value,)
        self.rect = (x, y, width, height)
        self.visible = True
        self.calls = []

    def setVisible(self, visible):
        self.visible = visible
        self.calls.append(('setVisible', visible))

    def setPosition(self, x, y):
        assert isinstance(x, int) and isinstance(y, int)
        self.rect = (x, y) + self.rect[2:]

    def setWidth(self, width):
        assert isinstance(width, int)
        self.rect = self.rect[:2] + (width, self.rect[3])

    def setHeight(self, height):
        assert isinstance(height, int)
        self.rect = self.rect[:3] + (height,)


class ControlImage(Control):
    def __init__(self, x, y, width, height, filename, aspectRatio=0, colorDiffuse=None):
        Control.__init__(self, x, y, width, height)
        self.filename = filename
        self.color = colorDiffuse

    def setImage(self, filename, useCache=True):
        assert useCache is False, 'pictures must bypass the texture cache'
        self.filename = filename
        self.calls.append(('setImage', filename))

    def setColorDiffuse(self, color):
        self.color = color
        self.calls.append(('setColorDiffuse', color))


class ControlLabel(Control):
    def __init__(self, x, y, width, height, label, font=None, textColor=None, disabledColor=None,
                 alignment=0, hasPath=False, angle=0):
        Control.__init__(self, x, y, width, height)
        self.label = label
        self.color = textColor

    def setLabel(self, label='', font=None, textColor=None, *args):
        self.label = label
        if textColor:
            self.color = textColor
        self.calls.append(('setLabel', label))


# ---------------------------------------------------------------- windows
class WindowDialog(object):
    def __init__(self):
        self._closed = threading.Event()
        self.controls = []
        self.shown = False
        self.close_calls = 0
        UI.windows.append(self)

    def getWidth(self):
        return 1280

    def getHeight(self):
        return 720

    def addControl(self, control):
        self.controls.append(control)

    def addControls(self, controls):
        self.controls.extend(controls)

    def show(self):
        self.shown = True

    def doModal(self):
        # like Kodi: a close() before doModal() is lost
        self._closed.clear()
        self.shown = True
        self._closed.wait()
        self.shown = False

    def close(self):
        self.close_calls += 1
        self._closed.set()
        self.shown = False


class Window(object):
    def __init__(self, window_id):
        self.window_id = window_id

    def setProperty(self, key, value):
        _properties[(self.window_id, key)] = value

    def getProperty(self, key):
        return _properties.get((self.window_id, key), '')

    def clearProperty(self, key):
        _properties.pop((self.window_id, key), None)


class ListItem(object):
    def __init__(self, label='', label2='', path='', offscreen=False):
        self.label = label
        self.path = path

    def getVideoInfoTag(self):
        class _Tag(object):
            def setTitle(self, title):
                pass
        return _Tag()


# ---------------------------------------------------------------- dialogs
class Dialog(object):
    def notification(self, heading, message, icon=None, time=5000, sound=True):
        UI.notifications.append(message)

    def ok(self, heading, message):
        UI.calls.append(('ok', message))
        return UI.take('ok', True)

    def yesno(self, heading, message, nolabel='', yeslabel='', autoclose=0, defaultbutton=None):
        UI.calls.append(('yesno', message))
        return UI.take('yesno', False)

    def select(self, heading, items, autoclose=0, preselect=-1, useDetails=False):
        UI.calls.append(('select', heading, list(items), preselect))
        return UI.take('select', -1)

    def input(self, heading, defaultt='', type=INPUT_ALPHANUM, option=0, autoclose=0):
        UI.calls.append(('input', heading, defaultt, option))
        return UI.take('input', '')

    def textviewer(self, heading, text, usemono=False):
        UI.calls.append(('textviewer', text))


class DialogProgress(object):
    def create(self, heading, message=''):
        UI.calls.append(('progress', message))

    def update(self, percent, message=''):
        pass

    def iscanceled(self):
        return False

    def close(self):
        pass
