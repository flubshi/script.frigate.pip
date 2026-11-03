"""Minimal stand-in for Kodi's xbmcvfs module (tests only)."""
import tempfile


def translatePath(path):
    return path.replace('special://temp', tempfile.gettempdir())
