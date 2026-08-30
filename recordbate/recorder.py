from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import deque
from pathlib import Path

from . import config as config_mod
from . import logbook, platforms, tools
from .models import Status, Streamer

MIN_VALID_BYTES = 200_000          # below this there was no real stream to keep
START_TIMEOUT = 120                # seconds without data before giving up
COOLDOWN_AFTER_END = 30            # short retry: the stream may have just hiccuped
COOLDOWN_AFTER_MANUAL_STOP = 600   # don't fight the user who just pressed stop
COOLDOWN_AFTER_OFFLINE = 15


class Recording:
    """One capture in flight: subprocess + watchdog + post-processing."""

    def __init__(self, streamer: Streamer, cfg: config_mod.Config, on_finished) -> None:
        self.streamer = streamer
        self.cfg = cfg
        self.on_finished = on_finished          # callback(recording, saved: bool)
        stem = config_mod.build_output_stem(cfg, streamer)
        self.ts_path = (cfg.recordings_path / stem).with_suffix('.ts')
        self.mp4_path = self.ts_path.with_suffix('.mp4')
        self.state = 'iniciando'                # iniciando | grabando | deteniendo | procesando
        self.started_at = time.time()
        self.recording_since: float | None = None
        self.rec_file: Path | None = None
        self.size = 0
        self.manual_stop = False
        self.stop_reason = ''                   # '', 'usuario', 'app', 'timeout'
        self.cmd = ''
        self.log: deque[str] = deque(maxlen=200)
        self.proc: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task | None = None

    @property
    def elapsed(self) -> float:
        return time.time() - (self.recording_since or self.started_at)

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
        # read the nudge fresh so changing it from the CLI or the GUI applies to the
        # next capture without restarting
        offset_ms = getattr(config_mod.load(), 'audio_offset_ms', 0)
        cmd = await platforms.build_record_cmd(
            self.streamer.platform, self.streamer.username, self.cfg.quality, self.ts_path,
            audio_offset_ms=offset_ms)
        self.cmd = ' '.join(cmd)
        logbook.event(f'INICIO  {self.streamer.username} ({self.streamer.platform})  →  {self.cmd}')
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
            if self.state == 'iniciando':
                if self.size >= MIN_VALID_BYTES:
                    self.state = 'grabando'
                    self.recording_since = time.time()
                    self.streamer.status = Status.RECORDING
                elif time.time() - self.started_at > START_TIMEOUT:
                    self.log.append(f'Sin datos en {START_TIMEOUT}s; se cancela el intento.')
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
            self.stop_reason = 'usuario'
        self.state = 'deteniendo'
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
        self.state = 'procesando'
        src = self._output_file() or self.ts_path
        with contextlib.suppress(OSError):
            if src.exists():
                self.size = src.stat().st_size
        saved = False
        s = self.streamer
        s.last_log = list(self.log)

        exit_code = self.proc.returncode if self.proc else None
        if self.stop_reason == 'usuario':
            exit_label = 'detenido a mano'
        elif self.stop_reason == 'app':
            exit_label = 'app cerrada'
        elif self.stop_reason == 'timeout':
            exit_label = f'sin datos de inicio (grabador salió con código {exit_code})'
        else:
            exit_label = f'el grabador terminó solo (código {exit_code})'

        if self.size < MIN_VALID_BYTES:
            # nothing worth keeping; clear the scraps so the library stays clean
            with contextlib.suppress(OSError):
                src.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                self.ts_path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                if self.ts_path.parent != self.cfg.recordings_path and \
                        not any(self.ts_path.parent.iterdir()):
                    self.ts_path.parent.rmdir()
            if self.stop_reason == 'usuario':
                hint = 'lo detuviste antes de que grabara nada útil'
            elif self.stop_reason == 'timeout':
                hint = 'no llegó vídeo: el directo no estaba público o no arrancó'
            elif self.stop_reason == 'app':
                hint = 'se cerró la app durante el arranque'
            else:
                hint = ('cerró antes de grabar nada útil (¿terminó el directo, se cortó, '
                        'o lo rechazó la plataforma?)')
            reason = (f'NO guardado — solo {tools.human_size(self.size)} '
                      f'(mínimo {tools.human_size(MIN_VALID_BYTES)}): {hint}')
            s.status = Status.UNKNOWN if self.manual_stop else Status.OFFLINE
            s.last_error = reason
            s.last_result = ''
            s.cooldown_until = time.time() + COOLDOWN_AFTER_OFFLINE
        else:
            final = await remux_to_mp4(src, self.mp4_path) or src
            await make_thumbnail(final)
            duration = await probe_duration(final)
            final_size = 0
            with contextlib.suppress(OSError):
                final_size = final.stat().st_size
            reason = (f'guardado {final.name} · {tools.human_duration(duration)} · '
                      f'{tools.human_size(final_size)}')
            saved = True
            s.last_result = reason
            s.last_error = ''
            s.status = Status.UNKNOWN if self.manual_stop else Status.OFFLINE
            s.cooldown_until = time.time() + (COOLDOWN_AFTER_MANUAL_STOP if self.manual_stop
                                              else COOLDOWN_AFTER_END)

        s.last_cmd = self.cmd
        s.last_exit = exit_label
        s.last_reason = reason
        tail = list(self.log)[-25:]
        logbook.block(
            f'{s.username} ({s.platform}) — {"GUARDADO" if saved else "SIN GUARDAR"}',
            [f'comando: {self.cmd}',
             f'salida:  {exit_label}',
             f'motivo:  {reason}',
             'últimas líneas del grabador:',
             *(tail or ['(el grabador no imprimió nada)'])])
        self.on_finished(self, saved)


