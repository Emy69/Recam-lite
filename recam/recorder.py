from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import deque
from pathlib import Path

from . import config as config_mod
from . import logbook, platforms, tools
from .i18n import t
from .models import Status, Streamer

MIN_VALID_BYTES = 200_000          # below this there was no real stream to keep
START_TIMEOUT = 120                # seconds without data before giving up
COOLDOWN_AFTER_END = 30            # short retry: the stream may have just hiccuped
COOLDOWN_AFTER_MANUAL_STOP = 600   # don't fight the user who just pressed stop
COOLDOWN_AFTER_OFFLINE = 15

# Post-processing is pure disk I/O on multi-gigabyte files. Stopping several
# captures at once used to fire that many parallel remuxes and grind the machine.
# One at a time keeps the app responsive and finishes just as fast overall.
_POSTPROCESS = asyncio.Semaphore(1)

SNAPSHOT_EVERY = 15   # seconds between live-preview frames of a running capture
PREVIEW_IDLE_AFTER = 60


class Recording:
    """One capture in flight: subprocess + watchdog + post-processing."""

    def __init__(self, streamer: Streamer, cfg: config_mod.Config, on_finished) -> None:
        self.streamer = streamer
        self.cfg = cfg
        self.on_finished = on_finished          # callback(recording, saved: bool)
        stem = config_mod.build_output_stem(cfg, streamer)
        self.ts_path = (cfg.recordings_path / stem).with_suffix('.ts')
        self.mp4_path = self.ts_path.with_suffix('.mp4')
        self.state = 'starting'                 # starting | recording | stopping | processing
        self.started_at = time.time()
        self.recording_since: float | None = None
        self.rec_file: Path | None = None
        self.size = 0
        self.manual_stop = False
        self.stop_reason = ''                   # '', 'user', 'app', 'timeout'
        self.cmd = ''
        self.log: deque[str] = deque(maxlen=200)
        self.proc: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task | None = None
        self._last_snapshot = 0.0
        self._preview_read = 0.0
        self._snapshot_task: asyncio.Task | None = None

    @property
    def elapsed(self) -> float:
        return time.time() - (self.recording_since or self.started_at)

    @property
    def live_thumb(self) -> tuple[str, int] | None:
        """(recordings-relative path, mtime) of the live preview frame, if any.

        The path is what the /media route serves; the mtime doubles as a cache
        buster so the browser refetches each new frame.

        Reading this is also what tells the recorder somebody is looking: frames
        are only pulled while a page keeps asking for them.
        """
        self._preview_read = time.time()
        thumb = thumb_path_for(self.mp4_path)
        try:
            mtime = int(thumb.stat().st_mtime)
            return thumb.relative_to(self.cfg.recordings_path).as_posix(), mtime
        except (OSError, ValueError):
            return None

    async def _snapshot(self) -> None:
        """Pull one recent frame out of the growing capture into the thumbnail slot.

        thumb_path_for maps X.ts and X.mp4 to the same file, so the preview simply
        becomes the recording's real thumbnail slot; finalizing overwrites it with
        the definitive one.
        """
        ffmpeg = tools.ffmpeg_path()
        src = self.rec_file or self.ts_path
        if not ffmpeg or not src.exists():
            return
        thumb = thumb_path_for(self.mp4_path)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        # temp file + replace, so the browser never fetches a half-written image
        tmp = thumb.with_name(thumb.stem + '.live.jpg')
        try:
            for extra in (['-sseof', '-4'], []):   # near the end; first frame as fallback
                rc, _ = await _run_quiet([ffmpeg, '-y', '-loglevel', 'error', *extra,
                                          '-i', str(src), '-frames:v', '1',
                                          '-vf', 'scale=480:-2', str(tmp)], timeout=20)
                if rc == 0 and tmp.exists() and tmp.stat().st_size > 0:
                    with contextlib.suppress(OSError):
                        tmp.replace(thumb)
                    return
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    def _should_snapshot(self) -> bool:
        """Whether to pull a preview frame right now: only while the capture is
        running, somebody is watching it, and the previous frame is old enough."""
        if self.state != 'recording':
            return False
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return False
        if time.time() - self._preview_read > PREVIEW_IDLE_AFTER:
            return False
        return time.time() - self._last_snapshot >= SNAPSHOT_EVERY

    async def _stop_snapshot(self) -> None:
        task, self._snapshot_task = self._snapshot_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _output_file(self) -> Path | None:
        """The file the recorder is really writing to.

        Everything we launch today writes straight to ts_path. The directory sweep
        is a fallback for captures that landed as 'X.ts.mp4' back when yt-dlp did
        the muxing and tacked its own extension on.
        """
        if self.ts_path.exists():
            return self.ts_path
        prefix = self.ts_path.name + '.'
        best: Path | None = None
        best_size = -1
        with contextlib.suppress(OSError):
            for p in self.ts_path.parent.iterdir():
                if p == self.mp4_path or not p.name.startswith(prefix) or not p.is_file():
                    continue
                with contextlib.suppress(OSError):
                    size = p.stat().st_size
                    if size > best_size:
                        best, best_size = p, size
        return best

    async def start(self) -> None:
        self.ts_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = await platforms.build_record_cmd(
            self.streamer.platform, self.streamer.username, self.cfg.quality, self.ts_path)
        self.cmd = ' '.join(cmd)
        logbook.event(f'START  {self.streamer.username} ({self.streamer.platform})  →  {self.cmd}')
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=tools.CREATE_NO_WINDOW,
        )
        tools.bind_to_lifetime(self.proc.pid)
        asyncio.create_task(self._read_output())
        self._watch_task = asyncio.create_task(self._watch())

    async def wait_until_finalized(self, timeout: float = 600) -> None:
        """Block until the watchdog is done remuxing and thumbnailing.

        Used on shutdown so quitting leaves finished MP4s instead of half-written
        captures.
        """
        if self._watch_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._watch_task), timeout)

    async def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                self.log.append(text)

    async def _watch(self) -> None:
        assert self.proc
        while self.proc.returncode is None:
            found = self._output_file()
            if found is not None:
                self.rec_file = found
                with contextlib.suppress(OSError):
                    self.size = found.stat().st_size
            if self._should_snapshot():
                self._last_snapshot = time.time()
                self._snapshot_task = asyncio.create_task(self._snapshot())
            if self.state == 'starting':
                if self.size >= MIN_VALID_BYTES:
                    self.state = 'recording'
                    self.recording_since = time.time()
                    self.streamer.set_status(Status.RECORDING)
                elif time.time() - self.started_at > START_TIMEOUT:
                    self.log.append(t('No data in {}s; giving up on this attempt.',
                                      'Sin datos en {}s; se cancela el intento.')
                                    .format(START_TIMEOUT))
                    self.stop_reason = 'timeout'
                    await self._terminate()
                    break
            try:
                await asyncio.wait_for(asyncio.shield(self.proc.wait()), timeout=2)
            except asyncio.TimeoutError:
                continue
        await self.proc.wait()
        await self._finalize()

    async def stop(self) -> None:
        self.manual_stop = True
        if not self.stop_reason:
            self.stop_reason = 'user'
        self.state = 'stopping'
        await self._terminate()

    async def _terminate(self) -> None:
        if not self.proc or self.proc.returncode is not None:
            return
        if os.name == 'nt':
            # kill the whole tree: streamlink spawns an ffmpeg child that a plain
            # terminate() would leave orphaned and recording forever
            await _run_quiet(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'], timeout=15)
        else:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(self.proc.wait()), timeout=10)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.kill()

    async def _finalize(self) -> None:
        self.state = 'processing'
        await self._stop_snapshot()
        # the chaturbate capture leaves a small helper playlist next to the output
        with contextlib.suppress(OSError):
            self.ts_path.with_suffix('.m3u8').unlink(missing_ok=True)
        src = self._output_file() or self.ts_path
        with contextlib.suppress(OSError):
            if src.exists():
                self.size = src.stat().st_size
        saved = False
        s = self.streamer
        s.last_log = list(self.log)

        exit_code = self.proc.returncode if self.proc else None
        if self.stop_reason == 'user':
            exit_label = t('stopped by hand', 'detenido a mano')
        elif self.stop_reason == 'app':
            exit_label = t('app closed', 'app cerrada')
        elif self.stop_reason == 'timeout':
            exit_label = t('no data at start (recorder exited with code {})',
                           'sin datos de inicio (grabador salió con código {})').format(exit_code)
        else:
            exit_label = t('the recorder ended on its own (code {})',
                           'el grabador terminó solo (código {})').format(exit_code)

        if self.size < MIN_VALID_BYTES:
            # nothing worth keeping; clear the scraps so the library stays clean
            with contextlib.suppress(OSError):
                src.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                self.ts_path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                thumb_path_for(self.mp4_path).unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                thumb_path_for(self.mp4_path).parent.rmdir()   # .thumbs, if now empty
            with contextlib.suppress(OSError):
                if self.ts_path.parent != self.cfg.recordings_path and \
                        not any(self.ts_path.parent.iterdir()):
                    self.ts_path.parent.rmdir()
            if self.stop_reason == 'user':
                hint = t('you stopped it before anything useful was captured',
                         'lo detuviste antes de que grabara nada útil')
            elif self.stop_reason == 'timeout':
                hint = t('no video arrived: the stream was not public or never started',
                         'no llegó vídeo: el directo no estaba público o no arrancó')
            elif self.stop_reason == 'app':
                hint = t('the app closed during startup', 'se cerró la app durante el arranque')
            else:
                hint = t('it ended before capturing anything useful (stream over, dropped, or refused by the site?)',
                         'cerró antes de grabar nada útil (¿terminó el directo, se cortó, o lo rechazó la plataforma?)')
            reason = t('NOT saved — only {} (minimum {}): {}',
                       'NO guardado — solo {} (mínimo {}): {}').format(
                tools.human_size(self.size), tools.human_size(MIN_VALID_BYTES), hint)
            # stopped by hand: the broadcast is probably still on, so keep it in
            # the live group (the cooldown holds auto-record back, not this)
            s.set_status(Status.ONLINE if self.manual_stop else Status.OFFLINE)
            s.last_error = reason
            s.last_result = ''
            s.cooldown_until = time.time() + COOLDOWN_AFTER_OFFLINE
        else:
            async with _POSTPROCESS:
                final = await remux_to_mp4(src, self.mp4_path) or src
                await make_thumbnail(final)
                duration = await probe_duration(final)
                has_audio = await _has_audio(final)
            final_size = 0
            with contextlib.suppress(OSError):
                final_size = final.stat().st_size
            reason = t('saved {} · {} · {}', 'guardado {} · {} · {}').format(
                final.name, tools.human_duration(duration), tools.human_size(final_size))
            if not has_audio:
                # loud in the log so a "no audio" report is diagnosable at a glance
                reason += t(' · WARNING: no audio track', ' · AVISO: sin pista de audio')
                logbook.event(f'NO AUDIO  {s.username} ({s.platform}) — the capture has '
                              f'no audio stream: {final.name}')
            saved = True
            s.last_result = reason
            s.last_error = ''
            # stopped by hand: the broadcast is probably still on, so keep it in
            # the live group (the cooldown holds auto-record back, not this)
            s.set_status(Status.ONLINE if self.manual_stop else Status.OFFLINE)
            s.cooldown_until = time.time() + (COOLDOWN_AFTER_MANUAL_STOP if self.manual_stop
                                              else COOLDOWN_AFTER_END)

        s.last_cmd = self.cmd
        s.last_exit = exit_label
        s.last_reason = reason
        tail = list(self.log)[-25:]
        logbook.block(
            f'{s.username} ({s.platform}) — {"SAVED" if saved else "NOT SAVED"}',
            [f'command: {self.cmd}',
             f'exit:    {exit_label}',
             f'reason:  {reason}',
             'last recorder lines:',
             *(tail or ['(the recorder printed nothing)'])])
        self.on_finished(self, saved)


