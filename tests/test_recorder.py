from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

from recam import recorder, tools
from recam.models import Status

from conftest import make_streamer


def fake_proc(returncode=0):
    return types.SimpleNamespace(returncode=returncode)


def build(cfg, on_finished=None, username='emy'):
    streamer = make_streamer(username)
    rec = recorder.Recording(streamer, cfg, on_finished or (lambda *_a: None))
    rec.proc = fake_proc()
    return rec


def test_thumb_path_for_hides_the_still_next_to_the_video():
    video = Path('C:/rec/emy/clip.mp4')
    assert recorder.thumb_path_for(video) == Path('C:/rec/emy/.thumbs/clip.jpg')


def test_thumb_is_shared_between_the_capture_and_the_final_file():
    assert recorder.thumb_path_for(Path('a/b.ts')) == recorder.thumb_path_for(
        Path('a/b.mp4'))


def test_recording_derives_its_paths_from_the_template(cfg):
    cfg.filename_template = '{streamer}/clip'
    rec = build(cfg)
    assert rec.ts_path == cfg.recordings_path / 'emy' / 'clip.ts'
    assert rec.mp4_path == cfg.recordings_path / 'emy' / 'clip.mp4'
    assert rec.state == 'starting'


def test_elapsed_counts_from_the_first_bytes(cfg):
    rec = build(cfg)
    rec.started_at = time.time() - 100
    assert rec.elapsed == pytest.approx(100, abs=2)
    rec.recording_since = time.time() - 10
    assert rec.elapsed == pytest.approx(10, abs=2)


def test_output_file_prefers_the_expected_capture(cfg):
    rec = build(cfg)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x' * 10)
    assert rec._output_file() == rec.ts_path


def test_output_file_finds_an_old_style_capture(cfg):
    rec = build(cfg)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = rec.ts_path.with_name(rec.ts_path.name + '.mp4')
    legacy.write_bytes(b'x' * 10)
    assert rec._output_file() == legacy


def test_output_file_is_none_when_nothing_was_written(cfg):
    assert build(cfg)._output_file() is None


def test_live_thumb_is_none_without_a_frame(cfg):
    assert build(cfg).live_thumb is None


def test_live_thumb_points_at_the_media_route(cfg):
    rec = build(cfg)
    thumb = recorder.thumb_path_for(rec.mp4_path)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b'jpeg')
    rel, mtime = rec.live_thumb
    assert rel.endswith('.jpg')
    assert '/.thumbs/' in rel or rel.startswith('.thumbs/')
    assert mtime > 0


async def test_remux_copies_without_re_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')
    seen = {}

    async def fake_run(cmd, timeout=None):
        seen['cmd'] = cmd
        Path(cmd[-1]).write_bytes(b'mp4 data')
        return 0, ''

    monkeypatch.setattr(recorder, '_run_quiet', fake_run)
    source = tmp_path / 'a.ts'
    source.write_bytes(b'ts data')
    target = tmp_path / 'a.mp4'
    assert await recorder.remux_to_mp4(source, target) == target
    assert seen['cmd'].count('-i') == 1
    assert '-c' in seen['cmd'] and 'copy' in seen['cmd']
    assert '-itsoffset' not in seen['cmd']
    assert not source.exists()


async def test_remux_applies_the_manual_audio_nudge(tmp_path, monkeypatch, cfg):
    from recam import config as config_mod
    cfg.audio_offset_ms = 250
    config_mod.save(cfg)
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')
    seen = {}

    async def fake_run(cmd, timeout=None):
        seen['cmd'] = cmd
        Path(cmd[-1]).write_bytes(b'mp4 data')
        return 0, ''

    monkeypatch.setattr(recorder, '_run_quiet', fake_run)
    source = tmp_path / 'a.ts'
    source.write_bytes(b'ts data')
    await recorder.remux_to_mp4(source, tmp_path / 'a.mp4')
    cmd = seen['cmd']
    assert cmd.count('-i') == 2
    assert '0.250' in cmd[cmd.index('-itsoffset') + 1]
    assert '1:a:0' in cmd


async def test_remux_keeps_the_capture_when_ffmpeg_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')

    async def failing(cmd, timeout=None):
        Path(cmd[-1]).write_bytes(b'')
        return 1, 'boom'

    monkeypatch.setattr(recorder, '_run_quiet', failing)
    source = tmp_path / 'a.ts'
    source.write_bytes(b'ts data')
    target = tmp_path / 'a.mp4'
    assert await recorder.remux_to_mp4(source, target) is None
    assert source.exists()
    assert not target.exists()


async def test_remux_needs_ffmpeg_and_a_source(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: None)
    assert await recorder.remux_to_mp4(tmp_path / 'a.ts', tmp_path / 'a.mp4') is None
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')
    assert await recorder.remux_to_mp4(tmp_path / 'missing.ts',
                                       tmp_path / 'a.mp4') is None


