"""Release gate: what has to hold before a build goes out the door.

The rest of the suite tests behaviour. These check the things that only break
at release time and that no unit test would notice: a version bumped in one
place and forgotten in five, a platform half-enabled, a dependency the frozen
exe would not ship, a string that lost one of its two languages.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import string
import sys
from pathlib import Path

import pytest

from recam import __version__, platforms, ui_panel
from recam import config as config_mod
from recam.monitor import Monitor

from conftest import SAMPLE_URLS, disabled_platforms

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / 'recam').glob('*.py')) + [ROOT / 'app.py', ROOT / 'build_exe.py']
# Whatever documentation this build ships, found rather than listed: which
# translations exist is a moving target, and what matters is that the ones that
# are here agree with the package about the version.
DOCS = sorted(ROOT.glob('README*.md')) + sorted(ROOT.glob('TUTORIAL*.md'))

DISABLED = disabled_platforms()

# What this build records. It is spelled out here, and only here, so that
# merging a branch which enables another site lands as a conflict on this one
# line: turning a site on is a decision somebody makes, not a diff that slips
# through. The engine still carries all four end to end, so a one-word edit in
# platforms.py would otherwise ship them unannounced.
SHIPS = ('chaturbate',)


# --------------------------------------------------------------- the gate itself

def test_this_build_ships_the_sites_it_says_it_does():
    assert platforms.ENABLED_PLATFORMS == SHIPS


def test_every_url_shape_the_docs_promise_is_accepted():
    assert platforms.detect('https://chaturbate.com/emy') == ('chaturbate', 'emy')
    assert platforms.detect('chaturbate.com/emy') == ('chaturbate', 'emy')
    assert platforms.detect('https://es.chaturbate.com/emy') == ('chaturbate', 'emy')


@pytest.mark.parametrize('platform', DISABLED)
def test_a_disabled_platform_is_not_detected(platform):
    assert platforms.detect(SAMPLE_URLS[platform]) is None


@pytest.mark.parametrize('platform', DISABLED)
def test_a_disabled_platform_cannot_be_added(platform, cfg):
    monitor = Monitor(cfg, [], None)
    with pytest.raises(ValueError):
        monitor.add_streamer(SAMPLE_URLS[platform])
    assert monitor.streamers == []


@pytest.mark.parametrize('platform', DISABLED)
def test_a_saved_channel_on_a_disabled_platform_is_not_loaded(platform, isolated):
    """The gate on adding a channel does not cover a list already on disk:
    one written by a build with more sites enabled, or edited by hand. Without
    this the free build would poll and record a site it does not offer."""
    config_mod.STREAMERS_FILE.write_text(json.dumps([
        {'url': SAMPLE_URLS['chaturbate'], 'platform': 'chaturbate',
         'username': 'emy', 'auto_record': True},
        {'url': SAMPLE_URLS[platform], 'platform': platform,
         'username': 'other', 'auto_record': True},
    ]), encoding='utf-8')

    loaded = config_mod.load_streamers()

    assert [s.username for s in loaded] == ['emy']
    assert all(s.platform in platforms.ENABLED_PLATFORMS for s in loaded)


@pytest.mark.parametrize('platform', DISABLED)
def test_a_channel_this_build_ignores_survives_being_saved_over(platform, isolated):
    """Held aside, not dropped: the list belongs to whoever wrote it, and a
    build that does enable the site has to find them again."""
    parked = {'url': SAMPLE_URLS[platform], 'platform': platform,
              'username': 'other', 'auto_record': True}
    config_mod.STREAMERS_FILE.write_text(json.dumps([
        {'url': SAMPLE_URLS['chaturbate'], 'platform': 'chaturbate',
         'username': 'emy', 'auto_record': True},
        parked,
    ]), encoding='utf-8')

    loaded = config_mod.load_streamers()
    config_mod.save_streamers(loaded)          # what every add, remove and poll does

    on_disk = json.loads(config_mod.STREAMERS_FILE.read_text(encoding='utf-8'))
    assert parked in on_disk
    assert {entry['username'] for entry in on_disk} == {'emy', 'other'}
    # and the reload still hands the engine only what it can record
    assert [s.username for s in config_mod.load_streamers()] == ['emy']


def test_saving_a_list_that_was_never_loaded_invents_nothing(isolated, streamer):
    """save_streamers is called from places that never loaded; the held-aside
    list must not be a leftover from whatever ran before."""
    config_mod.save_streamers([streamer])
    on_disk = json.loads(config_mod.STREAMERS_FILE.read_text(encoding='utf-8'))
    assert [entry['username'] for entry in on_disk] == [streamer.username]


@pytest.mark.parametrize('platform', platforms.ENABLED_PLATFORMS)
def test_an_enabled_platform_is_wired_everywhere(platform):
    """Enabling a site means more than widening the tuple; these are the rest."""
    url = platforms.canonical_url(platform, 'emy')
    assert platforms.detect(url) == (platform, 'emy')
    assert platform in platforms.PLATFORM_COLORS
    assert platform in ui_panel._PLATFORM_TAG      # the tag on every tile header
    source = (ROOT / 'recam' / 'platforms.py').read_text(encoding='utf-8')
    assert f"platform == {platform!r}" in source   # probe() knows how to poll it


async def test_an_enabled_platform_can_build_a_record_command(tmp_path, monkeypatch):
    monkeypatch.setattr('recam.tools.ffmpeg_path', lambda: 'ffmpeg.exe')

    async def fake_master(username, quality):
        return '#EXTM3U\n'

    monkeypatch.setattr(platforms, 'resolve_chaturbate_master', fake_master)
    cmd = await platforms.build_record_cmd('chaturbate', 'emy', 'best',
                                           tmp_path / 'out.ts')
    assert cmd[0] == 'ffmpeg.exe'
    assert cmd[-1] == str(tmp_path / 'out.ts')


def test_a_chaturbate_room_has_a_preview_thumbnail():
    """The tiles show a live still; only Chaturbate serves one unauthenticated."""
    assert platforms.thumbnail_url('chaturbate', 'emy')


def test_no_real_channel_is_baked_into_the_source():
    """The filename-template preview needs a sample name. It once held a real
    channel from the author's own list, which then shipped inside every build.
    """
    settings = (ROOT / 'recam' / 'ui_settings.py').read_text(encoding='utf-8')
    assert "username='streamer'" in settings


# ------------------------------------------------------------ the version string

VERSION_IN_TEXT = re.compile(r'v(\d+\.\d+(?:\.\d+)?)')


def _version_mentions() -> list[tuple[Path, int, str]]:
    found = []
    for path in SOURCES + DOCS:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            found += [(path, number, m.group(1)) for m in VERSION_IN_TEXT.finditer(line)]
    return found


def test_every_version_written_by_hand_matches_the_package():
    """A release is one version number, not seven.

    The version sits hard-coded in both READMEs, both tutorials and the CLI
    banner. Bumping recam/__init__.py alone would ship a build that calls
    itself two different things.
    """
    wrong = [f'{path.relative_to(ROOT)}:{number} says v{text}'
             for path, number, text in _version_mentions() if text != __version__]
    assert not wrong, (f'these still advertise another version, __version__ is '
                       f'{__version__}: ' + '; '.join(wrong))


def test_the_version_is_a_release_number():
    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__), __version__


# -------------------------------------------------- what the frozen build ships

def _freeze_list(name: str) -> list[str]:
    """Read PACKAGES / EXCLUDES out of build_exe.py without importing cx_Freeze."""
    tree = ast.parse((ROOT / 'build_exe.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, (ast.AnnAssign, ast.Assign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in targets):
                return list(ast.literal_eval(node.value))
    raise AssertionError(f'{name} is gone from build_exe.py')


def _imported_top_level_modules() -> set[str]:
    names: set[str] = set()
    for path in SOURCES:
        if path.name == 'build_exe.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split('.')[0])
    return {n for n in names if n not in sys.stdlib_module_names}


def test_the_frozen_build_ships_every_package_the_code_imports():
    """cx_Freeze follows imports, but a package it cannot follow only shows up
    as a crash on a tester's machine. Catch it here instead."""
    # pulled in by something already listed, or imported lazily behind a guard
    covered = set(_freeze_list('PACKAGES')) | {'pystray', 'PIL', 'cx_Freeze'}
    missing = sorted(_imported_top_level_modules() - covered)
    assert not missing, f'not in build_exe.PACKAGES: {missing}'