async def _run_quiet(cmd: list[str], timeout: float | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=tools.CREATE_NO_WINDOW)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        return -1, 'timeout'
    return proc.returncode or 0, out.decode('utf-8', errors='replace')[-2000:]


async def remux_to_mp4(ts_path: Path, mp4_path: Path) -> Path | None:
    """Wrap a capture into MP4 without re-encoding anything.

    Sync is the capture's job (see platforms.resolve_chaturbate_master), so both
    tracks are copied with their timestamps untouched. The audio_offset_ms setting
    is the one escape hatch: a manual nudge applied here by opening the same file
    twice and shifting the audio input, which moves timestamps but never touches
    the samples.
    """
    ffmpeg = tools.ffmpeg_path()
    if not ffmpeg or not ts_path.exists():
        return None
    offset_ms = getattr(config_mod.load(), 'audio_offset_ms', 0)
    cmd = [ffmpeg, '-y', '-loglevel', 'error']
    if offset_ms:
        cmd += ['-i', str(ts_path), '-itsoffset', f'{offset_ms / 1000:.3f}',
                '-i', str(ts_path), '-map', '0:v:0', '-map', '1:a:0']
    else:
        cmd += ['-i', str(ts_path)]
    # no +faststart: it rewrites the whole file a second time, doubling the disk
    # work per capture. The media route serves byte ranges, so the browser can
    # read the trailing moov anyway.
    cmd += ['-c', 'copy', str(mp4_path)]
    rc, _ = await _run_quiet(cmd, timeout=7200)
    if rc == 0 and mp4_path.exists() and mp4_path.stat().st_size > 0:
        with contextlib.suppress(OSError):
            ts_path.unlink()
        return mp4_path
    with contextlib.suppress(OSError):
        mp4_path.unlink(missing_ok=True)
    return None


