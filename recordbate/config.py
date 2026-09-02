from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

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


def load() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            valid = {f.name for f in fields(Config)}
            for key, value in data.items():
                if key in valid:
                    setattr(cfg, key, value)
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


def load_streamers() -> list[Streamer]:
    if STREAMERS_FILE.exists():
        try:
            data = json.loads(STREAMERS_FILE.read_text(encoding='utf-8'))
            return [Streamer.from_json(d) for d in data]
        except Exception:
            pass
    return []


def save_streamers(streamers: list[Streamer]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STREAMERS_FILE.write_text(
        json.dumps([s.to_json() for s in streamers], indent=2, ensure_ascii=False),
        encoding='utf-8')


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_segment(name: str) -> str:
    clean = _ILLEGAL.sub('_', name).strip(' .')
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