def test_nothing_excluded_from_the_build_is_imported():
    """An excluded module that some module imports unconditionally means the
    exe dies on launch."""
    # streamlink is run as a subprocess, never imported, so excluding it is safe
    excluded = set(_freeze_list('EXCLUDES')) - {'streamlink'}
    clash = sorted(excluded & _imported_top_level_modules())
    assert not clash, f'excluded from the build but imported: {clash}'


# --------------------------------------------------------------- both languages

def _t_calls() -> list[tuple[Path, ast.Call]]:
    calls = []
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == 't':
                calls.append((path, node))
    return calls


def test_there_is_something_to_translate():
    assert len(_t_calls()) > 200


def test_every_translated_string_carries_both_languages():
    bad = [f'{path.relative_to(ROOT)}:{call.lineno}'
           for path, call in _t_calls()
           if len(call.args) != 2
           or not all(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                      and arg.value.strip() for arg in call.args)]
    assert not bad, 't() needs two non-empty literals: ' + '; '.join(bad)


def _placeholders(text: str) -> list[str]:
    return sorted(name or str(i) for i, (_, name, _, _)
                  in enumerate(string.Formatter().parse(text)) if name is not None)


def test_the_two_languages_take_the_same_format_arguments():
    """A .format() placeholder dropped from the Spanish half throws at runtime,
    and only for Spanish users: exactly the bug nobody catches in testing."""
    bad = []
    for path, call in _t_calls():
        if len(call.args) != 2 or not all(isinstance(a, ast.Constant) and
                                          isinstance(a.value, str) for a in call.args):
            continue
        english, spanish = (arg.value for arg in call.args)
        if _placeholders(english) != _placeholders(spanish):
            bad.append(f'{path.relative_to(ROOT)}:{call.lineno} '
                       f'{_placeholders(english)} vs {_placeholders(spanish)}')
    assert not bad, 'placeholders differ between languages: ' + '; '.join(bad)


