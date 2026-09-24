"""The request budget: what the watch loop is allowed to send, and when.

Chaturbate's limit is undocumented and answers 429 when crossed, so the engine
spaces its polls out and turns away whatever does not fit the window. These
tests cover the part that is easy to get backwards: a refused poll has to cost
less than one that went through, never more.

Nothing here stubs `probe` or `Recording.start`, since the point is what the
real code spends. The fakes sit at the two edges only: the throttle's window
and the HTTP client.
"""
from __future__ import annotations

import asyncio
import json
import time
import types

import pytest

from recam import config as config_mod
from recam import logbook, platforms, tools
from recam.library import Library
from recam.models import Status, Streamer
from recam.monitor import Monitor


def _channels(count: int, auto_record: bool = True) -> list[Streamer]:
    return [Streamer(url=f'https://chaturbate.com/user{i}', platform='chaturbate',
                     username=f'user{i}', auto_record=auto_record)
            for i in range(count)]


class _Window:
    """A throttle with a fixed number of ordinary slots left in its window.

    Stands in for the real one once a long pass has queued past the window:
    ordinary reservations are refused while the priority lane stays open, since
    it is measured from the last request sent rather than from the queue. That
    asymmetry is what let a refused poll turn into a capture attempt.
    """

    def __init__(self, slots: int = 0):
        self.slots = slots
        self.granted: list[bool] = []            # True for each urgent grant

    async def slot(self, max_wait: float, urgent: bool = False) -> bool:
        if not urgent and len(self.granted) >= self.slots:
            return False
        self.granted.append(urgent)
        return True

    def holding(self) -> bool:
        return False

    def hold_remaining(self) -> float:
        return 0.0

    def report_429(self, retry_after: float = 0.0) -> None:
        pass

    def report_ok(self) -> None:
        pass

    def release(self) -> None:
        pass


class _Rooms:
    """An httpx stand-in: every room answers 200, offline, and is written down."""

    def __init__(self, log: list[str]):
        self.log = log

    async def get(self, url, *_a, **_kw):
        self.log.append(url)
        await asyncio.sleep(0)                   # a real request always suspends

        class _Response:
            status_code = 200
            headers: dict = {}
            text = ''

            @staticmethod
            def json():
                return {'room_status': 'offline', 'start_timestamp': 0,
                        'num_viewers': 0}

        return _Response()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def aclose(self):
        return None


@pytest.fixture
def sent(monkeypatch):
    """Every request the engine sends, however it got there.

    Captures start by resolving the stream over HTTP, so routing the client
    the recorder builds through here too is what makes a capture attempt
    visible as what it costs.
    """
    log: list[str] = []
    monkeypatch.setattr(platforms, 'httpx',
                        types.SimpleNamespace(AsyncClient=lambda *a, **kw: _Rooms(log)))
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')
    return log


def _monitor(cfg, streamers, sent) -> Monitor:
    monitor = Monitor(cfg, streamers, Library(cfg))
    monitor.client = _Rooms(sent)
    return monitor


# ------------------------------------------- a refused poll must not cost more

async def test_a_poll_the_throttle_refused_sends_nothing_at_all(
        cfg, sent, monkeypatch):
    """A refused poll returns UNKNOWN, and UNKNOWN used to mean "try anyway
    and let the recorder decide". Every channel the throttle had just protected
    went straight into a capture attempt, which resolves the endpoint the poll
    skipped and takes the priority lane to do it. The longer the list, the
    faster the app fired, which is backwards.
    """
    window = _Window(slots=0)
    monkeypatch.setattr(platforms, '_CB_THROTTLE', window)
    monitor = _monitor(cfg, _channels(5), sent)

    await monitor._cycle()

    assert sent == []
    assert window.granted == []
    assert monitor.recordings == {}


async def test_a_refused_poll_leaves_the_channel_as_it_was(cfg, sent, monkeypatch):
    """No request went out, so a channel known to be live must not be
    repainted UNKNOWN, and it must still be owed a check."""
    monkeypatch.setattr(platforms, '_CB_THROTTLE', _Window(slots=0))
    live = _channels(1)[0]
    live.set_status(Status.ONLINE)
    was_live_since = live.live_since

    await _monitor(cfg, [live], sent)._cycle()

    assert live.status is Status.ONLINE
    assert live.live_since == was_live_since
    assert live.last_check == 0.0      # still owed one, and the next pass knows


