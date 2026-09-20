"""The tabs a tester actually sees, built and driven without a browser.

Everything else in this suite stops at the engine. These build the real Panel,
Library and Settings against a simulated client, so a page that crashes on
build, a button wired to nothing or a Spanish label that never arrives shows
up here instead of on someone's desktop.
"""
from __future__ import annotations

import json

import pytest
from nicegui import ui

from recam import config as config_mod
from recam import i18n, tools, ui_dialogs, ui_library, ui_panel, ui_settings, ui_tutorial
from recam.library import Library
from recam.models import Status
from recam.monitor import Monitor

from conftest import make_streamer


def _monitor(cfg, streamers=None) -> Monitor:
    """A Monitor that never polls: nothing starts until start() is awaited."""
    return Monitor(cfg, list(streamers or []), Library(cfg))


def _have_ffmpeg(monkeypatch) -> None:
    """Keep the missing-tools banner out of the way of the tests about other things."""
    monkeypatch.setattr(tools, 'missing_tools', lambda: [])


@pytest.fixture
def offline_tools(monkeypatch):
    """Settings prints the version of every tool it finds, by running it.

    These tests must not shell out, so hand it the answers instead.
    """
    monkeypatch.setattr(tools, 'missing_tools', lambda: [])
    monkeypatch.setattr(tools, 'tool_version', lambda *_a, **_kw: 'ffmpeg version 7.1')


# ------------------------------------------------------------------- the panel

