from __future__ import annotations

import asyncio
import dataclasses
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recam import config as config_mod          # noqa: E402
from recam import i18n                          # noqa: E402
from recam import library as library_mod        # noqa: E402
from recam import logbook                       # noqa: E402
from recam import platforms                     # noqa: E402
from recam import status as status_mod          # noqa: E402
from recam import tools                         # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    recordings = tmp_path / 'grabaciones'
    data.mkdir()
    recordings.mkdir()

    @dataclasses.dataclass
    class IsolatedConfig(config_mod.Config):
        recordings_dir: str = str(recordings)

    monkeypatch.setattr(config_mod, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(config_mod, 'DATA_DIR', data)
    monkeypatch.setattr(config_mod, 'CONFIG_FILE', data / 'config.json')
    monkeypatch.setattr(config_mod, 'STREAMERS_FILE', data / 'streamers.json')
    monkeypatch.setattr(config_mod, 'Config', IsolatedConfig)
    monkeypatch.setattr(status_mod, 'STATUS_FILE', data / 'status.json')
    monkeypatch.setattr(status_mod, 'COMMANDS_DIR', data / 'commands')
    monkeypatch.setattr(library_mod, 'CACHE_FILE', data / 'library_cache.json')
    monkeypatch.setattr(logbook, 'LOG_FILE', data / 'recam.log')
    monkeypatch.setattr(logbook, '_logger', None)
    _reset_logger()

    i18n.set_language('en')
    tools.find_tool.cache_clear()
    monkeypatch.setattr(platforms, '_CB_THROTTLE',
                        platforms._HostThrottle(min_interval=0.0))

    yield types.SimpleNamespace(root=tmp_path, data=data, recordings=recordings)

    tools.find_tool.cache_clear()
    i18n.set_language('en')
    _reset_logger()


def _reset_logger() -> None:
    import logging
    lg = logging.getLogger('recam')
    for handler in list(lg.handlers):
        lg.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


@pytest.fixture
def cfg(isolated):
    return config_mod.Config()


@pytest.fixture
def recordings(isolated):
    return isolated.recordings


def make_streamer(username='emy', platform='chaturbate', **kwargs):
    from recam.models import Streamer
    return Streamer(url=platforms.canonical_url(platform, username),
                    platform=platform, username=username, **kwargs)


@pytest.fixture
def streamer():
    return make_streamer()


class FakeResponse:
    def __init__(self, status_code=200, text='', json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        return self._json


class FakeClient:
    """Stands in for httpx.AsyncClient: answers from a url -> FakeResponse map."""

    def __init__(self, routes):
        self.routes = routes
        self.requested: list[str] = []

    async def get(self, url, *_a, **_kw):
        self.requested.append(url)
        for pattern, response in self.routes.items():
            if pattern in url:
                return response
        return FakeResponse(404, '')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def aclose(self):
        return None


@pytest.fixture
def fake_http(monkeypatch):
    """Route every httpx.AsyncClient built inside recam.platforms to a fake."""
    holder = {}

    def install(routes):
        client = FakeClient(routes)
        holder['client'] = client
        monkeypatch.setattr(platforms.httpx, 'AsyncClient',
                            lambda *a, **kw: client)
        return client

    install.holder = holder
    return install


async def let_tasks_run(times: int = 3) -> None:
    for _ in range(times):
        await asyncio.sleep(0)
