from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import time
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


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    """Locate an executable. A copy shipped next to the frozen app wins; winget
    installs land in a shim dir that is often missing from the PATH of a
    process started before the install."""
    if _APP_DIR is not None:
        for candidate in (_APP_DIR / f'{name}.exe', _APP_DIR / 'ffmpeg' / f'{name}.exe'):
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
    seconds = int(seconds)
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
            / 'Startup' / 'RecordBate.vbs')


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
