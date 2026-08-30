from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections import deque

import httpx

from . import config as config_mod
from . import logbook, platforms
from .library import Library
from .models import Status, Streamer
from .recorder import Recording

ENCRYPTED_COOLDOWN = 1800   # no point hammering a stream we cannot decrypt


class Monitor:
    """The engine: polls channels, starts captures and collects them when they end."""

    def __init__(self, cfg: config_mod.Config, streamers: list[Streamer],
                 library: Library) -> None:
        self.cfg = cfg
        self.streamers = streamers
        self.library = library
        self.recordings: dict[str, Recording] = {}
        self.enabled = True
        self.client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        # a short history of what happened, for the panel's activity feed
        self.events: deque[dict] = deque(maxlen=50)

    def _event(self, kind: str, text: str) -> None:
        # a platform-wide problem (like a 429 hold) hits many channels at once;
        # don't let it flood the feed with identical lines
        if self.events and self.events[-1]['text'] == text \
                and time.time() - self.events[-1]['ts'] < 120:
            return
        self.events.append({'ts': time.time(), 'kind': kind, 'text': text})

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
            logbook.event(f'APP CERRÁNDOSE: finalizando grabación de {rec.streamer.username}')
            await rec._terminate()
        # let each one finish its remux, so quitting leaves playable MP4s behind
        if recs:
            await asyncio.gather(*(rec.wait_until_finalized() for rec in recs),
                                 return_exceptions=True)
        if self.client:
            await self.client.aclose()

    async def _loop(self) -> None:
        await asyncio.sleep(1)
        while True:
            if self.enabled:
                try:
                    await self._cycle()
                except Exception as exc:
                    # a bad cycle must never kill the loop, but it should be traceable
                    logbook.event(f'ERROR en el ciclo de vigilancia: {exc!r}')
            await asyncio.sleep(max(15, int(self.cfg.poll_seconds)))

    async def _cycle(self) -> None:
        now = time.time()
        due = [s for s in list(self.streamers)
               if s.key not in self.recordings and now >= s.cooldown_until]
        if due:
            await asyncio.gather(*(self._check_one(s) for s in due))

    async def _check_one(self, streamer: Streamer) -> None:
        await asyncio.sleep(random.uniform(0, 2))   # spread the requests out a little
        status = await platforms.check_online(self.client, streamer.platform,
                                              streamer.username)
        streamer.last_check = time.time()
        if streamer.key in self.recordings:
            return
        streamer.status = status
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
            self._drop(streamer, rec, 'no se lanzó (emisión cifrada)', str(exc),
                       ENCRYPTED_COOLDOWN)
            streamer.status = Status.ONLINE   # it is live, we just cannot read it
            self._event('fail', f'{streamer.username}: emisión cifrada, no grabable')
            logbook.event(f'NO GRABABLE  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except platforms.RateLimited as exc:
            # wait the hold out plus some jitter, so 18 channels don't all knock
            # again in the same second when it lifts
            cooldown = max(60.0, platforms.rate_limit_remaining(streamer.platform)) \
                + random.uniform(0, 30)
            self._drop(streamer, rec, 'no se lanzó (límite de peticiones 429)',
                       str(exc), cooldown)
            self._event('fail', f'{streamer.platform}: límite de peticiones (429), '
                                'pausa automática')
            logbook.event(f'LÍMITE 429  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except platforms.StreamNotAvailable as exc:
            self._drop(streamer, rec, 'no se lanzó (sin emisión pública)', str(exc), 30)
            logbook.event(f'NO DISPONIBLE  {streamer.username} ({streamer.platform}): {exc}')
            return None
        except Exception as exc:
            self._drop(streamer, rec, 'error al lanzar',
                       f'No se pudo lanzar la grabación: {exc}', self.cfg.poll_seconds)
            self._event('fail', f'{streamer.username}: error al lanzar la grabación')
            logbook.event(f'ERROR AL LANZAR  {streamer.username} '
                          f'({streamer.platform}): {exc!r}')
            return None
        self._event('start', f'Grabando a {streamer.username} ({streamer.platform})')
        self.library.bump()   # show the "recording" card right away
        return rec

    def _on_finished(self, rec: Recording, saved: bool) -> None:
        self.recordings.pop(rec.streamer.key, None)
        self.library.active_paths.difference_update({rec.ts_path, rec.mp4_path})
        if saved:
            self._event('saved', f'{rec.streamer.username}: {rec.streamer.last_result}')
            self.library.bump()
        else:
            self._event('fail', f'{rec.streamer.username}: {rec.streamer.last_error}')

    async def stop_recording(self, streamer: Streamer) -> None:
        rec = self.recordings.get(streamer.key)
        if rec:
            await rec.stop()

    async def manual_check(self, streamer: Streamer) -> Status:
        status = await platforms.check_online(self.client, streamer.platform,
                                              streamer.username)
        streamer.last_check = time.time()
        if streamer.key not in self.recordings:
            streamer.status = status
        return status

    def add_streamer(self, text: str) -> Streamer:
        detected = platforms.detect(text)
        if not detected:
            raise ValueError('No reconozco esa URL. Vale un enlace de Twitch, Kick, '
                             'Stripchat o Chaturbate.')
        platform, username = detected
        streamer = Streamer(url=platforms.canonical_url(platform, username),
                            platform=platform, username=username)
        if any(s.key == streamer.key for s in self.streamers):
            raise ValueError(f'{username} ({platform}) ya está en la lista.')
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
