from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from ctypes import wintypes
from datetime import datetime
from functools import lru_cache
from importlib import metadata
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

IS_FROZEN = bool(getattr(sys, 'frozen', False))
_APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else None

_JOB = None   # None = not tried yet, False = unavailable, (kernel32, handle) = ready


def _kill_on_close_job():
    """Windows job object that kills its members when the app's handle closes.

    Without it a hard exit (crash, taskkill, pulled power) leaves streamlink/ffmpeg
    recording forever: orphan files keep growing and the library rescans in a loop.
    """
    global _JOB
    if _JOB is not None:
        return _JOB
    if os.name != 'nt':
        _JOB = False
        return False
    try:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        SIZE_T = ctypes.c_size_t

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                        ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', SIZE_T),
                        ('MaximumWorkingSetSize', SIZE_T),
                        ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', SIZE_T),
                        ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ('r_ops', 'w_ops', 'o_ops', 'r_bytes', 'w_bytes', 'o_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T),
                        ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]

        job = k.CreateJobObjectW(None, None)
        if not job:
            _JOB = False
            return False
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x00002000   # KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k.CloseHandle(job)
            _JOB = False
            return False
        _JOB = (k, job)
    except Exception:
        _JOB = False
    return _JOB


def bind_to_lifetime(pid: int) -> None:
    """Tie a process and its children to ours, so the OS reaps them if we die."""
    job = _kill_on_close_job()
    if not job:
        return
    k, handle = job
    with contextlib.suppress(Exception):
        proc = k.OpenProcess(0x0100 | 0x0001, False, pid)   # SET_QUOTA | TERMINATE
        if proc:
            k.AssignProcessToJobObject(handle, proc)
            k.CloseHandle(proc)


_WINGET_LINKS = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links'


# where a bundled or downloaded ffmpeg lives: next to the exe, or in the project
# root when running from source (the same place config.BASE_DIR points at)
TOOLS_DIR = (_APP_DIR if _APP_DIR is not None
             else Path(__file__).resolve().parent.parent) / 'ffmpeg'

FFMPEG_ZIP_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    """Locate an executable. A copy shipped with or downloaded by the app wins;
    winget installs land in a shim dir that is often missing from the PATH of a
    process started before the install."""
    candidates = [TOOLS_DIR / f'{name}.exe']
    if _APP_DIR is not None:
        candidates.insert(0, _APP_DIR / f'{name}.exe')
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    candidate = _WINGET_LINKS / f'{name}.exe'
    return str(candidate) if candidate.exists() else None


def ffmpeg_path() -> str | None:
    return find_tool('ffmpeg')


def ffprobe_path() -> str | None:
    return find_tool('ffprobe')


def missing_tools() -> list[str]:
    """The external binaries recording depends on that cannot be found."""
    return [name for name in ('ffmpeg', 'ffprobe') if not find_tool(name)]


def download_ffmpeg(progress: dict | None = None) -> Path:
    """Fetch a static Windows build and drop ffmpeg.exe + ffprobe.exe into TOOLS_DIR.

    Blocking; run it in a worker thread. `progress`, when given, is updated with
    'done'/'total' bytes and a 'stage' ('download' then 'unpack') for a UI to poll.
    The published sha256 is checked when it can be fetched; a mismatch fails hard.
    """
    from .i18n import t
    if os.name != 'nt':
        raise RuntimeError(t('Automatic download is only available on Windows',
                             'La descarga automática solo está disponible en Windows'))
    progress = progress if progress is not None else {}
    progress.update(done=0, total=0, stage='download')
    request = urllib.request.Request(FFMPEG_ZIP_URL, headers={'User-Agent': 'Recam'})
    archive = Path(tempfile.gettempdir()) / 'recam-ffmpeg.zip'
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response, open(archive, 'wb') as out:
        progress['total'] = int(response.headers.get('Content-Length') or 0)
        while chunk := response.read(256 * 1024):
            out.write(chunk)
            digest.update(chunk)
            progress['done'] += len(chunk)

    expected = ''
    with contextlib.suppress(Exception):
        with urllib.request.urlopen(FFMPEG_ZIP_URL + '.sha256', timeout=30) as sidecar:
            expected = sidecar.read().decode('utf-8', 'replace').split()[0].strip().lower()
    if expected and digest.hexdigest() != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(t('the download did not match its published checksum',
                             'la descarga no coincide con su suma de verificación'))

    progress['stage'] = 'unpack'
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {'ffmpeg.exe', 'ffprobe.exe'}
    extracted: set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            base = os.path.basename(member.filename)
            if member.is_dir() or base not in wanted:
                continue
            with zf.open(member) as src, open(TOOLS_DIR / base, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            extracted.add(base)
    archive.unlink(missing_ok=True)
    if extracted != wanted:
        raise RuntimeError(t('the archive did not contain {}', 'el archivo no contenía {}')
                           .format(', '.join(sorted(wanted - extracted))))
    find_tool.cache_clear()   # make the new binaries visible without a restart
    return TOOLS_DIR


def package_version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        # frozen builds often ship the module without its dist-info
        with contextlib.suppress(Exception):
            mod = __import__(package.replace('-', '_'))
            version = getattr(mod, '__version__', None)
            if version:
                return version
            return getattr(getattr(mod, 'version', None), '__version__', None)
        return None


def tool_version(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, '-version'], capture_output=True, text=True,
                             timeout=10, creationflags=CREATE_NO_WINDOW)
        first = (out.stdout or out.stderr).splitlines()
        return first[0].strip() if first else None
    except Exception:
        return None


