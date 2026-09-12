from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections import deque

import httpx

from . import config as config_mod
from . import logbook, platforms
from .i18n import t
from .library import Library
from .models import Status, Streamer
from .recorder import Recording

ENCRYPTED_COOLDOWN = 1800   # no point hammering a stream we cannot decrypt

MIN_PAUSE_BETWEEN_PASSES = 5


class Monitor:
    """The engine: polls channels, starts captures and collects them when they end."""

    def __init__(self, cfg: config_mod.Config, streamers: list[Streamer],
                 library: Library) -> None:
        self.cfg = cfg
        self.streamers = streamers
        self.library = library
        self.recordings: dict[str, Recording] = {}
        self.enabled = True
        self.check_progress: list[int] | None = None   # [done, total] while a pass runs
        self.client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        # a short history of what happened, for the panel's activity feed
        self.events: deque[dict] = deque(maxlen=50)

    def _event(self, kind: str, text: str, who: str = '') -> None:
        """Add a line to the activity feed. `who` is the channel it is about; a
        platform-wide note (like a 429 hold) leaves it empty."""
        # a platform-wide problem hits many channels at once; don't let it flood
        # the feed with identical lines
        last = self.events[-1] if self.events else None
        if last and last['text'] == text and last.get('who', '') == who \
                and time.time() - last['ts'] < 120:
            return
        self.events.append({'ts': time.time(), 'kind': kind, 'who': who, 'text': text})

    async def start(self) -> None:
        self.client = httpx.AsyncClient(headers=platforms.REQUEST_HEADERS, timeout=15,
                                        follow_redirects=True)
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
        recs = list(self.recordings.values())
        for rec in recs:
            rec.manual_stop = True
            rec.stop_reason = 'app'
            logbook.event(f'APP CLOSING: finalizing capture of {rec.streamer.username}')
            await rec._terminate()
        # let each one finish its remux, so quitting leaves playable MP4s behind
        if recs:
            await asyncio.gather(*(rec.wait_until_finalized() for rec in recs),
                                 return_exceptions=True)
        config_mod.save_streamers(self.streamers)   # keep last_online across restarts
        if self.client:
            await self.client.aclose()

    def _sleep_after_pass(self, elapsed: float) -> float:
        """How long to wait before the next pass, the one just finished included."""
        interval = max(15, int(self.cfg.poll_seconds))
        return max(MIN_PAUSE_BETWEEN_PASSES, interval - elapsed)

    async def _loop(self) -> None:
        await asyncio.sleep(1)
        while True:
            started = time.monotonic()
            if self.enabled:
                try:
                    await self._cycle()
                except Exception as exc:
                    # a bad cycle must never kill the loop, but it should be traceable
                    logbook.event(f'ERROR in the watch cycle: {exc!r}')
            await asyncio.sleep(self._sleep_after_pass(time.monotonic() - started))

    async def _cycle(self) -> None:
        now = time.time()
        due = [s for s in list(self.streamers)
               if s.key not in self.recordings and now >= s.cooldown_until]
        if due:
            await self._check_many(due, self._check_one)

    async def _check_many(self, streamers: list[Streamer], check) -> list:
        """Run one check per channel, exposing how far along the pass is."""
        self.check_progress = [0, len(streamers)]
        try:
            return await asyncio.gather(*(check(s) for s in streamers),
                                        return_exceptions=True)
        finally:
            self.check_progress = None

    def _count_check(self) -> None:
        if self.check_progress:
            self.check_progress[0] += 1

    def _apply_probe(self, streamer: Streamer, probe: platforms.Probe) -> bool:
        """Fold a poll result into the channel; True when the list is worth saving."""
        if probe.started_at:
            streamer.last_broadcast_start = max(streamer.last_broadcast_start,
                                                probe.started_at)
        changed = streamer.set_status(probe.status, started_at=probe.started_at)
        if streamer.is_live:
            streamer.viewers = probe.viewers
        return changed

    async def _check_one(self, streamer: Streamer) -> None:
        # no jitter here: the per-host throttle already spaces the requests out
        probe = await platforms.probe(self.client, streamer.platform, streamer.username)
        streamer.last_check = time.time()
        self._count_check()
        if streamer.key in self.recordings:
            return
        if self._apply_probe(streamer, probe):
            config_mod.save_streamers(self.streamers)
        status = probe.status
        if not (streamer.auto_record and status in (Status.ONLINE, Status.UNKNOWN)
                and len(self.recordings) < self.cfg.max_concurrent):
            return
        # UNKNOWN normally means "try anyway and let the recorder decide", but when
        # the site is rate-limiting us, trying anyway is what keeps the limit alive
        if status is Status.UNKNOWN and platforms.rate_limited(streamer.platform):
            return
        await self.start_recording(streamer)

    def _drop(self, streamer: Streamer, rec: Recording, exit_label: str,
              reason: str, cooldown: float) -> None:
        self.recordings.pop(streamer.key, None)
        self.library.active_paths.difference_update({rec.ts_path, rec.mp4_path})
        streamer.last_error = reason
        streamer.last_reason = reason
        streamer.last_exit = exit_label
        streamer.cooldown_until = time.time() + cooldown

    async def start_recording(self, streamer: Streamer) -> Recording | None:
        if streamer.key in self.recordings:
            return None
        rec = Recording(streamer, self.cfg, self._on_finished)
        self.recordings[streamer.key] = rec
        self.library.active_paths.update({rec.ts_path, rec.mp4_path})
        try:
            await rec.start()
        except platforms.StreamEncrypted as exc:
            self._drop(streamer, rec, t('not launched (encrypted stream)',
                                        'no se lanzó (emisión cifrada)'), str(exc),
                       ENCRYPTED_COOLDOWN)
            streamer.set_status(Status.ONLINE)   # it is live, we just cannot read it
            self._event('fail', t('encrypted stream, not recordable',
                                  'emisión cifrada, no grabable'), streamer.username)
            logbook.event(f'NOT RECORDABLE  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except platforms.RateLimited as exc:
            # wait the hold out plus some jitter, so 18 channels don't all knock
            # again in the same second when it lifts
            cooldown = max(60.0, platforms.rate_limit_remaining(streamer.platform)) \
                + random.uniform(0, 30)
            self._drop(streamer, rec, t('not launched (rate limited, 429)',
                                        'no se lanzó (límite de peticiones 429)'),
                       str(exc), cooldown)
            self._event('fail', t('{}: rate limited (429), backing off automatically',
                                  '{}: límite de peticiones (429), pausa automática')
                        .format(streamer.platform))
            logbook.event(f'RATE LIMIT 429  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except platforms.StreamNotAvailable as exc:
            self._drop(streamer, rec, t('not launched (no public stream)',
                                        'no se lanzó (sin emisión pública)'), str(exc), 30)
            logbook.event(f'NOT AVAILABLE  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except Exception as exc:
            self._drop(streamer, rec, t('failed to launch', 'error al lanzar'),
                       t('Could not launch the capture: {}',
                         'No se pudo lanzar la grabación: {}').format(exc),
                       self.cfg.poll_seconds)
            self._event('fail', t('failed to launch the capture',
                                  'error al lanzar la grabación'), streamer.username)
            logbook.event(f'LAUNCH ERROR  {streamer.username} '
                          f'({streamer.platform}): {exc!r}')
            return None
        self._event('start', t('recording started', 'grabación iniciada'), streamer.username)
        self.library.bump()   # show the "recording" card right away
        return rec

    def _on_finished(self, rec: Recording, saved: bool) -> None:
        self.recordings.pop(rec.streamer.key, None)
        self.library.active_paths.difference_update({rec.ts_path, rec.mp4_path})
        config_mod.save_streamers(self.streamers)   # the channel just left the live group
        if saved:
            self._event('saved', rec.streamer.last_result, rec.streamer.username)
            self.library.bump()
        else:
            self._event('fail', rec.streamer.last_error, rec.streamer.username)

    async def stop_recording(self, streamer: Streamer) -> None:
        rec = self.recordings.get(streamer.key)
        if rec:
            await rec.stop()

    async def manual_check(self, streamer: Streamer) -> Status:
        probe = await platforms.probe(self.client, streamer.platform, streamer.username)
        streamer.last_check = time.time()
        self._count_check()
        if streamer.key not in self.recordings and self._apply_probe(streamer, probe):
            config_mod.save_streamers(self.streamers)
        return probe.status

    async def check_all(self) -> int:
        """Poll every channel right now (the panel's button). Returns how many are live."""
        results = await self._check_many(list(self.streamers), self.manual_check)
        return sum(1 for r in results if r is Status.ONLINE)

    def add_streamer(self, text: str) -> Streamer:
        detected = platforms.detect(text)
        if not detected:
            raise ValueError(t('That does not look like a Chaturbate channel URL. '
                               'This test build records Chaturbate only.',
                               'Eso no parece una URL de canal de Chaturbate. '
                               'Esta versión de prueba solo graba Chaturbate.'))
        platform, username = detected
        streamer = Streamer(url=platforms.canonical_url(platform, username),
                            platform=platform, username=username)
        if any(s.key == streamer.key for s in self.streamers):
            raise ValueError(t('{} ({}) is already on the list.',
                               '{} ({}) ya está en la lista.').format(username, platform))
        self.streamers.append(streamer)
        config_mod.save_streamers(self.streamers)
        return streamer

    async def remove_streamer(self, streamer: Streamer) -> None:
        await self.stop_recording(streamer)
        with contextlib.suppress(ValueError):
            self.streamers.remove(streamer)
        config_mod.save_streamers(self.streamers)

    def persist(self) -> None:
        config_mod.save_streamers(self.streamers)
