from __future__ import annotations

import time

import pytest

from recam import i18n, tools


@pytest.mark.parametrize('value, expected', [
    (0, '0 B'),
    (999, '999 B'),
    (1023, '1023 B'),
    (1024, '1.0 KB'),
    (1536, '1.5 KB'),
    (1024 ** 2, '1.0 MB'),
    (1024 ** 3, '1.0 GB'),
    (1024 ** 4, '1.0 TB'),
])
def test_human_size(value, expected):
    assert tools.human_size(value) == expected


@pytest.mark.parametrize('value, expected', [
    (None, '—'),
    (0, '0:00'),
    (9, '0:09'),
    (59, '0:59'),
    (60, '1:00'),
    (3599, '59:59'),
    (3600, '1:00:00'),
    (3661, '1:01:01'),
])
def test_human_duration(value, expected):
    assert tools.human_duration(value) == expected


def test_human_ago_in_english(monkeypatch):
    i18n.set_language('en')
    now = 1_700_000_000.0
    monkeypatch.setattr(tools.time, 'time', lambda: now)
    assert tools.human_ago(now) == 'just now'
    assert tools.human_ago(now - 300) == '5 min ago'
    assert tools.human_ago(now - 7200) == '2 h ago'
    assert tools.human_ago(now - 86400 * 1.2) == 'yesterday'
    assert tools.human_ago(now - 86400 * 3) == '3 days ago'
    assert '/' in tools.human_ago(now - 86400 * 30)


def test_human_ago_never_goes_negative(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr(tools.time, 'time', lambda: now)
    assert tools.human_ago(now + 500) == 'just now'


@pytest.mark.parametrize('seconds, expected', [
    (0, 'under a minute'),
    (59, 'under a minute'),
    (60, '1 min'),
    (3540, '59 min'),
    (3600, '1 h'),
    (5400, '1 h 30 min'),
    (86400, '1 d'),
    (90000, '1 d 1 h'),
])
def test_human_span(seconds, expected):
    i18n.set_language('en')
    assert tools.human_span(seconds) == expected


def test_find_tool_prefers_the_bundled_copy(tmp_path, monkeypatch):
    bundled = tmp_path / 'ffmpeg'
    bundled.mkdir()
    (bundled / 'ffmpeg.exe').write_text('binary', encoding='utf-8')
    monkeypatch.setattr(tools, 'TOOLS_DIR', bundled)
    monkeypatch.setattr(tools.shutil, 'which', lambda _n: 'C:/windows/ffmpeg.exe')
    tools.find_tool.cache_clear()
    assert tools.find_tool('ffmpeg') == str(bundled / 'ffmpeg.exe')


def test_find_tool_falls_back_to_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'TOOLS_DIR', tmp_path / 'empty')
    monkeypatch.setattr(tools.shutil, 'which',
                        lambda n: f'C:/windows/{n}.exe' if n == 'ffprobe' else None)
    monkeypatch.setattr(tools, '_WINGET_LINKS', tmp_path / 'nowhere')
    tools.find_tool.cache_clear()
    assert tools.find_tool('ffprobe') == 'C:/windows/ffprobe.exe'
    assert tools.find_tool('ffmpeg') is None


def test_missing_tools_lists_what_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'TOOLS_DIR', tmp_path / 'empty')
    monkeypatch.setattr(tools, '_WINGET_LINKS', tmp_path / 'nowhere')
    monkeypatch.setattr(tools.shutil, 'which', lambda _n: None)
    tools.find_tool.cache_clear()
    assert tools.missing_tools() == ['ffmpeg', 'ffprobe']


def test_disk_free_returns_none_for_a_bad_path():
    assert tools.disk_free('Z:/definitely/not/here') is None


def test_human_helpers_clamp_negative_input():
    assert tools.human_duration(-5) == '0:00'
    assert tools.human_size(-5) == '0 B'