def human_size(num_bytes: float) -> str:
    num_bytes = max(0.0, num_bytes)
    if num_bytes < 1024:
        return f'{int(num_bytes)} B'
    for unit in ('KB', 'MB', 'GB'):
        num_bytes /= 1024
        if abs(num_bytes) < 1024:
            return f'{num_bytes:.1f} {unit}'
    return f'{num_bytes / 1024:.1f} TB'


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def human_ago(ts: float) -> str:
    """Relative time for the UI; falls back to a plain date past a week."""
    from .i18n import t
    delta = max(0.0, time.time() - ts)
    if delta < 60:
        return t('just now', 'ahora mismo')
    if delta < 3600:
        return t('{} min ago', 'hace {} min').format(int(delta // 60))
    if delta < 86400:
        return t('{} h ago', 'hace {} h').format(int(delta // 3600))
    days = int(delta // 86400)
    if days == 1:
        return t('yesterday', 'ayer')
    if days < 7:
        return t('{} days ago', 'hace {} días').format(days)
    return datetime.fromtimestamp(ts).strftime('%d/%m/%Y')


def human_span(seconds: float) -> str:
    """Compact length of time for "live for …" / "offline for …" labels."""
    from .i18n import t
    minutes = max(0, int(seconds)) // 60
    if minutes < 1:
        return t('under a minute', 'menos de 1 min')
    if minutes < 60:
        return t('{} min', '{} min').format(minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours} h {minutes} min' if minutes else f'{hours} h'
    days, hours = divmod(hours, 24)
    return f'{days} d {hours} h' if hours else f'{days} d'


def disk_free(path) -> int | None:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


# --- start with Windows: a tiny .vbs in the user's Startup folder. A .bat there
# would flash a console window on logon; WScript's Run with window mode 0 doesn't.

def _startup_shortcut() -> Path | None:
    if os.name != 'nt':
        return None
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    return (Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
            / 'Startup' / 'Recam.vbs')


def startup_enabled() -> bool:
    p = _startup_shortcut()
    return bool(p and p.exists())


def set_startup(enabled: bool) -> bool:
    p = _startup_shortcut()
    if p is None:
        return False
    try:
        if not enabled:
            p.unlink(missing_ok=True)
            return True
        if IS_FROZEN:
            cmd = f'""{Path(sys.executable).resolve()}""'
        else:
            from . import config as config_mod
            pythonw = config_mod.BASE_DIR / '.venv' / 'Scripts' / 'pythonw.exe'
            if not pythonw.exists():
                pythonw = Path(sys.executable).with_name('pythonw.exe')
            cmd = f'""{pythonw}"" ""{config_mod.BASE_DIR / "app.py"}""'
        # quotes are doubled inside a VBS string literal; 0 = hidden window
        p.write_text(f'CreateObject("WScript.Shell").Run "{cmd}", 0, False\r\n',
                     encoding='utf-8')
        return True
    except OSError:
        return False
