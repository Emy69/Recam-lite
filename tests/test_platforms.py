from __future__ import annotations

import time

import pytest

from recam import platforms
from recam.models import Status

from conftest import FakeClient, FakeResponse

MASTER_URL = 'https://cdn.example.com/live/master.m3u8'
MASTER_BODY = '\n'.join([
    '#EXTM3U',
    '#EXT-X-VERSION:6',
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a1",NAME="audio",DEFAULT=YES,URI="audio.m3u8"',
    '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x480,AUDIO="a1"',
    '480p.m3u8',
    '#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO="a1"',
    '/abs/720p.m3u8',
    '#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,AUDIO="a1"',
    '//cdn2.example.com/1080p.m3u8',
])


def live_routes(body=MASTER_BODY, room_status='public'):
    return {
        'chatvideocontext': FakeResponse(200, json_data={
            'room_status': room_status, 'hls_source': MASTER_URL,
            'start_timestamp': 1700000000, 'num_viewers': 37}),
        'master.m3u8': FakeResponse(200, body),
    }


@pytest.mark.parametrize('text, expected', [
    ('https://chaturbate.com/emy', ('chaturbate', 'emy')),
    ('https://www.chaturbate.com/emy/', ('chaturbate', 'emy')),
    ('http://es.chaturbate.com/emy_69', ('chaturbate', 'emy_69')),
    ('chaturbate.com/emy-69', ('chaturbate', 'emy-69')),
    ('  https://chaturbate.com/emy?tour=abc  ', ('chaturbate', 'emy')),
])
def test_detect_accepts_channel_urls(text, expected):
    assert platforms.detect(text) == expected


@pytest.mark.parametrize('text', [
    'https://chaturbate.com/tag/latina',
    'https://chaturbate.com/directory',
    'https://chaturbate.com/p',
    'emy',
    '',
    'https://example.com/emy',
])
def test_detect_rejects_what_is_not_a_channel(text):
    assert platforms.detect(text) is None


@pytest.mark.parametrize('text', [
    'https://twitch.tv/emy',
    'https://kick.com/emy',
    'https://stripchat.com/emy',
])
def test_detect_ignores_platforms_this_build_disabled(text):
    assert platforms.detect(text) is None


def test_canonical_url():
    assert platforms.canonical_url('chaturbate', 'emy') == 'https://chaturbate.com/emy'


def test_thumbnail_url_only_for_chaturbate():
    assert platforms.thumbnail_url('chaturbate', 'emy').endswith('/emy.jpg')
    assert platforms.thumbnail_url('twitch', 'emy') is None


async def test_probe_reads_a_live_room():
    probe = await platforms.probe(FakeClient(live_routes()), 'chaturbate', 'emy')
    assert probe.status is Status.ONLINE
    assert probe.started_at == 1700000000
    assert probe.viewers == 37


async def test_probe_reports_offline_without_viewers():
    client = FakeClient(live_routes(room_status='offline'))
    probe = await platforms.probe(client, 'chaturbate', 'emy')
    assert probe.status is Status.OFFLINE
    assert probe.viewers == 0


async def test_probe_turns_a_429_into_unknown_and_backs_off():
    client = FakeClient({'chatvideocontext': FakeResponse(429)})
    probe = await platforms.probe(client, 'chaturbate', 'emy')
    assert probe.status is Status.UNKNOWN
    assert platforms.rate_limited('chaturbate')
    assert platforms.rate_limit_remaining('chaturbate') > 0
    platforms.clear_rate_limit('chaturbate')
    assert not platforms.rate_limited('chaturbate')


async def test_probe_survives_a_broken_answer():
    class Exploding(FakeClient):
        async def get(self, *_a, **_kw):
            raise RuntimeError('network down')

    probe = await platforms.probe(Exploding({}), 'chaturbate', 'emy')
    assert probe.status is Status.UNKNOWN


async def test_check_online_is_the_status_of_probe():
    status = await platforms.check_online(FakeClient(live_routes()),
                                          'chaturbate', 'emy')
    assert status is Status.ONLINE


