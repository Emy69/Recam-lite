"""Build the frozen Windows app and the distributable zip, with cx_Freeze.

    .venv\\Scripts\\python.exe build_exe.py            # release (no console)
    .venv\\Scripts\\python.exe build_exe.py --console  # debug build with a console

cx_Freeze rather than PyInstaller on purpose: PyInstaller's self-extracting
bootloader trips antivirus heuristics constantly; a cx_Freeze build is a
plain executable next to its libraries and rarely gets flagged.

Everything lands in ONE folder, `build/`: the finished app in
`build/Recam/` and the zip next to it. If ffmpeg.exe/ffprobe.exe (plus
their DLLs for a shared build) sit in a `ffmpeg/` folder next to this
script, they are copied in so testers do not have to install anything.
"""
from __future__ import annotations

import shutil
import sys
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

    out = BUILD / f'Recam-{__version__}.zip'
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(DIST.rglob('*')):
            rel = path.relative_to(DIST)
            # a test run of the built exe leaves personal state behind; never ship it
            if rel.parts[0] in ('data', 'grabaciones'):
                continue
            z.write(path, Path('Recam') / rel)
    print(f'\n{out.name}: {out.stat().st_size / 1_048_576:.1f} MB')
    print(f'app folder: {DIST}')


if __name__ == '__main__':
    main()