def thumb_path_for(video: Path) -> Path:
    return video.parent / '.thumbs' / (video.stem + '.jpg')


async def make_thumbnail(video: Path) -> Path | None:
    ffmpeg = tools.ffmpeg_path()
    if not ffmpeg or not video.exists():
        return None
    thumb = thumb_path_for(video)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    duration = await probe_duration(video)
    # a quarter in usually beats the first frames, which are often black
    offsets = [min(60, int(duration * 0.25)), 3] if duration else [30, 3]
    for offset in offsets:
        rc, _ = await _run_quiet([ffmpeg, '-y', '-loglevel', 'error',
                                  '-ss', str(offset), '-i', str(video),
                                  '-frames:v', '1', '-vf', 'scale=480:-2', str(thumb)],
                                 timeout=120)
        if rc == 0 and thumb.exists() and thumb.stat().st_size > 0:
            return thumb
    return None


async def _has_audio(path: Path) -> bool:
    """Whether the file has an audio stream. Assumes yes when it cannot tell, so a
    missing ffprobe never raises a false 'no audio' alarm."""
    ffprobe = tools.ffprobe_path()
    if not ffprobe or not path.exists():
        return True
    rc, out = await _run_quiet([ffprobe, '-v', 'error', '-select_streams', 'a',
                                '-show_entries', 'stream=codec_type', '-of', 'csv=p=0',
                                str(path)], timeout=60)
    return not (rc == 0 and not out.strip())


async def probe_duration(video: Path) -> float | None:
    ffprobe = tools.ffprobe_path()
    if not ffprobe or not video.exists():
        return None
    rc, out = await _run_quiet([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'default=nw=1:nk=1', str(video)], timeout=60)
    try:
        return float(out.strip().splitlines()[-1]) if rc == 0 and out.strip() else None
    except (ValueError, IndexError):
        return None
