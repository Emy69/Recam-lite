from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes
from functools import lru_cache
from importlib import metadata
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

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
    """Locate an executable. winget installs land in a shim dir that is often
    missing from the PATH of a process started before the install."""
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
