"""Shared state between the running engine and other terminals.

Whoever owns the engine (GUI, `cli run` or the dashboard) writes `data/status.json`
every few seconds; `python -m recam.cli now` reads it. Commands travel the other way as
one file per order, which avoids read/write races between processes.
"""
from __future__ import annotations

import contextlib
import itertools
import json
import os
import time
from pathlib import Path

from . import config as config_mod

STATUS_FILE: Path = config_mod.DATA_DIR / 'status.json'
COMMANDS_DIR: Path = config_mod.DATA_DIR / 'commands'


def write(monitor) -> None:
    data = {
        'ts': time.time(),
        'enabled': bool(getattr(monitor, 'enabled', True)),
        'channels': len(monitor.streamers),
        'recording': [{'user': rec.streamer.username,
                       'platform': rec.streamer.platform,
                       'state': rec.state,
                       'elapsed': round(rec.elapsed),
                       'size': rec.size,
                       'file': rec.mp4_path.name}
                      for rec in monitor.recordings.values()],
    }
    with contextlib.suppress(OSError):
        config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data), encoding='utf-8')
        tmp.replace(STATUS_FILE)   # atomic, so a reader never sees half a file


def read() -> dict | None:
    with contextlib.suppress(Exception):
        return json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    return None


_SEQUENCE = itertools.count()


def send_command(action: str, **kwargs) -> None:
    with contextlib.suppress(OSError):
        COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
        name = f'{time.time_ns()}_{next(_SEQUENCE):06d}_{os.getpid()}.json'
        (COMMANDS_DIR / name).write_text(json.dumps({'action': action, **kwargs}),
                                         encoding='utf-8')


def drain_commands() -> list[dict]:
    """Return the pending orders and delete them."""
    out: list[dict] = []
    with contextlib.suppress(OSError):
        if COMMANDS_DIR.is_dir():
            for f in sorted(COMMANDS_DIR.iterdir()):
                if f.suffix != '.json':
                    continue
                with contextlib.suppress(Exception):
                    out.append(json.loads(f.read_text(encoding='utf-8')))
                with contextlib.suppress(OSError):
                    f.unlink()
    return out
