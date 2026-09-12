from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from recam import library as library_mod
from recam import recorder
from recam.library import (Library, LibraryItem, clean_mp4_target,
                           free_path, full_suffix)


@pytest.fixture
def quiet_metadata(monkeypatch):
    async def noop(self, items):
        return None

    monkeypatch.setattr(Library, '_fill_metadata', noop)


def write_video(root: Path, rel: str, size: int = 1000, age: float = 0) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def item_for(path: Path, root: Path) -> LibraryItem:
    return LibraryItem(path=path, rel=path.relative_to(root).as_posix(),
                       streamer=path.parent.name, size=path.stat().st_size,
                       mtime=path.stat().st_mtime, duration=None, thumb=None)


@pytest.mark.parametrize('name, expected', [
    ('clip.ts', 'clip.mp4'),
    ('clip.ts.mp4', 'clip.mp4'),
    ('clip.TS', 'clip.mp4'),
    ('clip.mkv', 'clip.mp4'),
    ('clip.mp4', 'clip.mp4'),
])
def test_clean_mp4_target(name, expected):
    assert clean_mp4_target(Path('C:/rec') / name).name == expected


@pytest.mark.parametrize('name, raw', [
    ('clip.ts', True),
    ('clip.TS', True),
    ('clip.ts.mp4', True),
    ('clip.mp4', False),
    ('clip.mkv', False),
])
def test_is_ts_marks_captures_browsers_cannot_play(name, raw):
    item = LibraryItem(path=Path('C:/rec') / name, rel=name, streamer='emy',
                       size=1, mtime=1, duration=None, thumb=None)
    assert item.is_ts is raw


async def test_scan_lists_videos_newest_first(cfg, quiet_metadata):
    root = cfg.recordings_path
    write_video(root, 'emy/old.mp4', age=7200)
    write_video(root, 'emy/new.mp4', age=60)
    items = await Library(cfg).scan()
    assert [i.path.name for i in items] == ['new.mp4', 'old.mp4']
    assert {i.streamer for i in items} == {'emy'}


async def test_scan_ignores_thumbnails_and_other_files(cfg, quiet_metadata):
    root = cfg.recordings_path
    write_video(root, 'emy/clip.mp4')
    (root / 'emy' / '.thumbs').mkdir(parents=True, exist_ok=True)
    (root / 'emy' / '.thumbs' / 'clip.jpg').write_bytes(b'jpeg')
    (root / 'emy' / 'notes.txt').write_text('hi', encoding='utf-8')
    (root / 'emy' / 'capture.m3u8').write_text('#EXTM3U', encoding='utf-8')
    items = await Library(cfg).scan()
    assert [i.path.name for i in items] == ['clip.mp4']


async def test_scan_leaves_a_capture_in_flight_out(cfg, quiet_metadata):
    root = cfg.recordings_path
    running = write_video(root, 'emy/running.ts')
    write_video(root, 'emy/done.mp4')
    lib = Library(cfg)
    lib.active_paths.update({running, running.with_suffix('.mp4')})
    items = await lib.scan()
    assert [i.path.name for i in items] == ['done.mp4']


async def test_scan_attaches_an_existing_thumbnail(cfg, quiet_metadata):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    thumb = recorder.thumb_path_for(video)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b'jpeg')
    items = await Library(cfg).scan()
    assert items[0].thumb == thumb


async def test_scan_reuses_a_cached_duration(cfg, quiet_metadata):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4', size=1234)
    library_mod.CACHE_FILE.write_text(
        json.dumps({str(video): {'size': 1234, 'duration': 42.0}}), encoding='utf-8')
    items = await Library(cfg).scan()
    assert items[0].duration == 42.0


async def test_scan_distrusts_the_cache_when_the_file_changed(cfg, quiet_metadata):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4', size=1234)
    library_mod.CACHE_FILE.write_text(
        json.dumps({str(video): {'size': 99, 'duration': 42.0}}), encoding='utf-8')
    items = await Library(cfg).scan()
    assert items[0].duration is None


async def test_scan_forgets_files_that_disappeared(cfg, quiet_metadata):
    root = cfg.recordings_path
    gone = root / 'emy' / 'gone.mp4'
    library_mod.CACHE_FILE.write_text(
        json.dumps({str(gone): {'size': 1, 'duration': 1.0}}), encoding='utf-8')
    lib = Library(cfg)
    await lib.scan()
    assert str(gone) not in lib._cache


async def test_fill_metadata_probes_only_what_is_missing(cfg, monkeypatch):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    probed = []

    async def fake_duration(path):
        probed.append(path)
        return 12.0

    async def fake_thumb(path):
        return recorder.thumb_path_for(path)

    monkeypatch.setattr(recorder, 'probe_duration', fake_duration)
    monkeypatch.setattr(recorder, 'make_thumbnail', fake_thumb)
    lib = Library(cfg)
    item = item_for(video, root)
    await lib._fill_metadata([item])
    assert probed == [video]
    assert item.duration == 12.0
    assert lib._cache[str(video)]['duration'] == 12.0
    assert lib.version == 1


