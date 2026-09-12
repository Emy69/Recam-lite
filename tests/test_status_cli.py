from __future__ import annotations

import argparse
import json
import time
import types
from pathlib import Path

import pytest

from recam import cli
from recam import config as config_mod
from recam import status as status_mod

from conftest import make_streamer


def fake_recording(username='emy', state='recording', elapsed=65.4, size=2048):
    return types.SimpleNamespace(
        streamer=make_streamer(username), state=state, elapsed=elapsed, size=size,
        mp4_path=Path(f'C:/rec/{username}/clip.mp4'))


def fake_monitor(recordings=(), channels=(), enabled=True):
    return types.SimpleNamespace(
        enabled=enabled, streamers=list(channels),
        recordings={r.streamer.key: r for r in recordings})


def args(**kwargs):
    return argparse.Namespace(**kwargs)


def test_status_roundtrip():
    monitor = fake_monitor([fake_recording()], [make_streamer(), make_streamer('emy2')])
    status_mod.write(monitor)
    data = status_mod.read()
    assert data['channels'] == 2
    assert data['enabled'] is True
    assert data['recording'][0] == {'user': 'emy', 'platform': 'chaturbate',
                                    'state': 'recording', 'elapsed': 65,
                                    'size': 2048, 'file': 'clip.mp4'}
    assert time.time() - data['ts'] < 5


def test_status_write_leaves_no_temporary_file(isolated):
    status_mod.write(fake_monitor())
    assert list(isolated.data.glob('*.tmp')) == []


def test_status_read_without_a_file():
    assert status_mod.read() is None


def test_status_read_of_a_corrupt_file():
    status_mod.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    status_mod.STATUS_FILE.write_text('{half', encoding='utf-8')
    assert status_mod.read() is None


def test_commands_are_drained_in_order_and_removed():
    status_mod.send_command('stop', channel='emy')
    status_mod.send_command('shutdown')
    pending = status_mod.drain_commands()
    assert [c['action'] for c in pending] == ['stop', 'shutdown']
    assert pending[0]['channel'] == 'emy'
    assert status_mod.drain_commands() == []


def test_orders_sent_in_the_same_instant_are_all_kept():
    for i in range(5):
        status_mod.send_command('stop', channel=f'canal{i}')
    pending = status_mod.drain_commands()
    assert [c['channel'] for c in pending] == [f'canal{i}' for i in range(5)]


def test_draining_an_empty_queue_is_harmless():
    assert status_mod.drain_commands() == []


def test_drain_ignores_files_that_are_not_orders():
    status_mod.COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    (status_mod.COMMANDS_DIR / 'notes.txt').write_text('hi', encoding='utf-8')
    status_mod.send_command('stop', channel='emy')
    assert [c['action'] for c in status_mod.drain_commands()] == ['stop']


def test_match_finds_a_channel_by_user_url_or_key():
    channels = [make_streamer('emy'), make_streamer('otra')]
    assert cli._match(channels, 'EMY')[0].username == 'emy'
    assert cli._match(channels, 'https://chaturbate.com/emy')[0].username == 'emy'
    assert cli._match(channels, 'chaturbate:emy')[0].username == 'emy'
    assert cli._match(channels, 'em') == []


def test_list_without_channels(capsys):
    assert cli.cmd_list(args()) == 0
    assert 'No channels' in capsys.readouterr().out


def test_list_shows_the_auto_flag(capsys):
    config_mod.save_streamers([make_streamer('emy', auto_record=False)])
    cli.cmd_list(args())
    out = capsys.readouterr().out
    assert 'emy' in out and 'manual' in out


def test_add_and_remove_a_channel(capsys):
    assert cli.cmd_add(args(url='https://chaturbate.com/emy')) == 0
    assert [s.username for s in config_mod.load_streamers()] == ['emy']
    assert cli.cmd_remove(args(channel='emy')) == 0
    assert config_mod.load_streamers() == []
    assert 'Removed' in capsys.readouterr().out


def test_add_rejects_junk(capsys):
    assert cli.cmd_add(args(url='nope')) == 1
    assert 'Error' in capsys.readouterr().err


def test_remove_an_unknown_channel_fails(capsys):
    assert cli.cmd_remove(args(channel='nobody')) == 1
    assert capsys.readouterr().err