async def test_master_picks_the_best_variant_and_attaches_its_audio(fake_http):
    fake_http(live_routes())
    master = await platforms.resolve_chaturbate_master('emy', 'best')
    lines = master.splitlines()
    assert lines[0] == '#EXTM3U'
    assert lines[-1] == 'https://cdn2.example.com/1080p.m3u8'
    assert any('URI="https://cdn.example.com/live/audio.m3u8"' in ln for ln in lines)


async def test_master_respects_the_quality_cap(fake_http):
    fake_http(live_routes())
    master = await platforms.resolve_chaturbate_master('emy', '720p')
    assert master.splitlines()[-1] == 'https://cdn.example.com/abs/720p.m3u8'


async def test_master_resolves_a_relative_variant_against_the_playlist(fake_http):
    fake_http(live_routes())
    master = await platforms.resolve_chaturbate_master('emy', '480p')
    assert master.splitlines()[-1] == 'https://cdn.example.com/live/480p.m3u8'


async def test_master_takes_the_smallest_when_everything_is_over_the_cap(fake_http):
    body = MASTER_BODY.replace('RESOLUTION=640x480', 'RESOLUTION=1920x1080')
    fake_http(live_routes(body=body))
    master = await platforms.resolve_chaturbate_master('emy', '480p')
    assert master.splitlines()[-1].endswith('.m3u8')


async def test_master_refuses_a_variant_whose_audio_is_missing(fake_http):
    body = '\n'.join(ln for ln in MASTER_BODY.splitlines()
                     if not ln.startswith('#EXT-X-MEDIA'))
    fake_http(live_routes(body=body))
    with pytest.raises(RuntimeError):
        await platforms.resolve_chaturbate_master('emy', 'best')


async def test_master_keeps_a_muxed_variant_without_an_audio_group(fake_http):
    body = MASTER_BODY.replace(',AUDIO="a1"', '')
    fake_http(live_routes(body=body))
    master = await platforms.resolve_chaturbate_master('emy', 'best')
    assert '#EXT-X-MEDIA' not in master


async def test_master_says_when_the_room_is_offline(fake_http):
    fake_http(live_routes(room_status='offline'))
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.resolve_chaturbate_master('emy', 'best')


async def test_master_says_when_the_show_is_private(fake_http):
    fake_http(live_routes(room_status='private'))
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.resolve_chaturbate_master('emy', 'best')


async def test_master_reports_a_429_as_rate_limited(fake_http):
    fake_http({'chatvideocontext': FakeResponse(429)})
    with pytest.raises(platforms.RateLimited):
        await platforms.resolve_chaturbate_master('emy', 'best')
    assert platforms.rate_limited('chaturbate')


async def test_master_complains_when_the_playlist_lists_nothing(fake_http):
    fake_http(live_routes(body='#EXTM3U\n'))
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.resolve_chaturbate_master('emy', 'best')


async def test_master_complains_without_an_hls_source(fake_http):
    fake_http({'chatvideocontext': FakeResponse(
        200, json_data={'room_status': 'public'})})
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.resolve_chaturbate_master('emy', 'best')


async def test_build_record_cmd_writes_the_helper_playlist(tmp_path, monkeypatch):
    from recam import tools
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')

    async def fake_master(_user, _quality):
        return '#EXTM3U\nvariant\n'

    monkeypatch.setattr(platforms, 'resolve_chaturbate_master', fake_master)
    out = tmp_path / 'emy' / 'capture.ts'
    cmd = await platforms.build_record_cmd('chaturbate', 'emy', 'best', out)
    helper = out.with_suffix('.m3u8')
    assert helper.read_text(encoding='utf-8').startswith('#EXTM3U')
    assert cmd[0] == 'ffmpeg.exe'
    assert cmd.count('-i') == 1
    assert str(helper) in cmd
    assert '0:a:0?' in cmd
    assert cmd[-1] == str(out)