async def test_a_poll_that_went_through_is_stamped(cfg, sent, monkeypatch):
    """The counterpart, so the test above cannot pass by doing nothing."""
    monkeypatch.setattr(platforms, '_CB_THROTTLE', _Window(slots=5))
    channel = _channels(1)[0]

    await _monitor(cfg, [channel], sent)._cycle()

    assert len(sent) == 1
    assert channel.status is Status.OFFLINE
    assert channel.last_check > 0


async def test_the_channels_turned_away_lead_the_next_pass(cfg, sent, monkeypatch):
    """Otherwise the same names at the bottom are refused forever and never
    checked."""
    window = _Window(slots=3)
    monkeypatch.setattr(platforms, '_CB_THROTTLE', window)
    monitor = _monitor(cfg, _channels(6), sent)

    await monitor._cycle()
    first_pass = [url.rstrip('/').rsplit('/', 1)[-1] for url in sent]

    sent.clear()
    window.granted.clear()
    await monitor._cycle()
    second_pass = [url.rstrip('/').rsplit('/', 1)[-1] for url in sent]

    assert len(first_pass) == 3
    assert len(second_pass) == 3
    assert set(first_pass).isdisjoint(second_pass)       # nobody polled twice
    assert set(first_pass) | set(second_pass) == {f'user{i}' for i in range(6)}


# ------------------------------------------------------------ leaving a trace

def test_a_429_leaves_something_to_read_back(cfg):
    """Only a 429 hit while launching a capture used to reach the log. One
    hit while polling raised the banner and logged nothing, leaving no record
    of what spacing was in use."""
    throttle = platforms._HostThrottle(min_interval=1.5, name='chaturbate')

    throttle.report_429()

    written = logbook.LOG_FILE.read_text(encoding='utf-8')
    assert 'RATE LIMIT 429' in written
    assert 'chaturbate' in written
    assert 'req/min' in written        # the number to tune
    assert '2.25' in written           # and that the spacing did widen


def test_a_throttle_with_no_spacing_still_reports(cfg):
    """The fixtures run with min_interval=0 to take the spacing out of the way.
    Quoting a rate for that one divides by zero."""
    throttle = platforms._HostThrottle(min_interval=0.0, name='chaturbate')

    throttle.report_429()                              # must not raise

    assert 'RATE LIMIT 429' in logbook.LOG_FILE.read_text(encoding='utf-8')


# --------------------------------------------- remembering what the site said

def _throttle(name='chaturbate', base=1.5):
    return platforms._HostThrottle(min_interval=base, name=name)


async def test_the_spacing_a_429_forced_is_written_down(cfg):
    throttle = _throttle()

    throttle.report_429()

    saved = json.loads((config_mod.DATA_DIR / 'throttle.json').read_text(encoding='utf-8'))
    assert saved['chaturbate']['interval'] == 2.25          # 1.5 widened once
    assert saved['chaturbate']['at'] > 0


async def test_a_restart_keeps_the_spacing_instead_of_earning_it_again(cfg):
    """A site's limit does not reset because the app did, so starting over
    at the base spacing means collecting another 429 on every launch."""
    _throttle().report_429()                                 # what a previous run learned

    after_restart = _throttle()                              # a fresh process
    await after_restart.slot(max_wait=1)

    assert after_restart.min_interval == 2.25
    assert after_restart.min_interval > after_restart.base_interval


async def test_each_host_remembers_its_own(cfg):
    """One file, two hosts: neither may restore the other's spacing."""
    _throttle('chaturbate').report_429()                     # 1.5 -> 2.25
    sc = _throttle('stripchat', base=1.0)
    sc.report_429()                                          # 1.0 -> 1.5

    cb_again, sc_again = _throttle('chaturbate'), _throttle('stripchat', base=1.0)
    await cb_again.slot(max_wait=1)
    await sc_again.slot(max_wait=1)

    assert cb_again.min_interval == 2.25
    assert sc_again.min_interval == 1.5


async def test_a_clock_that_went_backwards_keeps_the_spacing(cfg):
    """Between two runs the clock can move back: a manual change, an NTP
    step, or a timestamp rounded just past now. Too wide costs some latency,
    too narrow costs another 429."""
    (config_mod.DATA_DIR / 'throttle.json').write_text(json.dumps(
        {'chaturbate': {'interval': 3.0, 'at': time.time() + 3600}}), encoding='utf-8')

    throttle = _throttle()
    await throttle.slot(max_wait=1)

    assert throttle.min_interval == 3.0


