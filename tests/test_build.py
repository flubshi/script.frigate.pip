"""Release packaging."""
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'tools', 'build_zip.py')


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, check=False)


def test_zip_contains_the_addon_only():
    out = tempfile.mkdtemp()
    result = run('--out', out)
    assert result.returncode == 0, result.stderr
    path = result.stdout.strip()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert 'script.frigate.pip/addon.xml' in names
    assert 'script.frigate.pip/resources/language/resource.language.de_de/strings.po' in names
    assert all(name.startswith('script.frigate.pip/') for name in names)
    assert not [n for n in names if '__pycache__' in n or n.endswith('.pyc') or '/tests/' in n]


def test_zip_is_reproducible():
    first = run('--out', tempfile.mkdtemp()).stdout.strip()
    second = run('--out', tempfile.mkdtemp()).stdout.strip()
    with open(first, 'rb') as a, open(second, 'rb') as b:
        assert a.read() == b.read()


def test_tag_must_match_version():
    assert run('--tag', 'v0.0.1', '--out', tempfile.mkdtemp()).returncode != 0


def test_release_notes_exist_for_the_current_version():
    result = run('--notes')
    assert result.returncode == 0 and result.stdout.strip()