async def test_probe_duration_reads_the_last_line(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffprobe_path', lambda: 'ffprobe.exe')
    video = tmp_path / 'a.mp4'
    video.write_bytes(b'x')

    async def fake_run(_cmd, timeout=None):
        return 0, 'warning\n123.5\n'

    monkeypatch.setattr(recorder, '_run_quiet', fake_run)
    assert await recorder.probe_duration(video) == 123.5


async def test_probe_duration_is_none_when_ffprobe_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffprobe_path', lambda: 'ffprobe.exe')
    video = tmp_path / 'a.mp4'
    video.write_bytes(b'x')

    async def fake_run(_cmd, timeout=None):
        return 0, 'N/A\n'

    monkeypatch.setattr(recorder, '_run_quiet', fake_run)
    assert await recorder.probe_duration(video) is None


async def test_probe_duration_needs_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffprobe_path', lambda: None)
    assert await recorder.probe_duration(tmp_path / 'a.mp4') is None


async def test_has_audio_assumes_yes_without_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffprobe_path', lambda: None)
    assert await recorder._has_audio(tmp_path / 'a.mp4') is True


async def test_has_audio_detects_a_silent_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'ffprobe_path', lambda: 'ffprobe.exe')
    video = tmp_path / 'a.mp4'
    video.write_bytes(b'x')

    async def empty(_cmd, timeout=None):
        return 0, '\n'

    monkeypatch.setattr(recorder, '_run_quiet', empty)
    assert await recorder._has_audio(video) is False

    async def with_audio(_cmd, timeout=None):
        return 0, 'audio\n'

    monkeypatch.setattr(recorder, '_run_quiet', with_audio)
    assert await recorder._has_audio(video) is True


async def test_finalize_drops_a_capture_that_never_got_data(cfg):
    finished = []
    rec = build(cfg, on_finished=lambda r, saved: finished.append(saved))
    rec.streamer.set_status(Status.RECORDING)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x' * 1000)
    rec.size = 1000
    await rec._finalize()
    assert finished == [False]
    assert not rec.ts_path.exists()
    assert rec.streamer.status is Status.OFFLINE
    assert rec.streamer.cooldown_until > time.time()
    assert 'NOT saved' in rec.streamer.last_error


async def test_finalize_keeps_a_hand_stopped_channel_in_the_live_group(cfg):
    rec = build(cfg)
    rec.streamer.set_status(Status.RECORDING)
    rec.manual_stop = True
    rec.stop_reason = 'user'
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x')
    rec.size = 1
    await rec._finalize()
    assert rec.streamer.status is Status.ONLINE
    assert 'stopped it' in rec.streamer.last_error


async def test_finalize_removes_the_helper_playlist(cfg):
    rec = build(cfg)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    helper = rec.ts_path.with_suffix('.m3u8')
    helper.write_text('#EXTM3U', encoding='utf-8')
    rec.ts_path.write_bytes(b'x')
    await rec._finalize()
    assert not helper.exists()


async def test_finalize_saves_a_real_capture(cfg, monkeypatch):
    finished = []
    rec = build(cfg, on_finished=lambda r, saved: finished.append(saved))
    rec.streamer.set_status(Status.RECORDING)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x' * (recorder.MIN_VALID_BYTES + 10))

    async def fake_remux(src, target):
        target.write_bytes(b'y' * 500_000)
        src.unlink()
        return target

    monkeypatch.setattr(recorder, 'remux_to_mp4', fake_remux)
    monkeypatch.setattr(recorder, 'make_thumbnail', lambda _p: _async(None))
    monkeypatch.setattr(recorder, 'probe_duration', lambda _p: _async(61.0))
    monkeypatch.setattr(recorder, '_has_audio', lambda _p: _async(True))
    await rec._finalize()
    assert finished == [True]
    assert rec.mp4_path.exists()
    assert not rec.ts_path.exists()
    assert '1:01' in rec.streamer.last_result
    assert rec.streamer.last_error == ''
    assert rec.streamer.status is Status.OFFLINE


async def test_finalize_warns_about_a_capture_without_audio(cfg, monkeypatch):
    rec = build(cfg)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x' * (recorder.MIN_VALID_BYTES + 10))

    async def fake_remux(src, target):
        target.write_bytes(b'y' * 500_000)
        return target

    monkeypatch.setattr(recorder, 'remux_to_mp4', fake_remux)
    monkeypatch.setattr(recorder, 'make_thumbnail', lambda _p: _async(None))
    monkeypatch.setattr(recorder, 'probe_duration', lambda _p: _async(10.0))
    monkeypatch.setattr(recorder, '_has_audio', lambda _p: _async(False))
    await rec._finalize()
    assert 'no audio track' in rec.streamer.last_result


async def test_finalize_reports_how_the_recorder_ended(cfg):
    rec = build(cfg)
    rec.proc = fake_proc(returncode=1)
    rec.ts_path.parent.mkdir(parents=True, exist_ok=True)
    rec.ts_path.write_bytes(b'x')
    await rec._finalize()
    assert 'code 1' in rec.streamer.last_exit


async def _async(value):
    return value
