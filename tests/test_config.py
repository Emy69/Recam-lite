from __future__ import annotations

import json

import pytest

from recam import config as config_mod
from recam.models import Streamer

from conftest import make_streamer


def test_sanitize_segment_replaces_illegal_characters():
    assert config_mod.sanitize_segment(r'a<b>c:d"e/f\g|h?i*j') == 'a_b_c_d_e_f_g_h_i_j'


def test_sanitize_segment_trims_trailing_dots_and_spaces():
    assert config_mod.sanitize_segment('  emy . ') == 'emy'
    assert config_mod.sanitize_segment('...') == '_'
    assert config_mod.sanitize_segment('') == '_'


def test_build_output_stem_uses_the_default_template(cfg):
    stem = config_mod.build_output_stem(cfg, make_streamer('emy'))
    parts = stem.parts
    assert parts[0] == 'emy'
    assert parts[1].endswith('[chaturbate]')
    assert len(parts) == 2


def test_build_output_stem_honours_a_custom_template(cfg):
    cfg.filename_template = '{platform}/{streamer}/clip'
    stem = config_mod.build_output_stem(cfg, make_streamer('emy'))
    assert stem.parts == ('chaturbate', 'emy', 'clip')


def test_build_output_stem_falls_back_on_an_unknown_placeholder(cfg):
    cfg.filename_template = '{nope}/{streamer}'
    stem = config_mod.build_output_stem(cfg, make_streamer('emy'))
    assert stem.parts[0] == 'emy'
    assert len(stem.parts) == 2


def test_build_output_stem_cannot_escape_the_recordings_folder(cfg):
    cfg.filename_template = '../../{streamer}'
    stem = config_mod.build_output_stem(cfg, make_streamer('emy'))
    assert '..' not in stem.parts


def test_build_output_stem_sanitizes_the_username(cfg):
    cfg.filename_template = '{streamer}'
    stem = config_mod.build_output_stem(cfg, make_streamer('a:b|c'))
    assert stem.parts == ('a_b_c',)


def test_build_output_stem_survives_an_empty_template(cfg):
    cfg.filename_template = ''
    stem = config_mod.build_output_stem(cfg, make_streamer('emy'))
    assert stem.parts[0] == 'emy'


def test_load_returns_defaults_without_a_config_file():
    cfg = config_mod.load()
    assert cfg.poll_seconds == 60
    assert cfg.quality == 'best'
    assert cfg.language == 'en'
    assert cfg.recordings_path.is_dir()


def test_load_reads_known_keys_and_ignores_the_rest(isolated):
    config_mod.CONFIG_FILE.write_text(json.dumps({
        'poll_seconds': 120, 'quality': '720p', 'unknown_key': 'x',
    }), encoding='utf-8')
    cfg = config_mod.load()
    assert cfg.poll_seconds == 120
    assert cfg.quality == '720p'
    assert not hasattr(cfg, 'unknown_key')


def test_load_survives_a_corrupt_config_file(isolated):
    config_mod.CONFIG_FILE.write_text('{not json', encoding='utf-8')
    assert config_mod.load().poll_seconds == 60


def test_load_falls_back_when_the_recordings_folder_cannot_be_created(isolated):
    blocker = isolated.root / 'blocker'
    blocker.write_text('i am a file', encoding='utf-8')
    config_mod.CONFIG_FILE.write_text(
        json.dumps({'recordings_dir': str(blocker / 'inside')}), encoding='utf-8')
    cfg = config_mod.load()
    assert cfg.recordings_dir == config_mod.Config().recordings_dir


def test_save_then_load_roundtrips(cfg):
    cfg.poll_seconds = 45
    cfg.language = 'es'
    cfg.audio_offset_ms = -250
    config_mod.save(cfg)
    again = config_mod.load()
    assert (again.poll_seconds, again.language, again.audio_offset_ms) == (45, 'es', -250)


def test_streamers_roundtrip_keeps_the_persisted_timeline():
    one = make_streamer('emy', auto_record=False)
    one.last_online = 1700000000.7
    one.last_broadcast_start = 1699999999.2
    one.viewers = 42
    config_mod.save_streamers([one])
    back = config_mod.load_streamers()
    assert len(back) == 1
    assert back[0].username == 'emy'
    assert back[0].auto_record is False
    assert back[0].last_online == 1700000001
    assert back[0].last_broadcast_start == 1699999999
    assert back[0].viewers == 0


def test_load_streamers_survives_a_corrupt_file(isolated):
    config_mod.STREAMERS_FILE.write_text('[[[', encoding='utf-8')
    assert config_mod.load_streamers() == []


def test_load_streamers_without_a_file():
    assert config_mod.load_streamers() == []


def test_streamer_json_omits_a_never_seen_timeline():
    assert config_mod.Config() is not None
    data = make_streamer('emy').to_json()
    assert 'last_online' not in data
    assert 'last_broadcast_start' not in data


def test_streamer_from_json_tolerates_nulls():
    s = Streamer.from_json({'url': 'u', 'platform': 'chaturbate', 'username': 'emy',
                            'last_online': None})
    assert s.last_online == 0.0


def test_load_coerces_numeric_settings(isolated):
    config_mod.CONFIG_FILE.write_text(json.dumps({
        'poll_seconds': '90', 'max_concurrent': None, 'audio_offset_ms': '0',
    }), encoding='utf-8')
    cfg = config_mod.load()
    assert cfg.poll_seconds == 90
    assert isinstance(cfg.max_concurrent, int)
    assert isinstance(cfg.audio_offset_ms, int)


def test_sanitize_segment_guards_windows_device_names():
    for name in ('con', 'PRN', 'aux', 'nul', 'COM1', 'lpt1'):
        assert config_mod.sanitize_segment(name).lower() != name.lower()


def test_sanitize_segment_leaves_ordinary_names_alone():
    for name in ('conejita', 'auxiliar', 'nulo', 'com', 'lpt', 'prnt'):
        assert config_mod.sanitize_segment(name) == name


def test_load_clamps_settings_that_are_out_of_range(isolated):
    config_mod.CONFIG_FILE.write_text(json.dumps({
        'poll_seconds': 2, 'max_concurrent': 0, 'audio_offset_ms': 99999,
        'port': 0,
    }), encoding='utf-8')
    cfg = config_mod.load()
    assert cfg.poll_seconds == 15
    assert cfg.max_concurrent == 1
    assert cfg.audio_offset_ms == 2000
    assert cfg.port == 1


def test_load_keeps_the_default_for_a_setting_of_the_wrong_type(isolated):
    config_mod.CONFIG_FILE.write_text(json.dumps({
        'recordings_dir': 5, 'quality': None, 'lan_access': 'yes',
        'filename_template': ['a'],
    }), encoding='utf-8')
    cfg = config_mod.load()
    default = config_mod.Config()
    assert cfg.recordings_dir == default.recordings_dir
    assert cfg.quality == 'best'
    assert cfg.lan_access is False
    assert cfg.filename_template == default.filename_template
