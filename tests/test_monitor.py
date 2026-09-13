from __future__ import annotations

import asyncio
import time

import pytest

from recam import config as config_mod
from recam import monitor as monitor_mod
from recam import platforms
from recam.library import Library
from recam.models import Status
from recam.monitor import Monitor
from recam.platforms import Probe

from conftest import make_streamer


@pytest.fixture
def monitor(cfg):
    return Monitor(cfg, [], Library(cfg))


@pytest.fixture
def no_launch(monkeypatch):
    launched = []

    async def fake_start(self):
        launched.append(self.streamer.username)

    monkeypatch.setattr(monitor_mod.Recording, 'start', fake_start)
    return launched


def probe_returning(monkeypatch, probe):
    async def fake_probe(_client, _platform, _username):
        return probe

    monkeypatch.setattr(platforms, 'probe', fake_probe)


def test_add_streamer_accepts_a_channel_url(monitor):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    assert s.username == 'emy'
    assert s.url == 'https://chaturbate.com/emy'
    assert s.auto_record is True
    assert [x.username for x in config_mod.load_streamers()] == ['emy']


def test_add_streamer_rejects_junk(monitor):
    with pytest.raises(ValueError):
        monitor.add_streamer('what is this')
    assert monitor.streamers == []


def test_add_streamer_rejects_a_duplicate(monitor):
    monitor.add_streamer('https://chaturbate.com/emy')
    with pytest.raises(ValueError):
        monitor.add_streamer('https://chaturbate.com/EMY')
    assert len(monitor.streamers) == 1


async def test_remove_streamer_persists_the_list(monitor):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    await monitor.remove_streamer(s)
    assert monitor.streamers == []
    assert config_mod.load_streamers() == []


def test_apply_probe_records_the_broadcast_start(monitor):
    s = make_streamer()
    changed = monitor._apply_probe(s, Probe(Status.ONLINE, started_at=1700000000,
                                            viewers=12))
    assert changed is True
    assert s.live_since == 1700000000
    assert s.last_broadcast_start == 1700000000
    assert s.viewers == 12


def test_apply_probe_never_moves_the_broadcast_start_backwards(monitor):
    s = make_streamer()
    s.last_broadcast_start = 1700000500
    monitor._apply_probe(s, Probe(Status.OFFLINE, started_at=1700000000))
    assert s.last_broadcast_start == 1700000500


def test_activity_feed_collapses_a_repeated_line(monitor):
    monitor._event('fail', 'rate limited')
    monitor._event('fail', 'rate limited')
    assert len(monitor.events) == 1
    monitor._event('fail', 'rate limited', 'emy')
    assert len(monitor.events) == 2


