# Frigate PiP for Kodi

[![CI](https://github.com/flubshi/script.frigate.pip/actions/workflows/ci.yml/badge.svg)](https://github.com/flubshi/script.frigate.pip/actions/workflows/ci.yml)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](LICENSE)
[![Kodi 19+](https://img.shields.io/badge/Kodi-19%20%7C%2020%20%7C%2021%20%7C%2022-brightgreen.svg)](https://kodi.tv)

Show the cameras of your [Frigate NVR](https://frigate.video) as a small **picture-in-picture overlay**
on top of whatever Kodi is playing – for example when someone rings the doorbell.

- **Works with Frigate's authenticated API** (port 8971) – with certificate pinning, so your
  password is only ever sent to *your* Frigate server.
- **You see when something is wrong:** a frozen picture, a camera without signal or a Frigate that
  does not answer is shown with a red frame and a clear message – never silently.
- **Kodi stays usable:** keys step the overlay aside and work as usual; it comes back by itself.
- **Camera full screen:** switch to the live stream in the Kodi player and back to your movie or
  TV channel, at the same position.
- Several cameras at once, optional zoom into an image section, a setup wizard, English and German.

> This add-on is a community project and is not affiliated with the Frigate project.

---

## Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Setup](#setup)
4. [Showing cameras](#showing-cameras)
5. [Home Assistant](#home-assistant)
6. [Remote control keys](#remote-control-keys)
7. [What the overlay tells you](#what-the-overlay-tells-you)
8. [Camera full screen](#camera-full-screen)
9. [Zoom into an image section](#zoom-into-an-image-section)
10. [Settings](#settings)
11. [Troubleshooting](#troubleshooting)
12. [Security notes](#security-notes)
13. [Development](#development)

---

## Requirements

| | |
|---|---|
| **Kodi** | 19 (Matrix) or newer, any platform. Developed on LibreELEC with Kodi 22 (Piers). |
| **Frigate** | 0.14 or newer (authenticated port 8971). Older versions work via the unauthenticated port 5000. |
| **Network** | Kodi must reach Frigate on port **8971** (or 5000). For camera full screen also **8554** (RTSP). |
| **Zoom (optional)** | The Python library Pillow – included in LibreELEC and most Kodi builds. |

### Prepare Frigate (recommended)

Create a separate Frigate user for Kodi, so Kodi never needs your admin password:

1. In Frigate's settings, open **Users** and add a user.
2. Name it e.g. `kodi`, choose a strong password and the role **viewer**.

Port 8971 is enabled by default in the Home Assistant add-on. If you run Frigate with Docker, make
sure the port is published (`- "8971:8971"`).

---

## Installation

1. Download `script.frigate.pip-<version>.zip` from the
   [latest release](https://github.com/flubshi/script.frigate.pip/releases/latest).
2. Copy it to your Kodi device (for example via the network share or a USB stick).
3. In Kodi: **Settings → System → Add-ons** → enable **Unknown sources** (only needed once).
4. **Settings → Add-ons → Install from zip file** → select the downloaded file.

Updates are installed the same way. Your settings are kept.

---

## Setup

A few seconds after the installation Kodi asks: *"Set it up now?"* – answer **Yes** and the setup
wizard guides you through:

1. **Address** of the Frigate server, e.g. `192.168.1.10` or `frigate.local`.
   Port and HTTPS are detected automatically.
2. **Certificate:** Frigate uses a self-signed certificate. The wizard shows its fingerprint;
   after you confirm, the add-on only ever talks to exactly this certificate.
3. **Login** with the Frigate user you created above.
4. **Default camera** – used by the toggle key and when no camera is given.
5. Optionally the **RTSP credentials** for camera full screen.

At the end you can show a test picture right away.

You can start the wizard again at any time: **Add-ons → Program add-ons → Frigate PiP →
Settings → Connection → Run setup wizard**.

---

## Showing cameras

| How | What happens |
|---|---|
| **Add-ons → Program add-ons → Frigate PiP** | Choose a camera (or all cameras) |
| **Home Assistant** | e.g. on a doorbell press or a Frigate alert – see below |
| **A remote control key** | Toggle the overlay or switch to camera full screen – see below |

All commands can also be sent from scripts or other add-ons:

```text
RunScript(script.frigate.pip,camera=front_door)                       show one camera
RunScript(script.frigate.pip,camera=front_door+garden,duration=60)    two cameras for 60 s
RunScript(script.frigate.pip,action=toggle)                           show/hide (default camera)
RunScript(script.frigate.pip,action=hide)                             hide
RunScript(script.frigate.pip,action=fullscreen,camera=garden)         camera in the Kodi player
RunScript(script.frigate.pip,action=swap)                             camera <-> previous playback
RunScript(script.frigate.pip,action=roi,camera=garden)                define the zoom section
RunScript(script.frigate.pip,camera=garden,full=1)                    ignore the zoom section
RunScript(script.frigate.pip,action=setup)                            setup wizard
```

`duration=0` keeps the overlay open until you close it. Camera names are the names from your
Frigate configuration.

---

## Home Assistant

Home Assistant sends the command to Kodi through the
[Kodi integration](https://www.home-assistant.io/integrations/kodi/) (`kodi.call_method`).
The fastest way is a notification directly to the add-on's background service:

```yaml
action: kodi.call_method
target:
  entity_id: media_player.kodi
data:
  method: JSONRPC.NotifyAll
  sender: homeassistant
  message: frigatepip_show
  data:
    camera: front_door        # several cameras: "front_door+garden"
    duration: 30              # optional, 0 = until closed
```

| `message` | Effect |
|---|---|
| `frigatepip_show` | Show `camera` (with optional `duration`, `full: true` ignores the zoom section) |
| `frigatepip_hide` | Close the overlay |
| `frigatepip_toggle` | Show/hide the default camera |
| `frigatepip_fullscreen` | Show `camera` in the Kodi player (camera full screen) |
| `frigatepip_swap` | Switch between camera full screen and the previous playback |

If the overlay for the same camera is already open, another `frigatepip_show` only extends the
display time. A different camera replaces the overlay.

### Example: overlay on Frigate alerts

```yaml
alias: Kodi picture-in-picture on Frigate alert
mode: queued
triggers:
  - trigger: mqtt
    topic: frigate/reviews
conditions:
  - condition: template
    value_template: >
      {{ trigger.payload_json.type == 'new'
         and trigger.payload_json.after.severity == 'alert' }}
  - condition: not
    conditions:
      - condition: state
        entity_id: media_player.kodi
        state: "off"
actions:
  - action: kodi.call_method
    target:
      entity_id: media_player.kodi
    data:
      method: JSONRPC.NotifyAll
      sender: homeassistant
      message: frigatepip_show
      data:
        camera: "{{ trigger.payload_json.after.camera }}"
        duration: 30
```

---

## Remote control keys

Kodi lets you assign commands to keys with a *keymap*. Create the file
`userdata/keymaps/frigatepip.xml` (on LibreELEC: `/storage/.kodi/userdata/keymaps/frigatepip.xml`):

```xml
<keymap>
  <global>
    <keyboard>
      <f9>RunScript(script.frigate.pip,action=toggle)</f9>
    </keyboard>
    <remote>
      <red>RunScript(script.frigate.pip,action=toggle)</red>
      <record>RunScript(script.frigate.pip,action=swap)</record>
    </remote>
  </global>
  <FullscreenVideo>
    <remote><record>RunScript(script.frigate.pip,action=swap)</record></remote>
  </FullscreenVideo>
  <FullscreenLiveTV>
    <remote><record>RunScript(script.frigate.pip,action=swap)</record></remote>
  </FullscreenLiveTV>
</keymap>
```

Then restart Kodi (or run `kodi-send --action="Action(reloadkeymaps)"`).

- Set a **default camera** in the settings, otherwise the toggle key asks which camera to show.
- Keys used during playback (like *record*) have to be assigned in the full screen sections as well.
- With HDMI-CEC not every TV forwards every key. The colour keys almost always work. To see what
  arrives, enable debug logging in Kodi and look for `OnKey` in the log.

### Keys while the overlay is visible

Kodi treats add-on overlays as *modal*: while one is visible it receives all keys. So that Kodi
stays usable, the overlay **steps aside**:

| Key | Effect |
|---|---|
| **Back / Exit** | Close the overlay |
| **OK** during full screen video | Camera full screen (configurable) |
| Play/Pause, Stop, Volume, Mute | Work as usual – the overlay stays |
| Left/Right during full screen video | Seek – the overlay stays |
| Up/Down during full screen video | Big seek (live TV: switch channel) |
| **Any other key** | The overlay disappears and the key works as usual in Kodi |

After a few seconds without a key press (default: 4) the overlay comes back – but not while a
Kodi dialog, the on-screen display or the channel list is open. A new alarm for the same camera
brings it back after one second, the toggle key immediately.

---

## What the overlay tells you

| Frame | Status line | Meaning |
|---|---|---|
| grey | connecting … | waiting for the first picture |
| green | live · 4.8 fps | new pictures are arriving (fps = new pictures per second) |
| yellow | delayed · 2.1 s | no new picture for a moment |
| red + banner | **PICTURE FROZEN** | Frigate answers, but the picture does not change |
| red + banner | **CAMERA HAS NO SIGNAL** | Frigate receives 0 fps from the camera |
| red + banner | **NO RESPONSE** | Frigate does not answer the request |
| red + banner | **NO CONNECTION / LOGIN FAILED / CERTIFICATE REJECTED / …** | see the status line for details |

A picture only counts as new if its content changes. The timestamp that Frigate draws into the
picture (on by default) makes a stuck picture obvious as well. Every status change is written to
the Kodi log.

---

## Camera full screen

**OK** during full screen video (or `action=fullscreen`, or the swap key) closes the overlay and
plays the camera's live stream in the Kodi player. When you stop it, your movie continues at the
same position, or the previous TV channel is tuned again. If you started something else in the
meantime, nothing is resumed.

The stream comes from go2rtc, which is part of Frigate: `rtsp://<frigate>:8554/<camera>`.
If go2rtc requires credentials, enter them under **Settings → Camera full screen**. A different
stream name (e.g. a separate main stream `{camera}_main`) can be configured there as well.

---

## Zoom into an image section

Enable it under **Settings → Zoom**. When it is off, pictures are never decoded.

1. **Settings → Zoom → Define section …** and choose a camera.
2. Move the frame with the **arrow keys**, change the zoom with **OK** (1× → 1.5× → 2× → 3× → 4×),
   save with **Back**, cancel with **Menu**.

The section is saved per camera and used for every call, including Home Assistant
(`full: true` shows the whole picture). The sharpness depends on Frigate's *detect* resolution:
at 640×360, a 2× zoom leaves 320×180 pixels.

---

## Settings

| Category | Setting | Default |
|---|---|---|
| Connection | Host, port, HTTPS | – , 8971, on |
| | User name / password | – |
| | Certificate check | Pin fingerprint |
| Overlay | Position, width per camera | top right, 30 % |
| | Object boxes, timestamp | off, on |
| | Display time | 30 s (0 = until closed) |
| | Show PiP again after … s without key press | 4 s (0 = no) |
| | Key for camera full screen | OK during full screen video |
| | Polling interval | 250 ms |
| | Default camera(s) | – (ask) |
| Zoom | Enable, ask after camera selection | off, on |
| Camera full screen | RTSP host, port, user, password, stream name | Frigate host, 8554, –, –, `{camera}` |
| | Resume previous playback | on |
| Monitoring | Warning / error after … ms without a new picture | 1500 / 4000 |
| | Query Frigate statistics, notification on error | on, off |

**Connection → Test connection** checks TLS, login, camera list, pictures, statistics and RTSP and
shows a report – the best first step if something does not work.

---

## Troubleshooting

**"Login rejected" although the password is correct**
Check for characters inserted by autocorrection – phone keyboards turn `...` into `…`. The
connection test warns about this. Enter the password with the on-screen keyboard instead.

**"The certificate has changed"**
Frigate's built-in certificate is created anew whenever its container is recreated, for example
after an update or a reinstallation of the Home Assistant add-on. Compare the fingerprint on the
Frigate host before you trust the new one:

```sh
echo | openssl s_client -connect <frigate>:8971 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```

Then run **Settings → Connection → Fetch and trust certificate**. To avoid this, give Frigate a
permanent certificate (see the [Frigate TLS documentation](https://docs.frigate.video/configuration/tls)).

**"No Frigate found" in the wizard**
Is port 8971 published (Docker) or enabled in the add-on configuration (Home Assistant)? Try the
address in a browser: `https://<frigate>:8971` must show the Frigate login page.

**"Background service is not running"**
Disable and enable the add-on, or restart Kodi.

**Camera full screen does not start**
Run the connection test – it checks whether the RTSP port 8554 is reachable.

**Log file**
All messages of the add-on are tagged `[script.frigate.pip]`. On LibreELEC:

```sh
grep script.frigate.pip /storage/.kodi/temp/kodi.log | tail -50
```

Please attach these lines to bug reports (they do not contain passwords).

---

## Security notes

- Use a separate Frigate user with the **viewer** role for Kodi.
- With certificate pinning (default), the add-on only sends your credentials after the server
  has presented exactly the confirmed certificate. *No verification* has to be chosen explicitly.
- Port 5000 has no login at all. Use it only if you understand the consequences.
- Kodi stores add-on settings, including passwords, as plain text in its user data folder. Anyone
  with access to the Kodi device can read them – another reason for a dedicated viewer user.
- The login token is kept in memory only. Pictures are buffered in RAM (`/dev/shm`) where
  available and deleted when the overlay closes.

Found a vulnerability? Please read [SECURITY.md](SECURITY.md).

---

## Development

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest              # ~400 tests against a fake Frigate server and fake Kodi modules
ruff check .                   # code style
python3 tools/build_zip.py     # builds dist/script.frigate.pip-<version>.zip
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, translations and the release process.

## License

[GPL-2.0-or-later](LICENSE) © flubshi