async def test_the_panel_builds_with_no_channels(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    await user.should_see('No channels yet')
    await user.should_see('Add')


async def test_the_panel_shows_a_channel_and_its_platform_tag(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg, [make_streamer('emy')])

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    await user.should_see('emy')
    await user.should_see('CB')
    await user.should_not_see('No channels yet')


async def test_a_live_channel_is_listed_apart_from_the_idle_ones(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    live = make_streamer('live_one')
    live.status = Status.ONLINE
    monitor = _monitor(cfg, [live, make_streamer('idle_one')])

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    await user.should_see('Live now')
    await user.should_see('Not broadcasting')
    await user.should_see('live_one')
    await user.should_see('idle_one')


async def test_adding_a_channel_from_the_url_box_keeps_it(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    user.find(ui.input).type('https://chaturbate.com/emy')
    user.find('Add').click()
    await user.should_see('emy added')

    assert [s.username for s in monitor.streamers] == ['emy']
    # and it survives a restart: the add wrote the list to disk
    assert [s.username for s in config_mod.load_streamers()] == ['emy']


async def test_a_new_channel_does_not_record_on_its_own(user, cfg, monkeypatch):
    """Auto-record off by default; a tester must ask for it per channel."""
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    user.find(ui.input).type('https://chaturbate.com/emy')
    user.find('Add').click()
    await user.should_see('emy added')

    assert monitor.streamers[0].auto_record is False


async def test_the_url_box_turns_down_a_site_this_build_does_not_record(
        user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    user.find(ui.input).type('https://stripchat.com/emy')
    user.find('Add').click()
    await user.should_see('Chaturbate only')
    assert monitor.streamers == []


async def test_the_same_channel_cannot_be_added_twice(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg, [make_streamer('emy')])

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    user.find(ui.input).type('https://chaturbate.com/emy')
    user.find('Add').click()
    await user.should_see('already on the list')
    assert len(monitor.streamers) == 1


async def test_the_panel_warns_when_ffmpeg_is_missing(user, cfg, monkeypatch):
    monkeypatch.setattr(tools, 'missing_tools', lambda: ['ffmpeg', 'ffprobe'])
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    await user.should_see('ffmpeg is missing')


async def test_the_activity_drawer_lists_what_happened(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    monitor = _monitor(cfg)
    monitor._event('saved', 'Saved 12 min of emy', 'emy')

    @ui.page('/')
    def page():
        feed = ui_panel.build_activity(monitor)
        feed.tick()

    await user.open('/')
    await user.should_see('Saved 12 min of emy')


# ----------------------------------------------------------------- the library

async def test_the_library_builds_with_nothing_recorded(user, cfg):
    library = Library(cfg)

    @ui.page('/')
    def page():
        ui_library.build(library, _monitor(cfg))

    await user.open('/')
    await user.should_see('All')


async def test_the_library_lists_a_recording_on_disk(user, cfg, recordings):
    folder = recordings / 'emy'
    folder.mkdir()
    (folder / 'emy_2026-01-01_10-00.mp4').write_bytes(b'not really a video')
    library = Library(cfg)
    await library.scan()

    @ui.page('/')
    def page():
        ui_library.build(library, _monitor(cfg))

    await user.open('/')
    await user.should_see('emy')


# ---------------------------------------------------------------- the settings

async def test_the_settings_tab_builds(user, cfg, offline_tools):
    @ui.page('/')
    def page():
        ui_settings.build(cfg, _monitor(cfg))

    await user.open('/')
    await user.should_see('Recording')
    await user.should_see('Recordings folder')
    await user.should_see('Quality')


def _quality_select(user):
    """The quality dropdown, told apart from the other selects by its options."""
    return next(element for element in user.find(ui.select).elements
                if '720p' in (element.options or {}))


async def test_the_save_bar_stays_out_of_the_way_until_something_changes(
        user, cfg, offline_tools):
    @ui.page('/')
    def page():
        ui_settings.build(cfg, _monitor(cfg))

    await user.open('/')
    await user.should_not_see('Save settings')   # the bar is hidden while nothing is dirty

    _quality_select(user).set_value('720p')
    await user.should_see('Save settings')
    await user.should_see('1 unsaved change(s)')


async def test_a_changed_setting_reaches_the_file_and_the_engine(
        user, cfg, offline_tools):
    @ui.page('/')
    def page():
        ui_settings.build(cfg, _monitor(cfg))

    await user.open('/')
    _quality_select(user).set_value('720p')
    user.find('Save settings').click()
    await user.should_see('Settings saved')

    assert cfg.quality == '720p'                 # the running engine sees it at once
    saved = json.loads(config_mod.CONFIG_FILE.read_text(encoding='utf-8'))
    assert saved['quality'] == '720p'            # and it survives a restart
    assert config_mod.load().quality == '720p'


async def test_discarding_puts_the_settings_back(user, cfg, offline_tools):
    @ui.page('/')
    def page():
        ui_settings.build(cfg, _monitor(cfg))

    await user.open('/')
    _quality_select(user).set_value('720p')
    user.find('Discard').click()

    assert _quality_select(user).value == 'best'
    assert cfg.quality == 'best'
    assert not config_mod.CONFIG_FILE.exists()


# ------------------------------------------------------------------- the rest

async def test_the_tutorial_opens_on_its_first_step(user, cfg):
    @ui.page('/')
    def page():
        ui_tutorial.show()

    await user.open('/')
    await user.should_see('Chaturbate')


async def test_the_welcome_can_be_turned_off_for_good(user, cfg):
    @ui.page('/')
    def page():
        ui_dialogs.beta_notice(cfg, lambda: None).open()

    await user.open('/')
    await user.should_see('Welcome to the Recam test build')
    user.find(ui.checkbox).click()
    user.find('OK').click()

    assert cfg.show_beta_notice is False
    saved = json.loads(config_mod.CONFIG_FILE.read_text(encoding='utf-8'))
    assert saved['show_beta_notice'] is False


async def test_closing_the_window_asks_before_it_quits(user, cfg):
    answered = []

    @ui.page('/')
    def page():
        entry = ui_dialogs.close_question(True, lambda: answered.append('hide'),
                                          lambda: answered.append('quit'))
        entry['dialog'].open()

    await user.open('/')
    await user.should_see('Recam')


# --------------------------------------------------------------- both languages

async def test_the_panel_speaks_spanish_when_asked(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    i18n.set_language('es')
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    await user.should_see('Aún no hay canales')
    await user.should_see('Añadir')


async def test_a_refusal_is_translated_too(user, cfg, monkeypatch):
    _have_ffmpeg(monkeypatch)
    i18n.set_language('es')
    monitor = _monitor(cfg)

    @ui.page('/')
    def page():
        ui_panel.build(monitor)

    await user.open('/')
    user.find(ui.input).type('https://stripchat.com/emy')
    user.find('Añadir').click()
    await user.should_see('solo graba Chaturbate')
