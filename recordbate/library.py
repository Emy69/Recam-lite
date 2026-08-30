from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from send2trash import send2trash

from . import config as config_mod
from . import recorder

CACHE_FILE = config_mod.DATA_DIR / 'library_cache.json'
VIDEO_EXTS = {'.mp4', '.ts', '.mkv', '.m4v'}


@dataclass
class LibraryItem:
    path: Path
    rel: str            # relative to the recordings folder, '/'-separated
    streamer: str
    size: int
    mtime: float
    duration: float | None
    thumb: Path | None

    @property
    def is_ts(self) -> bool:
        # raw capture: a .ts, or the 'X.ts.mp4' older versions produced — either way
        # mpegts, which browsers refuse to play
        name = self.path.name.lower()
        return name.endswith('.ts') or name.endswith('.ts.mp4')


def clean_mp4_target(path: Path) -> Path:
    """Where a raw capture (.ts / .ts.mp4) should end up once remuxed."""
    name = path.name
    for suffix in ('.ts.mp4', '.ts'):
        if name.lower().endswith(suffix):
            return path.with_name(name[:-len(suffix)] + '.mp4')
    return path.with_suffix('.mp4')


class Library:
    def __init__(self, cfg: config_mod.Config) -> None:
        self.cfg = cfg
        self.items: list[LibraryItem] = []
        self._cache: dict[str, dict] = self._load_cache()
        # files belonging to captures in flight; the monitor registers them here
        self.active_paths: set[Path] = set()
        # bumped on every change, so each UI page knows whether it must rescan
        self.version = 0

    def bump(self) -> None:
        self.version += 1

    def _is_active(self, path: Path) -> bool:
        # prefix match: the real file may be 'X.ts.mp4' while active_paths holds
        # 'X.ts' and 'X.mp4'
        s = str(path)
        return any(s.startswith(str(p)) for p in self.active_paths)

    def _load_cache(self) -> dict:
        with contextlib.suppress(Exception):
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        return {}

    def _save_cache(self) -> None:
        with contextlib.suppress(Exception):
            CACHE_FILE.write_text(json.dumps(self._cache), encoding='utf-8')

    @staticmethod
    def _walk(root: Path) -> list[tuple[Path, int, float]]:
        out = []
        for path in root.rglob('*'):
            if path.suffix.lower() not in VIDEO_EXTS or '.thumbs' in path.parts:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((path, st.st_size, st.st_mtime))
        return out

    async def scan(self) -> list[LibraryItem]:
        root = self.cfg.recordings_path
        root.mkdir(parents=True, exist_ok=True)
        # off the event loop: walking a big library stats hundreds of files
        entries = await asyncio.to_thread(self._walk, root)

        # drop cache entries for files that vanished — but only under THIS root, so
        # switching the recordings folder back and forth doesn't wipe the cache
        root_prefix = str(root)
        stale = {k for k in self._cache if k.startswith(root_prefix)} \
            - {str(p) for p, _, _ in entries}
        for key in stale:
            del self._cache[key]

        found: list[LibraryItem] = []
        pending: list[LibraryItem] = []
        for path, size, mtime in entries:
            if self._is_active(path):
                continue
            cached = self._cache.get(str(path))
            item = LibraryItem(
                path=path,
                rel=path.relative_to(root).as_posix(),
                streamer=path.parent.name if path.parent != root else '',
                size=size, mtime=mtime,
                duration=cached.get('duration') if cached and cached.get('size') == size else None,
                thumb=None,
            )
            thumb = recorder.thumb_path_for(path)
            if thumb.exists():
                item.thumb = thumb
            found.append(item)
            if item.duration is None or item.thumb is None:
                pending.append(item)
        found.sort(key=lambda i: i.mtime, reverse=True)
        self.items = found
        if stale:
            self._save_cache()
        if pending:
            asyncio.create_task(self._fill_metadata(pending))
        return found

    async def _fill_metadata(self, items: list[LibraryItem]) -> None:
        sem = asyncio.Semaphore(2)
        changed = False

        async def one(item: LibraryItem) -> None:
            nonlocal changed
            async with sem:
                # only a file we have never seen is worth a refresh. An orphan capture
                # still growing gets re-read on every scan, and refreshing on that
                # would spin the library forever.
                is_new = str(item.path) not in self._cache
                if item.duration is None:
                    item.duration = await recorder.probe_duration(item.path)
                    if item.duration is not None:
                        self._cache[str(item.path)] = {'size': item.size,
                                                       'duration': item.duration}
                        changed = changed or is_new
                if item.thumb is None:
                    item.thumb = await recorder.make_thumbnail(item.path)
                    changed = changed or (item.thumb is not None and is_new)

        await asyncio.gather(*(one(i) for i in items))
        self._save_cache()
        if changed:
            self.bump()

    def rename(self, item: LibraryItem, new_stem: str) -> Path:
        new_stem = config_mod.sanitize_segment(new_stem)
        if not new_stem:
            raise ValueError('El nombre no puede quedar vacío')
        target = item.path.with_name(new_stem + item.path.suffix)
        if target == item.path:
            return target
        if target.exists():
            raise ValueError('Ya existe un archivo con ese nombre')
        old_thumb = recorder.thumb_path_for(item.path)
        item.path.rename(target)
        if old_thumb.exists():
            with contextlib.suppress(OSError):
                old_thumb.rename(recorder.thumb_path_for(target))
        self._cache.pop(str(item.path), None)
        self._save_cache()
        self.bump()
        return target

    def delete(self, item: LibraryItem) -> None:
        send2trash(str(item.path))       # recycle bin, never an unlink
        thumb = recorder.thumb_path_for(item.path)
        if thumb.exists():
            with contextlib.suppress(Exception):
                send2trash(str(thumb))
        self._cache.pop(str(item.path), None)
        self._save_cache()
        self.bump()

    async def convert_ts(self, item: LibraryItem) -> Path | None:
        target = clean_mp4_target(item.path)
        if target == item.path:
            return item.path
        result = await recorder.remux_to_mp4(item.path, target)
        if result:
            self._cache.pop(str(item.path), None)
            self.bump()
        return result

    def open_in_explorer(self, item: LibraryItem) -> None:
        subprocess.Popen(['explorer', f'/select,{item.path}'])

    def open_root(self) -> None:
        os.startfile(str(self.cfg.recordings_path))

    def open_external(self, item: LibraryItem) -> None:
        os.startfile(str(item.path))
