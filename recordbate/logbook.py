from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as config_mod

LOG_FILE: Path = config_mod.DATA_DIR / 'recordbate.log'

_logger: logging.Logger | None = None


def _get() -> logging.Logger:
    global _logger
    if _logger is None:
        config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        lg = logging.getLogger('recordbate')
        lg.setLevel(logging.INFO)
        lg.propagate = False   # keep it out of uvicorn's console
        if not lg.handlers:
            handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000,
                                          backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            lg.addHandler(handler)
        _logger = lg
    return _logger


def enable_console() -> None:
    """Mirror events to stdout as well (CLI mode)."""
    lg = _get()
    if any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
           for h in lg.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    lg.addHandler(handler)


def _ts() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def event(message: str) -> None:
    _get().info(f'{_ts()}  {message}')


def block(header: str, lines: list[str]) -> None:
    """Multi-line entry (the post-mortem of one recording) with a visible separator."""
    body = '\n'.join('    ' + ln for ln in lines)
    _get().info(f'\n{"─" * 72}\n{_ts()}  {header}\n{body}')


def read_tail(max_chars: int = 20000) -> str:
    try:
        data = LOG_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '(todavía no hay registro)'
    return data[-max_chars:]