# -------------------------------------------------------------- nothing left over

def test_no_debugger_or_scratch_code_survived():
    leftovers = []
    for path in SOURCES:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if line.strip().startswith('#'):
                continue
            if re.search(r'\bbreakpoint\s*\(|\bimport pdb\b|pdb\.set_trace', line):
                leftovers.append(f'{path.relative_to(ROOT)}:{number}')
    assert not leftovers, leftovers


def test_everything_the_release_ships_is_present():
    for path in (ROOT / 'README.md', ROOT / 'recam.ico', ROOT / 'requirements.txt',
                 ROOT / 'app.py', ROOT / 'build_exe.py'):
        assert path.exists(), path
    assert DOCS, 'the build ships no documentation at all'

def test_the_build_does_not_ship_the_builders_paths():
    """A .pyc keeps the absolute path of its source in co_filename, so a plain
    freeze hands every downloader the account name and folder layout of whoever
    built it. build_exe.scrub_paths rewrites them; this checks it actually does.
    """
    spec = importlib.util.spec_from_file_location('_build_exe', ROOT / 'build_exe.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_build_exe'] = module
    try:
        spec.loader.exec_module(module)
    except ImportError:                      # cx_Freeze is not a test dependency
        pytest.skip('cx_Freeze not installed')
    finally:
        sys.modules.pop('_build_exe', None)

    for before, after in [
        ('C:\\Users\\someone\\AppData\\Local\\Programs\\Python\\'
         'Python312\\Lib\\asyncio\\base_events.py',
         'python/lib/asyncio/base_events.py'),
        ('D:\\work\\proj\\.venv\\Lib\\site-packages\\nicegui\\ui.py',
         'site-packages/nicegui/ui.py'),
        ('C:\\somewhere\\checkout\\recam\\platforms.py',
         'recam/platforms.py'),
        ('C:\\somewhere\\checkout\\app.py', 'app.py'),
    ]:
        assert module._neutral(before) == after

    # whatever the shape, nothing absolute may survive
    for path in ('C:\\Users\\someone\\odd\\place\\mod.py',
                 '\\\\server\\share\\mod.py'):
        out = module._neutral(path)
        assert ':' not in out and not out.startswith('//'), out
