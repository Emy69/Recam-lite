"""Build the frozen Windows app and the distributable zip, with cx_Freeze.

    .venv\\Scripts\\python.exe build_exe.py            # release (no console)
    .venv\\Scripts\\python.exe build_exe.py --console  # debug build with a console

cx_Freeze rather than PyInstaller: PyInstaller's self-extracting bootloader
trips antivirus heuristics constantly, while a cx_Freeze build is a plain
executable next to its libraries and rarely gets flagged.

Everything lands in ONE folder, `build/`: the finished app in
`build/Recam/` and the zip next to it. If ffmpeg.exe/ffprobe.exe (plus
their DLLs for a shared build) sit in a `ffmpeg/` folder next to this
script, they are copied in so testers do not have to install anything.
"""
from __future__ import annotations

import importlib.util
import marshal
import shutil
import sys
import types
import zipfile
from pathlib import Path

from cx_Freeze import Executable, setup

from recam import __version__

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
DIST = BUILD / 'Recam'

CONSOLE = '--console' in sys.argv

# whole packages, because several of them import their internals dynamically
# (uvicorn picks protocol classes by name, engineio its async drivers, webview
# its platform backend, yt_dlp its extractors) and the module finder cannot
# see that from the imports alone
PACKAGES = ['recam', 'nicegui', 'uvicorn', 'wsproto', 'engineio', 'socketio',
            'webview', 'clr_loader', 'pythonnet', 'yt_dlp', 'httpx', 'certifi',
            'PIL', 'pystray', 'send2trash', 'rich']

EXCLUDES = ['streamlink', 'tkinter', 'unittest', 'pydoc_data', 'pip', 'setuptools']


def freeze() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    sys.argv = [sys.argv[0], 'build_exe']
    setup(
        name='Recam',
        version=__version__,
        options={'build_exe': {
            'build_exe': str(DIST),
            'packages': PACKAGES,
            'excludes': EXCLUDES,
            # keep packages as plain folders (with their data files) instead of a
            # zip: nicegui needs its static/ assets, pythonnet its runtime DLLs
            'zip_include_packages': [],
            'zip_exclude_packages': ['*'],
            'include_msvcr': True,
        }},
        executables=[Executable(
            'app.py',
            base=None if CONSOLE else 'Win32GUI',
            target_name='Recam.exe',
            icon=str(ROOT / 'recam.ico'),
        )],
    )


# Every .pyc records in co_filename the absolute path of the .py it was compiled
# from, and that is what a traceback prints. Those paths name the account and
# the folder the build ran in, which does not belong in a file other people
# download, so they are rewritten to short relative ones that still say which
# module a frame came from.
_ROOTS = (
    ('.venv/lib/site-packages/', 'site-packages/'),
    ('python312/lib/', 'python/lib/'),
)


def _neutral(path: str) -> str:
    """Map one absolute build path to something safe to ship."""
    unix = path.replace('\\', '/')
    low = unix.lower()
    for mark, prefix in _ROOTS:
        at = low.find(mark)
        if at != -1:
            return prefix + unix[at + len(mark):]
    for mark in ('/recam/', '/app.py', '/build_exe.py'):
        at = low.find(mark)
        if at != -1:
            return unix[at + 1:]
    if unix[1:3] == ':/' or unix.startswith('//'):
        return unix.rsplit('/', 1)[-1]      # last resort: just the file name
    return path


def _rewrite(code: types.CodeType) -> types.CodeType:
    """`code` with co_filename neutralised, nested code objects included."""
    consts, changed = [], False
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            sub = _rewrite(const)
            changed = changed or sub is not const
            consts.append(sub)
        else:
            consts.append(const)
    target = _neutral(code.co_filename)
    if target == code.co_filename and not changed:
        return code
    if changed:
        return code.replace(co_filename=target, co_consts=tuple(consts))
    return code.replace(co_filename=target)


def scrub_paths(root: Path) -> int:
    """Strip the build machine out of every .pyc under `root`.

    The 16-byte header is left alone: its mtime and source-size fields describe
    the original .py, which a frozen app never ships, so nothing validates them.
    """
    magic, done = importlib.util.MAGIC_NUMBER, 0
    for path in root.rglob('*.pyc'):
        data = path.read_bytes()
        if data[:4] != magic:
            continue
        original = marshal.loads(data[16:])
        code = _rewrite(original)
        if code is not original:
            path.write_bytes(data[:16] + marshal.dumps(code))
            done += 1
    return done


def main() -> None:
    freeze()

    shutil.copy2(ROOT / 'recam.ico', DIST / 'recam.ico')
    if (ROOT / 'mascot.png').exists():   # the About card's avatar
        shutil.copy2(ROOT / 'mascot.png', DIST / 'mascot.png')
    src_ffmpeg = ROOT / 'ffmpeg'
    if src_ffmpeg.is_dir():
        target = DIST / 'ffmpeg'
        target.mkdir(exist_ok=True)
        for f in src_ffmpeg.iterdir():
            if f.is_file() and f.name != 'ffplay.exe':
                shutil.copy2(f, target / f.name)
        print(f'bundled ffmpeg ({sum(1 for _ in target.iterdir())} files)')

    print(f'scrubbed build paths from {scrub_paths(DIST)} .pyc files')

    out = BUILD / f'Recam-{__version__}.zip'
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(DIST.rglob('*')):
            rel = path.relative_to(DIST)
            # a test run of the built exe leaves local state behind; never ship it
            if rel.parts[0] in ('data', 'grabaciones'):
                continue
            z.write(path, Path('Recam') / rel)
    print(f'\n{out.name}: {out.stat().st_size / 1_048_576:.1f} MB')
    print(f'app folder: {DIST}')


if __name__ == '__main__':
    main()
