# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-11-03

First public release.

### Added
- Picture-in-picture overlay for one or several Frigate cameras, triggered from Home Assistant,
  a remote control key or the add-on menu.
- Login via Frigate's authenticated API (port 8971) with certificate pinning; credentials are
  only sent after the certificate has been confirmed.
- Visible stall detection: frozen picture, camera without signal, no response, connection,
  login and certificate errors.
- The overlay steps aside on key presses so Kodi stays fully usable, and comes back after a
  short idle time.
- Camera full screen (RTSP via go2rtc) with resuming the previous movie or TV channel, and a
  swap key to switch back and forth.
- Optional zoom into a saved image section per camera.
- Setup wizard with automatic port and HTTPS detection, connection test and certificate dialog.
- English user interface with German translation.

## Before 1.4.0

Unreleased development versions: overlay with stall detection, certificate pinning, several
cameras, zoom into an image section, camera full screen with resume, swap key.

[Unreleased]: https://github.com/flubshi/script.frigate.pip/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/flubshi/script.frigate.pip/releases/tag/v1.4.0
