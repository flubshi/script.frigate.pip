#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Build the installable add-on ZIP.

    python3 tools/build_zip.py                 -> dist/script.frigate.pip-<version>.zip
    python3 tools/build_zip.py --tag v1.4.0    -> additionally checks that the tag matches addon.xml
    python3 tools/build_zip.py --notes         -> prints the CHANGELOG section of the current version

The ZIP is reproducible: files are sorted and get a fixed timestamp.
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_ID = 'script.frigate.pip'
ADDON_DIR = os.path.join(ROOT, ADDON_ID)
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', '.ruff_cache'}
EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.orig', '.rej', '~')
EXCLUDED_NAMES = {'.DS_Store', 'Thumbs.db', '.gitignore', '.gitattributes'}
TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def addon_version():
    version = ET.parse(os.path.join(ADDON_DIR, 'addon.xml')).getroot().get('version', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise SystemExit('addon.xml: invalid version %r' % version)
    return version


def collect_files():
    files = []
    for folder, dirs, names in os.walk(ADDON_DIR):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith('.'))
        for name in sorted(names):
            if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            path = os.path.join(folder, name)
            files.append((path, os.path.relpath(path, ROOT).replace(os.sep, '/')))
    return files


def build(out_dir):
    version = addon_version()
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, '%s-%s.zip' % (ADDON_ID, version))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in collect_files():
            info = zipfile.ZipInfo(name, TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(path, 'rb') as handle:
                archive.writestr(info, handle.read())
    return target


def release_notes(version):
    with open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8') as handle:
        text = handle.read()
    match = re.search(r'^## \[%s\][^\n]*\n(.*?)(?=^## \[|\Z)' % re.escape(version), text, re.S | re.M)
    if not match:
        raise SystemExit('CHANGELOG.md has no section for %s' % version)
    return match.group(1).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=os.path.join(ROOT, 'dist'), help='output folder (default: dist)')
    parser.add_argument('--tag', help='git tag that must match the add-on version, e.g. v1.4.0')
    parser.add_argument('--notes', action='store_true', help='print the release notes and exit')
    args = parser.parse_args(argv)

    version = addon_version()
    if args.tag and args.tag.lstrip('v') != version:
        raise SystemExit('Tag %s does not match addon.xml version %s' % (args.tag, version))
    if args.notes:
        print(release_notes(version))
        return 0
    release_notes(version)          # every release needs a changelog entry
    print(build(args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
