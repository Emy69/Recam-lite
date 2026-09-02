"""Build the frozen Windows app and the distributable zip.

    .venv\\Scripts\\python.exe build_exe.py

Everything lands in ONE folder, `build/`: the finished app in
`build/RecordBate/` and the zip next to it. PyInstaller's intermediate
files (which embed this machine's absolute paths in analysis reports) go to
a temp subfolder that is DELETED once the build succeeds — nothing personal
sticks around, let alone ships.

Wraps the PyInstaller invocation nicegui-pack would generate, plus what this
app needs on top: no streamlink (the Chaturbate-only build never launches
it), package metadata for the versions shown in Settings, and the icon next
to the executable where the app looks for it. If ffmpeg.exe/ffprobe.exe sit
in a `ffmpeg/` folder next to this script, they are copied into the build so
testers do not have to install anything.
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from recordbate import __version__

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
WORK = BUILD / 'tmp'                 # PyInstaller scratch; removed at the end
DIST = BUILD / 'RecordBate'          # the finished app
NICEGUI_PATH = ROOT / '.venv' / 'Lib' / 'site-packages' / 'nicegui'


def run(cmd: list[str]) -> None:
    print('>', ' '.join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    run([str(ROOT / '.venv' / 'Scripts' / 'pyinstaller.exe'),
         '--noconfirm', '--clean',
         '--name', 'RecordBate',
         '--windowed', '--onedir',
         '--icon', str(ROOT / 'recordbate.ico'),
         '--workpath', str(WORK),
         '--specpath', str(WORK),
         '--distpath', str(BUILD),
         '--add-data', f'{NICEGUI_PATH};nicegui',
         '--copy-metadata', 'nicegui',
         '--copy-metadata', 'yt-dlp',
         '--exclude-module', 'streamlink',
         'app.py'])

    shutil.copy2(ROOT / 'recordbate.ico', DIST / 'recordbate.ico')
    # ffmpeg may be a shared build (small exes plus their DLLs); ship the whole
    # folder so the exes find their libraries next to themselves
    src_ffmpeg = ROOT / 'ffmpeg'
    if src_ffmpeg.is_dir():
        target = DIST / 'ffmpeg'
        target.mkdir(exist_ok=True)
        for f in src_ffmpeg.iterdir():
            if f.is_file() and f.name != 'ffplay.exe':
                shutil.copy2(f, target / f.name)
        print(f'bundled ffmpeg ({sum(1 for _ in target.iterdir())} files)')

    out = BUILD / f'RecordBate-{__version__}.zip'
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(DIST.rglob('*')):
            rel = path.relative_to(DIST)
            # a test run of the built exe leaves personal state behind; never ship it
            if rel.parts[0] in ('data', 'grabaciones'):
                continue
            z.write(path, Path('RecordBate') / rel)

    # the analysis reports in the work dir list this machine's absolute paths;
    # wipe them so a finished build leaves only the app and its zip behind
    shutil.rmtree(WORK, ignore_errors=True)
    print(f'\n{out.name}: {out.stat().st_size / 1_048_576:.1f} MB')
    print(f'app folder: {DIST}')


if __name__ == '__main__':
    main()