def test_auto_toggles_and_persists(capsys):
    config_mod.save_streamers([make_streamer('emy')])
    assert cli.cmd_auto(args(channel='emy', state='off')) == 0
    assert config_mod.load_streamers()[0].auto_record is False
    assert cli.cmd_auto(args(channel='emy', state='on')) == 0
    assert config_mod.load_streamers()[0].auto_record is True


def test_auto_on_an_unknown_channel_fails():
    assert cli.cmd_auto(args(channel='nobody', state='on')) == 1


def test_offset_shows_the_current_value(capsys):
    assert cli.cmd_offset(args(ms=None)) == 0
    assert '0 ms' in capsys.readouterr().out


def test_offset_is_clamped_and_saved(capsys):
    assert cli.cmd_offset(args(ms=99999)) == 0
    assert config_mod.load().audio_offset_ms == 2000
    cli.cmd_offset(args(ms=-99999))
    assert config_mod.load().audio_offset_ms == -2000
    cli.cmd_offset(args(ms=-300))
    assert config_mod.load().audio_offset_ms == -300
    assert 'pulls the audio forward' in capsys.readouterr().out


def test_now_without_a_running_engine(capsys):
    assert cli.cmd_now(args()) == 1
    assert 'No state file' in capsys.readouterr().out


def test_now_lists_what_is_recording(capsys):
    status_mod.write(fake_monitor([fake_recording()], [make_streamer()]))
    assert cli.cmd_now(args()) == 0
    out = capsys.readouterr().out
    assert 'emy' in out and '1:05' in out


def test_now_says_when_nothing_is_recording(capsys):
    status_mod.write(fake_monitor([], [make_streamer()]))
    assert cli.cmd_now(args()) == 0
    assert 'Nothing recording' in capsys.readouterr().out


def test_now_warns_about_a_stale_state_file(capsys):
    status_mod.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    status_mod.STATUS_FILE.write_text(
        json.dumps({'ts': time.time() - 600, 'channels': 1, 'recording': []}),
        encoding='utf-8')
    cli.cmd_now(args())
    assert 'old' in capsys.readouterr().out


def test_stop_queues_the_order_even_without_a_daemon(capsys):
    assert cli.cmd_stop(args(channel='emy')) == 0
    orders = status_mod.drain_commands()
    assert orders == [{'action': 'stop', 'channel': 'emy'}]
    assert 'does not look running' in capsys.readouterr().out


def test_stop_notes_a_channel_that_is_not_recording(capsys):
    status_mod.write(fake_monitor([fake_recording('otra')], [make_streamer()]))
    cli.cmd_stop(args(channel='emy'))
    assert 'does not appear to be recording' in capsys.readouterr().out


def test_shutdown_needs_a_running_daemon(capsys):
    assert cli.cmd_shutdown(args()) == 1
    status_mod.write(fake_monitor())
    assert cli.cmd_shutdown(args()) == 0
    assert [c['action'] for c in status_mod.drain_commands()] == ['shutdown']


async def test_apply_commands_stops_one_channel():
    stopped = []
    monitor = fake_monitor([fake_recording('emy')],
                           [make_streamer('emy'), make_streamer('otra')])

    async def stop_recording(s):
        stopped.append(s.username)

    monitor.stop_recording = stop_recording
    status_mod.send_command('stop', channel='otra')
    assert await cli._apply_commands(monitor) is False
    assert stopped == []
    status_mod.send_command('stop', channel='emy')
    await cli._apply_commands(monitor)
    assert stopped == ['emy']


async def test_apply_commands_stops_everything():
    stopped = []
    monitor = fake_monitor([fake_recording('emy'), fake_recording('otra')],
                           [make_streamer('emy'), make_streamer('otra')])

    async def stop_recording(s):
        stopped.append(s.username)

    monitor.stop_recording = stop_recording
    status_mod.send_command('stop', channel='all')
    await cli._apply_commands(monitor)
    assert sorted(stopped) == ['emy', 'otra']


async def test_apply_commands_reports_a_shutdown(capsys):
    monitor = fake_monitor()
    status_mod.send_command('shutdown')
    assert await cli._apply_commands(monitor) is True


def test_main_dispatches_a_subcommand(capsys):
    config_mod.save_streamers([make_streamer('emy')])
    assert cli.main(['list']) == 0
    assert 'emy' in capsys.readouterr().out


def test_main_rejects_an_unknown_subcommand():
    with pytest.raises(SystemExit):
        cli.main(['does-not-exist'])


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
