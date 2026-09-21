"""The request budget: what the watch loop is allowed to send, and when.

Chaturbate's limit is undocumented and answers 429 when crossed, so the engine
spaces its polls out and turns away whatever does not fit the window. These
tests pin down the part that is easy to get backwards: a poll that was turned
away has to cost *less* than one that went through, never more.

Nothing here stubs `probe` or `Recording.start`: the whole point is what the
real code spends, so the fakes sit at the two edges only — the throttle's
window and the HTTP client.
"""
from __future__ import annotations

import asyncio
import types

import pytest

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
    ordinary reservations are refused, but the priority lane stays open, since
    it is measured from the last request actually sent rather than from the
    queue. That asymmetry is the whole point — it is what lets a refused poll
    turn into a more expensive capture attempt.
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
    """The trap: a refused poll returns UNKNOWN, and UNKNOWN used to mean "try
    anyway and let the recorder decide". So every channel the throttle just
    protected went straight into a capture attempt, which resolves the very
    endpoint the poll skipped and takes the priority lane to do it. The more
    channels on the list, the faster the app fired — backwards.
    """
    window = _Window(slots=0)
    monkeypatch.setattr(platforms, '_CB_THROTTLE', window)
    monitor = _monitor(cfg, _channels(5), sent)

    await monitor._cycle()

    assert sent == []
    assert window.granted == []
    assert monitor.recordings == {}


async def test_a_refused_poll_leaves_the_channel_as_it_was(cfg, sent, monkeypatch):
    """Nothing was asked, so nothing was learned: a channel known to be live
    must not be repainted UNKNOWN by a poll that never left the building, and
    it must still be owed a check."""
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
    """Otherwise the same bottom names are refused for ever and never checked."""
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
    """Only a 429 met while LAUNCHING a capture used to reach the log. One met
    while polling raised the banner and logged nothing, so the one question
    worth asking afterwards — what spacing was it using? — had no answer."""
    throttle = platforms._HostThrottle(min_interval=1.5, name='chaturbate')

    throttle.report_429()

    written = logbook.LOG_FILE.read_text(encoding='utf-8')
    assert 'RATE LIMIT 429' in written
    assert 'chaturbate' in written
    assert 'req/min' in written        # the number you tune on
    assert '2.25' in written           # and that the spacing did widen


def test_a_throttle_with_no_spacing_still_reports(cfg):
    """The fixtures run with min_interval=0 to take the spacing out of the way.
    Quoting a rate for that one divides by zero."""
    throttle = platforms._HostThrottle(min_interval=0.0, name='chaturbate')

    throttle.report_429()                              # must not raise

    assert 'RATE LIMIT 429' in logbook.LOG_FILE.read_text(encoding='utf-8')


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
    """The property that keeps the app under the limit: what a pass sends is
    capped by the clock, not by how many channels are on the list.

    Measured on the real throttle before this was fixed: 21 requests for 21
    channels, 30 for 30, 50 for 50 — a rate climbing from 42/min to 100/min as
    the list grew, while the site starts answering 429 around 60/min.
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