def test_rename_moves_the_video_and_its_thumbnail(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    thumb = recorder.thumb_path_for(video)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b'jpeg')
    lib = Library(cfg)
    target = lib.rename(item_for(video, root), 'sesion buena')
    assert target.name == 'sesion buena.mp4'
    assert target.exists() and not video.exists()
    assert recorder.thumb_path_for(target).exists()
    assert lib.version == 1


def test_rename_sanitizes_the_new_name(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    target = Library(cfg).rename(item_for(video, root), 'a/b:c')
    assert target.name == 'a_b_c.mp4'


def test_rename_refuses_an_empty_name(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    with pytest.raises(ValueError):
        Library(cfg).rename(item_for(video, root), '   ')


def test_rename_refuses_to_overwrite(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    write_video(root, 'emy/taken.mp4')
    with pytest.raises(ValueError):
        Library(cfg).rename(item_for(video, root), 'taken')
    assert video.exists()


def test_rename_to_the_same_name_is_a_no_op(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    assert Library(cfg).rename(item_for(video, root), 'clip') == video
    assert video.exists()


def test_delete_goes_through_the_recycle_bin(cfg, monkeypatch):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    thumb = recorder.thumb_path_for(video)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b'jpeg')
    trashed = []
    monkeypatch.setattr(library_mod, 'send2trash', trashed.append)
    lib = Library(cfg)
    lib._cache[str(video)] = {'size': 1, 'duration': 1.0}
    lib.delete(item_for(video, root))
    assert trashed == [str(video), str(thumb)]
    assert str(video) not in lib._cache
    assert lib.version == 1


async def test_convert_ts_produces_a_playable_name(cfg, monkeypatch):
    root = cfg.recordings_path
    capture = write_video(root, 'emy/clip.ts')

    async def fake_remux(src, target):
        target.write_bytes(b'mp4')
        src.unlink()
        return target

    monkeypatch.setattr(recorder, 'remux_to_mp4', fake_remux)
    lib = Library(cfg)
    result = await lib.convert_ts(item_for(capture, root))
    assert result == root / 'emy' / 'clip.mp4'
    assert result.exists()
    assert lib.version == 1


async def test_convert_ts_leaves_a_real_mp4_alone(cfg):
    root = cfg.recordings_path
    video = write_video(root, 'emy/clip.mp4')
    lib = Library(cfg)
    assert await lib.convert_ts(item_for(video, root)) == video
    assert lib.version == 0


def test_cache_survives_a_corrupt_file(cfg):
    library_mod.CACHE_FILE.write_text('{oops', encoding='utf-8')
    assert Library(cfg)._cache == {}


def test_is_active_covers_the_old_style_capture_name(cfg):
    lib = Library(cfg)
    base = cfg.recordings_path / 'emy' / 'clip'
    lib.active_paths.update({base.with_suffix('.ts'), base.with_suffix('.mp4')})
    assert lib._is_active(base.with_name('clip.ts.mp4'))
    assert not lib._is_active(cfg.recordings_path / 'emy' / 'other.mp4')


def test_rename_keeps_the_raw_capture_marker(cfg):
    root = cfg.recordings_path
    capture = write_video(root, 'emy/clip.ts.mp4')
    target = Library(cfg).rename(item_for(capture, root), 'sesion')
    renamed = LibraryItem(path=target, rel=target.name, streamer='emy', size=1,
                          mtime=1, duration=None, thumb=None)
    assert renamed.is_ts


async def test_convert_ts_does_not_overwrite_another_recording(cfg, monkeypatch):
    root = cfg.recordings_path
    capture = write_video(root, 'emy/clip.ts')
    existing = write_video(root, 'emy/clip.mp4', size=4242)
    monkeypatch.setattr(recorder, 'ffmpeg_path', lambda: 'ffmpeg.exe', raising=False)

    async def fake_remux(src, target):
        target.write_bytes(b'overwritten')
        return target

    monkeypatch.setattr(recorder, 'remux_to_mp4', fake_remux)
    await Library(cfg).convert_ts(item_for(capture, root))
    assert existing.stat().st_size == 4242


async def test_scan_only_prunes_its_own_folder(cfg, quiet_metadata, isolated):
    sibling = isolated.root / (cfg.recordings_path.name + '2')
    sibling.mkdir()
    other = sibling / 'clip.mp4'
    other.write_bytes(b'x')
    library_mod.CACHE_FILE.write_text(
        json.dumps({str(other): {'size': 1, 'duration': 9.0}}), encoding='utf-8')
    lib = Library(cfg)
    write_video(cfg.recordings_path, 'emy/clip.mp4')
    await lib.scan()
    assert str(other) in lib._cache


@pytest.mark.parametrize('name, expected', [
    ('clip.mp4', '.mp4'),
    ('clip.ts', '.ts'),
    ('clip.ts.mp4', '.ts.mp4'),
    ('clip.TS.MP4', '.TS.MP4'),
])
def test_full_suffix(name, expected):
    assert full_suffix(Path('C:/rec') / name) == expected


def test_free_path_steps_aside_instead_of_overwriting(cfg):
    root = cfg.recordings_path
    taken = write_video(root, 'emy/clip.mp4')
    assert free_path(taken).name == 'clip (2).mp4'
    write_video(root, 'emy/clip (2).mp4')
    assert free_path(taken).name == 'clip (3).mp4'
    assert free_path(root / 'emy' / 'libre.mp4').name == 'libre.mp4'