async def test_what_this_run_learned_outranks_the_file(cfg):
    """The file is for a cold start. Once the site has answered 429 here, that
    is the live state: restoring over it would also reset the relax deadline,
    so the hour of quiet would never finish counting down."""
    (config_mod.DATA_DIR / 'throttle.json').write_text(json.dumps(
        {'chaturbate': {'interval': 5.0, 'at': time.time()}}), encoding='utf-8')
    throttle = _throttle()
    throttle.report_429()                       # 1.5 -> 2.25, here and now
    throttle.release()

    await throttle.slot(max_wait=1)

    assert throttle.min_interval == 2.25        # not the 5.0 sitting in the file


async def test_spacing_old_enough_to_have_relaxed_is_not_restored(cfg):
    """It would have widened back down after an hour of quiet anyway; coming
    back from a long shutdown should not resurrect it."""
    (config_mod.DATA_DIR / 'throttle.json').write_text(json.dumps(
        {'chaturbate': {'interval': 4.0,
                        'at': time.time() - platforms._HostThrottle.RELAX_AFTER - 60}}),
        encoding='utf-8')

    throttle = _throttle()
    await throttle.slot(max_wait=1)

    assert throttle.min_interval == 1.5


async def test_a_restored_spacing_stays_inside_its_limits(cfg):
    """Whatever the file says, it cannot push the app past the ceiling the
    throttle sets for itself, or below the base it was built with."""
    (config_mod.DATA_DIR / 'throttle.json').write_text(json.dumps(
        {'chaturbate': {'interval': 99.0, 'at': time.time()}}), encoding='utf-8')

    throttle = _throttle()
    await throttle.slot(max_wait=1)

    assert throttle.min_interval == platforms._HostThrottle.MAX_INTERVAL


async def test_an_unreadable_file_is_the_same_as_nothing_learned(cfg):
    (config_mod.DATA_DIR / 'throttle.json').write_text('{not json', encoding='utf-8')

    throttle = _throttle()
    assert await throttle.slot(max_wait=1) is True
    assert throttle.min_interval == 1.5


# ------------------------------------------------------- the budget, end to end

class _FakeAsyncio:
    """asyncio with its sleep swapped; everything else is the real thing."""

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


@pytest.fixture
def simulated_clock(monkeypatch):
    """A discrete-event clock for platforms: time only moves once every task is
    parked, which is what makes a whole pass reserve its slots in one tick,
    the way it does on a real loop."""
    now = {'t': 1000.0}
    parked: list[list] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds, *_a, **_kw):
        if seconds <= 0:
            return await real_sleep(0)
        event = asyncio.Event()
        parked.append([now['t'] + seconds, event])
        await event.wait()

    async def run(coro):
        task = asyncio.ensure_future(coro)
        while not task.done():
            for _ in range(50):
                await real_sleep(0)
                if task.done():
                    return await task
            if not parked:
                break
            now['t'] = min(entry[0] for entry in parked)
            for entry in [e for e in parked if e[0] <= now['t']]:
                parked.remove(entry)
                entry[1].set()
        return await task

    monkeypatch.setattr(platforms, 'time',
                        types.SimpleNamespace(monotonic=lambda: now['t'],
                                              time=lambda: now['t']))
    monkeypatch.setattr(platforms, 'asyncio', _FakeAsyncio(sleep))
    return types.SimpleNamespace(run=run, now=now)


async def test_one_pass_never_outruns_the_throttle_window(
        cfg, sent, simulated_clock, monkeypatch):
    """What a pass sends is capped by the clock, not by how many channels
    are on the list.

    Measured on the real throttle before the fix: 21 requests for 21 channels,
    30 for 30, 50 for 50, a rate climbing from 42/min to 100/min as the list
    grew, while the site starts answering 429 around 60/min.
    """
    async def requests_for(channel_count: int) -> int:
        sent.clear()
        simulated_clock.now['t'] = 1000.0
        monkeypatch.setattr(platforms, '_CB_THROTTLE',
                            platforms._HostThrottle(min_interval=1.5))
        monitor = _monitor(cfg, _channels(channel_count), sent)
        await simulated_clock.run(monitor._cycle())
        return len(sent)

    window_full = await requests_for(21)
    twice_as_many = await requests_for(50)

    assert window_full == 21              # a full window, nothing wasted
    assert twice_as_many == window_full   # and a longer list sends no more