async def test_a_live_channel_with_auto_on_gets_recorded(monitor, monkeypatch,
                                                         no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    probe_returning(monkeypatch, Probe(Status.ONLINE))
    await monitor._check_one(s)
    assert no_launch == ['emy']
    assert s.key in monitor.recordings


async def test_auto_off_is_never_recorded_on_its_own(monitor, monkeypatch, no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    s.auto_record = False
    probe_returning(monkeypatch, Probe(Status.ONLINE))
    await monitor._check_one(s)
    assert no_launch == []
    assert monitor.recordings == {}


async def test_an_unsure_answer_still_gets_a_try(monitor, monkeypatch, no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    probe_returning(monkeypatch, Probe(Status.UNKNOWN))
    await monitor._check_one(s)
    assert no_launch == ['emy']


async def test_an_unsure_answer_is_dropped_while_rate_limited(monitor, monkeypatch,
                                                              no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    probe_returning(monkeypatch, Probe(Status.UNKNOWN))
    platforms._CB_THROTTLE.report_429()
    await monitor._check_one(s)
    assert no_launch == []


async def test_the_concurrency_cap_is_respected(monitor, monkeypatch, no_launch):
    monitor.cfg.max_concurrent = 1
    first = monitor.add_streamer('https://chaturbate.com/emy')
    second = monitor.add_streamer('https://chaturbate.com/emy2')
    probe_returning(monkeypatch, Probe(Status.ONLINE))
    await monitor._check_one(first)
    await monitor._check_one(second)
    assert no_launch == ['emy']


async def test_a_channel_already_recording_is_left_alone(monitor, monkeypatch,
                                                         no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    monitor.recordings[s.key] = object()
    probe_returning(monkeypatch, Probe(Status.OFFLINE))
    await monitor._check_one(s)
    assert s.status is Status.UNKNOWN
    assert no_launch == []


async def test_an_encrypted_stream_backs_off_for_a_long_while(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')

    async def raise_encrypted(self):
        raise platforms.StreamEncrypted('encrypted')

    monkeypatch.setattr(monitor_mod.Recording, 'start', raise_encrypted)
    assert await monitor.start_recording(s) is None
    assert monitor.recordings == {}
    assert monitor.library.active_paths == set()
    assert s.status is Status.ONLINE
    assert s.cooldown_until - time.time() == pytest.approx(
        monitor_mod.ENCRYPTED_COOLDOWN, abs=5)


async def test_a_429_backs_off_at_least_a_minute(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')

    async def raise_limited(self):
        raise platforms.RateLimited('429')

    monkeypatch.setattr(monitor_mod.Recording, 'start', raise_limited)
    await monitor.start_recording(s)
    assert s.cooldown_until - time.time() >= 60
    assert monitor.events[-1]['kind'] == 'fail'


async def test_a_room_that_is_not_public_retries_soon(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')

    async def raise_unavailable(self):
        raise platforms.StreamNotAvailable('not streaming right now')

    monkeypatch.setattr(monitor_mod.Recording, 'start', raise_unavailable)
    await monitor.start_recording(s)
    assert 0 < s.cooldown_until - time.time() <= 30
    assert 'not streaming' in s.last_error


async def test_an_unexpected_launch_error_is_reported(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')

    async def boom(self):
        raise OSError('ffmpeg vanished')

    monkeypatch.setattr(monitor_mod.Recording, 'start', boom)
    await monitor.start_recording(s)
    assert 'ffmpeg vanished' in s.last_error
    assert monitor.recordings == {}


async def test_start_recording_refuses_a_second_capture(monitor, no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    assert await monitor.start_recording(s) is not None
    assert await monitor.start_recording(s) is None
    assert no_launch == ['emy']


async def test_start_recording_marks_the_files_as_in_flight(monitor, no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    rec = await monitor.start_recording(s)
    assert monitor.library.active_paths == {rec.ts_path, rec.mp4_path}
    assert monitor.library.version == 1


async def test_finishing_a_capture_frees_the_channel(monitor, no_launch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    rec = await monitor.start_recording(s)
    s.last_result = 'saved clip.mp4'
    monitor._on_finished(rec, True)
    assert monitor.recordings == {}
    assert monitor.library.active_paths == set()
    assert monitor.events[-1]['kind'] == 'saved'


async def test_stop_recording_is_a_no_op_for_an_idle_channel(monitor):
    await monitor.stop_recording(make_streamer())


async def test_check_all_counts_the_live_channels(monitor, monkeypatch):
    monitor.add_streamer('https://chaturbate.com/emy')
    monitor.add_streamer('https://chaturbate.com/emy2')
    answers = iter([Probe(Status.ONLINE), Probe(Status.OFFLINE)])

    async def fake_probe(_client, _platform, _username):
        return next(answers)

    monkeypatch.setattr(platforms, 'probe', fake_probe)
    assert await monitor.check_all() == 1
    assert monitor.check_progress is None


async def test_a_cooling_down_channel_is_not_polled(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    s.cooldown_until = time.time() + 300
    calls = []

    async def fake_probe(_client, _platform, _username):
        calls.append(1)
        return Probe(Status.ONLINE)

    monkeypatch.setattr(platforms, 'probe', fake_probe)
    await monitor._cycle()
    assert calls == []


async def test_an_idle_watch_only_channel_is_polled_less_often(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    s.auto_record = False
    s.status = Status.OFFLINE
    s.last_check = time.time() - 30
    calls = []

    async def fake_probe(_client, _platform, _username):
        calls.append(1)
        return Probe(Status.OFFLINE)

    monkeypatch.setattr(platforms, 'probe', fake_probe)
    await monitor._cycle()
    assert calls == []                      # checked half a minute ago: skip
    s.last_check = time.time() - 200
    await monitor._cycle()
    assert calls == [1]                     # two minutes old: due again
    s.last_check = time.time()
    s.auto_record = True
    await monitor._cycle()
    assert calls == [1, 1]                  # auto-record: every pass


async def test_a_live_watch_only_channel_is_polled_every_pass(monitor, monkeypatch):
    s = monitor.add_streamer('https://chaturbate.com/emy')
    s.auto_record = False
    s.status = Status.ONLINE
    s.last_check = time.time()
    calls = []

    async def fake_probe(_client, _platform, _username):
        calls.append(1)
        return Probe(Status.ONLINE)

    monkeypatch.setattr(platforms, 'probe', fake_probe)
    await monitor._cycle()
    assert calls == [1]


async def test_the_watch_loop_survives_a_broken_cycle(monitor, monkeypatch):
    monitor.cfg.poll_seconds = 15

    async def boom():
        raise RuntimeError('the site changed shape')

    monkeypatch.setattr(monitor, '_cycle', boom)
    task = asyncio.create_task(monitor._loop())
    await asyncio.sleep(1.2)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_the_interval_is_the_period_of_a_pass_not_a_pause_on_top(monitor):
    monitor.cfg.poll_seconds = 20
    assert monitor._sleep_after_pass(0) == 20
    assert monitor._sleep_after_pass(5) == 15
    assert monitor._sleep_after_pass(19) == 5


def test_an_overrunning_pass_only_gets_breathing_room(monitor):
    monitor.cfg.poll_seconds = 20
    assert monitor._sleep_after_pass(24) == monitor_mod.MIN_PAUSE_BETWEEN_PASSES
    assert monitor._sleep_after_pass(600) == monitor_mod.MIN_PAUSE_BETWEEN_PASSES


def test_the_interval_never_drops_below_fifteen_seconds(monitor):
    monitor.cfg.poll_seconds = 1
    assert monitor._sleep_after_pass(0) == 15


async def test_the_loop_starts_the_next_pass_without_the_extra_wait(monitor,
                                                                    monkeypatch):
    passes = []

    async def slow_cycle():
        passes.append(time.monotonic())
        await asyncio.sleep(0.2)

    monkeypatch.setattr(monitor, '_cycle', slow_cycle)
    monkeypatch.setattr(monitor, '_sleep_after_pass', lambda elapsed: 0.05)
    task = asyncio.create_task(monitor._loop())
    await asyncio.sleep(1.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(passes) >= 2
    assert passes[1] - passes[0] == pytest.approx(0.25, abs=0.15)
