from __future__ import annotations

import logging
import sys

from recam import logbook


def test_event_writes_a_timestamped_line():
    logbook.event('START emy')
    text = logbook.LOG_FILE.read_text(encoding='utf-8')
    assert 'START emy' in text
    assert text.count('\n') == 1


def test_block_indents_the_body_under_a_separator():
    logbook.block('emy — SAVED', ['command: ffmpeg', 'exit: code 0'])
    lines = logbook.LOG_FILE.read_text(encoding='utf-8').splitlines()
    assert any(set(ln) == {'─'} for ln in lines)
    assert '    command: ffmpeg' in lines


def test_read_tail_returns_the_end_of_the_log():
    for i in range(50):
        logbook.event(f'line {i}')
    tail = logbook.read_tail(max_chars=200)
    assert len(tail) <= 200
    assert 'line 49' in tail


def test_read_tail_without_a_log_file():
    assert '(no log yet)' in logbook.read_tail()


def test_enable_console_adds_one_handler_only():
    logbook.enable_console()
    logbook.enable_console()
    handlers = [h for h in logbook._get().handlers
                if type(h) is logging.StreamHandler]
    assert len(handlers) == 1
    assert handlers[0].stream is sys.stdout


def test_the_logger_stays_out_of_the_root_logger():
    assert logbook._get().propagate is False
