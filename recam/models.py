from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .i18n import t


class Status(str, Enum):
    UNKNOWN = 'unknown'
    OFFLINE = 'offline'
    ONLINE = 'online'
    RECORDING = 'recording'


# both mean "there is a broadcast right now"; the panel groups tiles by this
LIVE_STATUSES = (Status.ONLINE, Status.RECORDING)


def status_label(status: Status) -> tuple[str, str]:
    """Display label + Quasar colour for a channel status, in the current language."""
    return {
        Status.UNKNOWN: (t('UNKNOWN', 'DESCONOCIDO'), 'grey-7'),
        Status.OFFLINE: (t('OFFLINE', 'OFFLINE'), 'blue-grey-7'),
        Status.ONLINE: (t('LIVE', 'EN VIVO'), 'green-8'),
        Status.RECORDING: (t('RECORDING', 'GRABANDO'), 'red-8'),
    }[status]


def state_label(state: str) -> str:
    """Display label for a Recording.state value (internal ids stay English)."""
    return {
        'starting': t('starting', 'iniciando'),
        'recording': t('recording', 'grabando'),
        'stopping': t('stopping', 'deteniendo'),
        'processing': t('processing', 'procesando'),
    }.get(state, state)


@dataclass
class Streamer:
    url: str
    platform: str
    username: str
    auto_record: bool = True

    # the live/offline timeline; these two survive restarts (see to_json)
    last_online: float = 0.0            # last moment we saw a broadcast running
    last_broadcast_start: float = 0.0   # start of the latest broadcast the site reported

    # everything below is runtime only; to_json/from_json deliberately skip it
    status: Status = Status.UNKNOWN
    live_since: float = 0.0             # start of the current broadcast (0 = not live)
    viewers: int = 0
    last_check: float = 0.0
    cooldown_until: float = 0.0
    last_result: str = ''
    last_error: str = ''
    last_log: list[str] = field(default_factory=list)
    last_cmd: str = ''
    last_exit: str = ''       # "exit code 1", "stopped by hand", "app closed"…
    last_reason: str = ''     # why the file was kept or dropped

    @property
    def key(self) -> str:
        return f'{self.platform}:{self.username.lower()}'

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES

    def set_status(self, status: Status, started_at: float = 0.0) -> bool:
        """Change the status while keeping the timeline straight.

        `started_at` is the broadcast start the site reported, when known; without
        it the first moment we saw the channel live stands in. Returns True when the
        channel crossed the live/offline line, which is when the persisted fields
        changed and the list is worth saving.
        """
        was_live = self.is_live
        self.status = status
        now = time.time()
        if self.is_live:
            self.last_online = now
            if started_at:
                self.live_since = started_at
                self.last_broadcast_start = started_at
            elif not was_live or not self.live_since:
                self.live_since = now
        else:
            self.live_since = 0.0
            self.viewers = 0
        return was_live != self.is_live

    def to_json(self) -> dict:
        d = {'url': self.url, 'platform': self.platform,
             'username': self.username, 'auto_record': self.auto_record}
        if self.last_online:
            d['last_online'] = round(self.last_online)
        if self.last_broadcast_start:
            d['last_broadcast_start'] = round(self.last_broadcast_start)
        return d

    @staticmethod
    def from_json(d: dict) -> 'Streamer':
        return Streamer(url=d['url'], platform=d['platform'],
                        username=d['username'], auto_record=d.get('auto_record', True),
                        last_online=float(d.get('last_online', 0) or 0),
                        last_broadcast_start=float(d.get('last_broadcast_start', 0) or 0))