async def _run_quiet(cmd: list[str], timeout: float | None = None,
                     limit: int = 2000) -> tuple[int, str]:
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
    return proc.returncode or 0, out.decode('utf-8', errors='replace')[-limit:]


async def _track_length(path: Path, stream: str, tail_from: float | None) -> float | None:
    """Length of one track, measured from packet timestamps.

    ffprobe's stream=duration is only an estimate on MPEG-TS — off by enough to
    leave over a millisecond per second of drift behind. Packet timestamps are
    exact, so we read the first few packets and the ones around the end (a seek,
    never a full scan of a multi-gigabyte capture) and take the span.
    """
    ffprobe = tools.ffprobe_path()
    if not ffprobe:
        return None
    args = [ffprobe, '-v', 'error', '-select_streams', stream,
            '-show_entries', 'packet=pts_time,duration_time', '-of', 'csv=p=0']
    if tail_from is not None:
        args += ['-read_intervals', f'%+#60,{tail_from:.3f}%+#20000']
    rc, out = await _run_quiet(args + [str(path)], timeout=600, limit=2_000_000)
    if rc != 0:
        return None
    first = last = prev = None
    tail_dur = 0.0
    for line in out.splitlines():
        pts, _, dur = line.partition(',')
        try:
            start = float(pts)
        except ValueError:
            continue
        if first is None or start < first:
            first = start
        if last is None or start > last:
            prev, last = last, start
            # MPEG-TS leaves duration_time as N/A on video packets, so fall back to
            # the gap to the previous one to account for the last frame
            try:
                tail_dur = float(dur)
            except ValueError:
                tail_dur = last - prev if prev is not None else 0.0
        elif prev is None or start > prev:
            prev = start
    if first is None or last is None or last <= first:
        return None
    return last - first + tail_dur


async def _av_lengths(path: Path) -> tuple[float | None, float | None]:
    total = await probe_duration(path)
    # with a short capture there is nothing to gain from seeking; read it whole
    tail_from = max(total - 8, 0) if total and total > 20 else None
    video, audio = await asyncio.gather(_track_length(path, 'v', tail_from),
                                        _track_length(path, 'a', tail_from))
    return video, audio


async def remux_to_mp4(ts_path: Path, mp4_path: Path) -> Path | None:
    """Wrap a capture into MP4. Video is always copied, never re-encoded.

    Cam sites hand out audio and video as two independent HLS streams whose clocks
    run at slightly different rates, so the sound ends up a fraction of a percent
    longer than the picture: unnoticeable in the first minutes, seconds out of sync
    after a long session. When that happens the audio is stretched back with atempo
    so both tracks end together; otherwise it is copied untouched.
    """
    ffmpeg = tools.ffmpeg_path()
    if not ffmpeg or not ts_path.exists():
        return None
    vlen, alen = await _av_lengths(ts_path)
    audio_args = ['-c:a', 'copy']
    if vlen and alen and abs(vlen - alen) > 0.08 and 0.9 < alen / vlen < 1.1:
        audio_args = ['-c:a', 'aac', '-b:a', '160k', '-af', f'atempo={alen / vlen:.6f}']
    rc, _ = await _run_quiet([ffmpeg, '-y', '-loglevel', 'error', '-i', str(ts_path),
                              '-c:v', 'copy', *audio_args, '-movflags', '+faststart',
                              str(mp4_path)], timeout=7200)
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