async def test_build_record_cmd_falls_back_to_two_inputs(tmp_path, monkeypatch):
    from recam import tools
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')

    async def broken_master(_user, _quality):
        raise RuntimeError('audio rendition not found in master')

    async def fake_urls(_user, _quality):
        return ['https://v.example/v.m3u8', 'https://a.example/a.m3u8']

    monkeypatch.setattr(platforms, 'resolve_chaturbate_master', broken_master)
    monkeypatch.setattr(platforms, 'resolve_chaturbate_urls', fake_urls)
    out = tmp_path / 'capture.ts'
    cmd = await platforms.build_record_cmd('chaturbate', 'emy', 'best', out)
    assert cmd.count('-i') == 2
    assert '0:v:0' in cmd and '1:a:0' in cmd
    assert not out.with_suffix('.m3u8').exists()


async def test_build_record_cmd_does_not_retry_an_offline_room(tmp_path, monkeypatch):
    from recam import tools
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: 'ffmpeg.exe')
    calls = []

    async def offline_master(_user, _quality):
        raise platforms.StreamNotAvailable('not streaming right now')

    async def fake_urls(_user, _quality):
        calls.append(1)
        return []

    monkeypatch.setattr(platforms, 'resolve_chaturbate_master', offline_master)
    monkeypatch.setattr(platforms, 'resolve_chaturbate_urls', fake_urls)
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.build_record_cmd('chaturbate', 'emy', 'best',
                                         tmp_path / 'a.ts')
    assert calls == []


async def test_build_record_cmd_needs_ffmpeg(tmp_path, monkeypatch):
    from recam import tools
    monkeypatch.setattr(tools, 'ffmpeg_path', lambda: None)
    with pytest.raises(platforms.StreamNotAvailable):
        await platforms.build_record_cmd('chaturbate', 'emy', 'best',
                                         tmp_path / 'a.ts')


async def test_throttle_spaces_requests_out():
    throttle = platforms._HostThrottle(min_interval=0.05)
    start = time.monotonic()
    assert await throttle.slot(max_wait=1) is True
    assert await throttle.slot(max_wait=1) is True
    assert time.monotonic() - start >= 0.04


async def test_throttle_gives_up_instead_of_queueing_behind_a_hold():
    throttle = platforms._HostThrottle(min_interval=0.01)
    throttle.report_429()
    assert throttle.holding()
    assert await throttle.slot(max_wait=1) is False


async def test_throttle_penalty_grows_and_is_capped():
    throttle = platforms._HostThrottle(min_interval=0.01)
    seen = []
    for _ in range(6):
        throttle.report_429()
        seen.append(round(throttle.hold_remaining()))
    assert seen[0] == 60
    assert seen[1] == 120
    assert seen[-1] == 900


async def test_throttle_forgets_the_penalty_after_a_good_answer():
    throttle = platforms._HostThrottle(min_interval=0.01)
    throttle.report_429()
    throttle.report_429()
    throttle.report_ok()
    throttle.release()
    throttle.report_429()
    assert round(throttle.hold_remaining()) == 60


async def test_throttle_release_lifts_the_hold():
    throttle = platforms._HostThrottle(min_interval=0.01)
    throttle.report_429()
    throttle.release()
    assert not throttle.holding()
    assert await throttle.slot(max_wait=0.1) is True


def test_rate_limit_helpers_only_know_chaturbate():
    platforms._CB_THROTTLE.report_429()
    assert platforms.rate_limited('chaturbate') is True
    assert platforms.rate_limited('twitch') is False
    assert platforms.rate_limit_remaining('twitch') == 0.0
    platforms.clear_rate_limit('twitch')
    assert platforms.rate_limited('chaturbate') is True
    platforms.clear_rate_limit('chaturbate')
    assert platforms.rate_limited('chaturbate') is False


def test_detect_rejects_lookalike_domains():
    assert platforms.detect('https://not-chaturbate.com/emy') is None


@pytest.mark.parametrize('text', [
    'https://not-chaturbate.com/emy',
    'https://xchaturbate.com/emy',
    'https://mi.chaturbate.com.evil.net/emy',
])
def test_detect_rejects_lookalike_domains(text):
    assert platforms.detect(text) is None


def test_detect_still_finds_a_url_inside_a_sentence():
    assert platforms.detect('mira esta https://chaturbate.com/emy a ver') == (
        'chaturbate', 'emy')
