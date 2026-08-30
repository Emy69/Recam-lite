from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    UNKNOWN = 'unknown'
    OFFLINE = 'offline'
    ONLINE = 'online'
    RECORDING = 'recording'


# label + Quasar colour for the UI badges
STATUS_LABELS: dict[Status, tuple[str, str]] = {
    Status.UNKNOWN: ('DESCONOCIDO', 'grey-7'),
    Status.OFFLINE: ('OFFLINE', 'blue-grey-7'),
    Status.ONLINE: ('EN VIVO', 'green-8'),
    Status.RECORDING: ('GRABANDO', 'red-8'),
}


@dataclass
class Streamer:
    url: str
    platform: str
    username: str
    auto_record: bool = True

    # everything below is runtime only; to_json/from_json deliberately skip it
    status: Status = Status.UNKNOWN
    last_check: float = 0.0
    cooldown_until: float = 0.0
    last_result: str = ''
    last_error: str = ''
    last_log: list[str] = field(default_factory=list)
    last_cmd: str = ''
    last_exit: str = ''       # "código 1", "detenido a mano", "app cerrada"…
    last_reason: str = ''     # why the file was kept or dropped

    @property
    def key(self) -> str:
        return f'{self.platform}:{self.username.lower()}'

    def to_json(self) -> dict:
        return {'url': self.url, 'platform': self.platform,
                'username': self.username, 'auto_record': self.auto_record}

    @staticmethod
    def from_json(d: dict) -> 'Streamer':
        return Streamer(url=d['url'], platform=d['platform'],
                        username=d['username'], auto_record=d.get('auto_record', True))
