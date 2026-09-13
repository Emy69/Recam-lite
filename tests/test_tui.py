from __future__ import annotations

import time

import pytest

from recam import config as config_mod
from recam import tui
from recam.library import Library
from recam.models import Status
from recam.monitor import Monitor


@pytest.fixture
def monitor(cfg):
    m = Monitor(cfg, [], Library(cfg))
    m.add_streamer('https://chaturbate.com/emy')
    m.add_streamer('https://chaturbate.com/otra')
    for s in m.streamers:
        s.auto_record = True   # the keys below toggle from "everyone on"
    m.started = []
    m.stopped = []

    async def start_recording(s):
        m.started.append(s.username)
        m.recordings[s.key] = object()

    async def stop_recording(s):
        m.stopped.append(s.username)
        m.recordings.pop(s.key, None)

    m.start_recording = start_recording
    m.stop_recording = stop_recording
    return m


def test_tui_status_has_a_label_for_every_state():
    for status in Status:
        label, style = tui._tui_status(status)
        assert label and style


async def test_quit_and_add_keys(monitor):
    assert await tui._handle_key('q', monitor, [0]) == 'quit'
    assert await tui._handle_key('ESC', monitor, [0]) == 'quit'
    assert await tui._handle_key('+', monitor, [0]) == 'add'
    assert await tui._handle_key('n', monitor, [0]) == 'add'


async def test_arrows_wrap_around(monitor):
    selected = [0]
    await tui._handle_key('DOWN', monitor, selected)
    assert selected == [1]
    await tui._handle_key('DOWN', monitor, selected)
    assert selected == [0]
    await tui._handle_key('UP', monitor, selected)
    assert selected == [1]


async def test_number_keys_select_a_row(monitor):
    selected = [0]
    await tui._handle_key('2', monitor, selected)
    assert selected == [1]
    await tui._handle_key('9', monitor, selected)
    assert selected == [1]


async def test_keys_do_nothing_without_channels(cfg):
    empty = Monitor(cfg, [], Library(cfg))
    selected = [0]
    assert await tui._handle_key('DOWN', empty, selected) is None
    assert selected == [0]


async def test_space_toggles_auto_for_the_selection(monitor):
    await tui._handle_key(' ', monitor, [0])
    assert monitor.streamers[0].auto_record is False
    assert config_mod.load_streamers()[0].auto_record is False
    assert monitor.streamers[1].auto_record is True


async def test_a_toggles_auto_for_everyone(monitor):
    await tui._handle_key('A', monitor, [0])
    assert [s.auto_record for s in monitor.streamers] == [False, False]
    await tui._handle_key('A', monitor, [0])
    assert [s.auto_record for s in monitor.streamers] == [True, True]


async def test_record_now_ignores_the_cooldown(monitor):
    monitor.streamers[0].cooldown_until = time.time() + 600
    await tui._handle_key('r', monitor, [0])
    assert monitor.started == ['emy']
    assert monitor.streamers[0].cooldown_until == 0


async def test_stop_keys(monitor):
    await tui._handle_key('r', monitor, [0])
    await tui._handle_key('r', monitor, [1])
    await tui._handle_key('s', monitor, [1])
    assert monitor.stopped == ['otra']
    await tui._handle_key('x', monitor, [0])
    assert sorted(monitor.stopped) == ['emy', 'otra']


async def test_delete_removes_the_selection_and_clamps_it(monitor):
    selected = [1]
    await tui._handle_key('\x7f', monitor, selected)
    assert [s.username for s in monitor.streamers] == ['emy']
    assert selected == [0]


async def test_v_toggles_monitoring(monitor):
    await tui._handle_key('v', monitor, [0])
    assert monitor.enabled is False
    await tui._handle_key('v', monitor, [0])
    assert monitor.enabled is True


def test_render_draws_an_empty_list(cfg):
    empty = Monitor(cfg, [], Library(cfg))
    assert tui._render(empty, cfg, 0) is not None


def test_render_draws_channels(monitor, cfg):
    monitor.streamers[0].set_status(Status.ONLINE)
    monitor.streamers[1].last_error = 'no public stream'
    assert tui._render(monitor, cfg, 1) is not None
