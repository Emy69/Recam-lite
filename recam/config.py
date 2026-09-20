from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from . import AUTHOR, LINKS  # noqa: F401  — re-exported for callers using config.*
from .models import Streamer

if getattr(sys, 'frozen', False):
    # frozen build: everything lives next to the executable (portable app)
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
CONFIG_FILE = DATA_DIR / 'config.json'
STREAMERS_FILE = DATA_DIR / 'streamers.json'

DEFAULT_TEMPLATE = '{streamer}/{date} {time} [{platform}]'


@dataclass
class Config:
    recordings_dir: str = str(BASE_DIR / 'grabaciones')
    poll_seconds: int = 60
    quality: str = 'best'          # best | 1080p | 720p | 480p
    max_concurrent: int = 4
    lan_access: bool = False
    filename_template: str = DEFAULT_TEMPLATE
    port: int = 8211
    # Manual audio nudge, applied when a capture is remuxed to MP4. Positive
    # delays the audio, negative pulls it forward. Normally 0: capture keeps the
    # source timeline, so recordings come out aligned on their own.
    audio_offset_ms: int = 0
    language: str = 'en'           # 'en' | 'es'
    show_beta_notice: bool = True  # the test-build welcome, until dismissed for good

    @property
    def recordings_path(self) -> Path:
        return Path(self.recordings_dir)


_LIMITS = {'poll_seconds': (15, 3600), 'max_concurrent': (1, 20),
           'audio_offset_ms': (-2000, 2000), 'port': (1, 65535)}


def _as_type_of(default, value):
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(value)
        return value
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ValueError(value)
        return int(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError(value)
        return value
    return value


def load() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            valid = {f.name for f in fields(Config)}
            for key, value in data.items():
                if key not in valid:
                    continue
                try:
                    clean = _as_type_of(getattr(cfg, key), value)
                except (TypeError, ValueError):
                    continue
                if key in _LIMITS:
                    low, high = _LIMITS[key]
                    clean = max(low, min(high, clean))
                setattr(cfg, key, clean)
        except Exception:
            pass   # a corrupt config should not stop the app from starting
    try:
        cfg.recordings_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        cfg.recordings_dir = Config().recordings_dir
        cfg.recordings_path.mkdir(parents=True, exist_ok=True)
    return cfg


def save(cfg: Config) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False),
                           encoding='utf-8')


# Channels saved on a platform this build does not record — a list written by a
# build that enabled more sites, or edited by hand. The gate on adding a channel
# never sees these, so without this they would be polled and recorded anyway.
#
# They are held aside rather than dropped: the list belongs to whoever wrote it,
# and a build that does enable the site has to find them again. load_streamers()
# keeps them out of the engine and save_streamers() puts them back in the file.
_parked: list[dict] = []


def load_streamers() -> list[Streamer]:
    """The channels this build can actually record. See _parked for the rest."""
    global _parked
    _parked = []
    if not STREAMERS_FILE.exists():
        return []
    # at call time, not at import: logbook imports this module, so naming either
    # of these at the top would be a cycle
    from . import platforms
    try:
        data = json.loads(STREAMERS_FILE.read_text(encoding='utf-8'))
        kept, parked = [], []
        for entry in data:
            if entry.get('platform') in platforms.ENABLED_PLATFORMS:
                kept.append(Streamer.from_json(entry))
            else:
                parked.append(entry)
    except Exception:
        return []
    _parked = parked
    if parked:
        sites = sorted({str(e.get('platform')) for e in parked})
        from . import logbook
        logbook.event(f'{len(parked)} channel(s) on {", ".join(sites)} are in the list '
                      f'but this build does not record them: left alone, not polled')
    return kept


def save_streamers(streamers: list[Streamer]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STREAMERS_FILE.write_text(
        json.dumps([s.to_json() for s in streamers] + _parked,
                   indent=2, ensure_ascii=False),
        encoding='utf-8')


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_DEVICE_NAMES = {'con', 'prn', 'aux', 'nul',
                 *(f'com{n}' for n in range(1, 10)),
                 *(f'lpt{n}' for n in range(1, 10))}


def sanitize_segment(name: str) -> str:
    clean = _ILLEGAL.sub('_', name).strip(' .')
    if clean.split('.')[0].lower() in _DEVICE_NAMES:
        clean = '_' + clean
    return clean or '_'


def build_output_stem(cfg: Config, streamer: Streamer) -> Path:
    """Extension-less path, relative to the recordings folder."""
    now = datetime.now()
    values = {
        'streamer': streamer.username,
        'platform': streamer.platform,
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H-%M-%S'),
    }
    template = cfg.filename_template or DEFAULT_TEMPLATE
    try:
        raw = template.format(**values)
    except (KeyError, IndexError, ValueError):
        raw = DEFAULT_TEMPLATE.format(**values)
    parts = [sanitize_segment(p) for p in re.split(r'[/\\]+', raw) if p.strip()]
    return Path(*parts) if parts else Path(sanitize_segment(streamer.username))
