# Contributing

Thank you for helping! Bug reports, translations and pull requests are welcome.

## Reporting bugs

Please use the [issue templates](https://github.com/flubshi/script.frigate.pip/issues/new/choose)
and attach the add-on's log lines:

```sh
grep script.frigate.pip /path/to/kodi.log | tail -100
```

The log never contains passwords, but please check it for host names you do not want to share.

## Project layout

```text
script.frigate.pip/            the add-on (this folder is what gets installed)
├── addon.xml
├── default.py                 entry point for RunScript / the Programs menu
├── service.py                 entry point for the background service
└── resources/
    ├── settings.xml
    ├── language/              strings.po files (en_gb = source, de_de = German)
    ├── media/                 icon, fanart
    └── lib/
        ├── frigate.py         Frigate API client, TLS pinning, server detection   (no Kodi imports)
        ├── poller.py          picture polling and stall detection                 (no Kodi imports)
        ├── keys.py            key handling while the overlay is visible           (no Kodi imports)
        ├── regions.py         zoom / image section                                (no Kodi imports)
        ├── messages.py        message catalogue and translation                   (no Kodi imports)
        ├── service.py         background service: commands, overlay sessions
        ├── overlay.py         overlay window and session
        ├── livestream.py      camera full screen and resuming the previous playback
        ├── picker.py          dialog to choose the image section
        ├── wizard.py          setup wizard
        ├── tools.py           connection test and certificate dialog
        ├── script.py          command line parsing for RunScript
        └── common.py          settings, logging, paths
tests/                         pytest suite with fake Kodi modules and a fake Frigate server
tools/build_zip.py             builds the installable ZIP
```

Everything that does not need Kodi lives in modules without Kodi imports, so it can be tested
directly.

## Development setup

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
ruff check .
```

The add-on must run on Python 3.8 (Kodi 19). CI runs the tests on 3.8 and on a current Python
version and checks the add-on with the official `kodi-addon-checker`.

### Guidelines

- Keep the add-on free of external dependencies – only the Python standard library (Pillow is
  optional and only used for zoom).
- Never let a background thread die: catch exceptions and show the problem in the overlay.
- No credentials in log messages or exceptions.
- Log messages are English. Everything the user sees goes through `tr()` and `strings.po`.
- Add a test for every bug fix.

## Translations

User-visible texts live in `script.frigate.pip/resources/language/*/strings.po`.

- `resource.language.en_gb/strings.po` is the source: `msgid` holds the English text, `msgstr` stays empty.
- Other languages copy that file and fill in `msgstr`.
- Keep placeholders such as `{camera}` unchanged and `[CR]` (line break) where it makes sense.

`tests/test_messages.py` checks that every language contains every string and that the
placeholders match.

## Releasing

1. Update the version in `script.frigate.pip/addon.xml` (including `<news>`).
2. Move the entries from *Unreleased* to a new version section in `CHANGELOG.md`.
3. Commit, then tag: `git tag v1.4.0 && git push --tags`.

The release workflow runs all checks, builds the ZIP with `tools/build_zip.py` and publishes a
GitHub release with the changelog section as description.
