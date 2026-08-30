from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from pathlib import Path

import httpx

from .models import Status

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

PLATFORM_COLORS = {
    'twitch': '#9146FF',
    'kick': '#53FC18',
    'chaturbate': '#F47321',
    'stripchat': '#E6224B',
}

_PATTERNS = [
    ('twitch', re.compile(r'(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,30})', re.I)),
    ('kick', re.compile(r'(?:https?://)?(?:www\.)?kick\.com/([A-Za-z0-9_\-]{2,30})', re.I)),
    ('stripchat', re.compile(r'(?:https?://)?(?:[a-z]{2,3}\.)?stripchat\.com/([A-Za-z0-9_\-]+)', re.I)),
    ('chaturbate', re.compile(r'(?:https?://)?(?:[a-z]{2,3}\.)?chaturbate\.com/([A-Za-z0-9_\-]+)', re.I)),
]

# first path segment of a non-profile URL, so pasting a category page is not
# mistaken for a channel
_RESERVED = {'videos', 'directory', 'category', 'categories', 'search', 'settings',
             'login', 'signup', 'p', 'tags', 'tag', 'girls', 'guys', 'couples', 'trans'}


def detect(text: str) -> tuple[str, str] | None:
    text = text.strip()
    for platform, pattern in _PATTERNS:
        m = pattern.search(text)
        if m and m.group(1).lower() not in _RESERVED:
            return platform, m.group(1)
    return None


def canonical_url(platform: str, username: str) -> str:
    return {
        'twitch': f'https://www.twitch.tv/{username}',
        'kick': f'https://kick.com/{username}',
        'stripchat': f'https://stripchat.com/{username}',
        'chaturbate': f'https://chaturbate.com/{username}',
    }[platform]


async def check_online(client: httpx.AsyncClient, platform: str, username: str) -> Status:
    """Cheap liveness poll against the public web/API. UNKNOWN when the site won't say.

    UNKNOWN never blocks anything: channels with auto-record on are attempted anyway
    and streamlink/yt-dlp gets the final word.
    """
    try:
        if platform == 'twitch':
            r = await client.get(f'https://www.twitch.tv/{username}')
            if r.status_code == 200:
                return Status.ONLINE if 'isLiveBroadcast' in r.text else Status.OFFLINE
        elif platform == 'kick':
            r = await client.get(f'https://kick.com/api/v2/channels/{username}')
            if r.status_code == 200:
                return Status.ONLINE if r.json().get('livestream') else Status.OFFLINE
        elif platform == 'chaturbate':
            r = await client.get(f'https://chaturbate.com/api/chatvideocontext/{username}/')
            if r.status_code == 200:
                return Status.ONLINE if r.json().get('room_status') == 'public' else Status.OFFLINE
        elif platform == 'stripchat':
            r = await client.get(f'https://stripchat.com/api/front/v2/users/username/{username}')
            if r.status_code == 200:
                item = (r.json() or {}).get('item') or {}
                return Status.ONLINE if item.get('isOnline') else Status.OFFLINE
    except Exception:
        pass
    return Status.UNKNOWN


class StreamNotAvailable(Exception):
    """Nothing public to record right now."""


class StreamEncrypted(StreamNotAvailable):
    """Live and public, but encrypted (Stripchat's Mouflon).

    Needs a decryption key the site rotates constantly and does not hand out, so
    retrying is pointless.
    """


_STREAMLINK_QUALITY = {
    'best': 'best',
    '1080p': '1080p60,1080p,best',
    '720p': '720p60,720p,best',
    '480p': '480p,best',
}
# cam sites publish audio and video separately, hence bv*+ba rather than plain best
_YTDLP_FORMAT = {
    'best': 'bv*+ba/b',
    '1080p': 'bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b',
    '720p': 'bv*[height<=720]+ba/b[height<=720]/bv*+ba/b',
    '480p': 'bv*[height<=480]+ba/b[height<=480]/bv*+ba/b',
}
_QUALITY_CAP = {'best': 100_000, '1080p': 1080, '720p': 720, '480p': 480}

STRIPCHAT_HLS = 'https://edge-hls.doppiocdn.com/hls/{id}/master/{id}_auto.m3u8'


async def resolve_stripchat_m3u8(username: str, quality: str) -> str:
    """m3u8 of the public stream, picking the variant that fits the quality setting.

    Done by hand instead of through yt-dlp, whose extractor reports a bogus
    "private show" when the page still carries a finished one in viewCam.show.
    """
    async with httpx.AsyncClient(headers=REQUEST_HEADERS, timeout=20,
                                 follow_redirects=True) as client:
        r = await client.get(f'https://stripchat.com/api/front/v2/users/username/{username}')
        if r.status_code != 200:
            raise StreamNotAvailable(f'stripchat no responde ({r.status_code})')
        item = (r.json() or {}).get('item') or {}
        if not item.get('id'):
            raise StreamNotAvailable('modelo no encontrado en stripchat')
        if not item.get('isOnline'):
            raise StreamNotAvailable('no está emitiendo ahora')
        master_url = STRIPCHAT_HLS.format(id=item['id'])
        r2 = await client.get(master_url)
        if r2.status_code != 200 or '#EXTM3U' not in r2.text:
            raise StreamNotAvailable('emisión no pública ahora mismo (¿show privado?)')
        if '#EXT-X-MOUFLON' in r2.text:
            # the playlist advertises decoy segments (media.mp4, always a 404) and hides
            # the real encrypted ones behind these tags. Without the key ffmpeg would
            # happily download nothing but 404s, so say so instead of recording garbage.
            raise StreamEncrypted('emisión cifrada por Stripchat (Mouflon): no grabable '
                                  'sin clave de descifrado')
        variants: list[tuple[int, int, str]] = []
        info = None
        for line in r2.text.splitlines():
            line = line.strip()
            if line.startswith('#EXT-X-STREAM-INF:'):
                info = line
            elif info and line and not line.startswith('#'):
                height = re.search(r'RESOLUTION=\d+x(\d+)', info)
                bandwidth = re.search(r'BANDWIDTH=(\d+)', info)
                url = line if '://' in line else master_url.rsplit('/', 1)[0] + '/' + line
                variants.append((int(height.group(1)) if height else 0,
                                 int(bandwidth.group(1)) if bandwidth else 0, url))
                info = None
        if not variants:
            return master_url   # nothing listed; let ffmpeg sort the master out
        cap = _QUALITY_CAP.get(quality, 100_000)
        eligible = [v for v in variants if v[0] <= cap] or variants
        eligible.sort(key=lambda v: (v[0], v[1]))
        return eligible[-1][2]


async def resolve_chaturbate_urls(username: str, quality: str) -> list[str]:
    """[video, audio] (or a single combined URL) for the public stream, via yt-dlp -g.

    Only the URLs come from yt-dlp; a single ffmpeg does the downloading and muxing.
    """
    from . import tools
    fmt = _YTDLP_FORMAT.get(quality, 'bv*+ba/b')
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'yt_dlp', '-f', fmt, '-g', '--no-playlist',
        canonical_url('chaturbate', username),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=tools.CREATE_NO_WINDOW)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        raise StreamNotAvailable('chaturbate no respondió a tiempo')
    urls = [u for u in out.decode('utf-8', 'replace').splitlines() if u.startswith('http')]
    if not urls:
        msg = err.decode('utf-8', 'replace').lower()
        if 'offline' in msg:
            raise StreamNotAvailable('no está emitiendo ahora')
        if 'private' in msg:
            raise StreamNotAvailable('en show privado')
        raise StreamNotAvailable('chaturbate: no se pudo resolver el directo')
    return urls


async def build_record_cmd(platform: str, username: str, quality: str, out_ts: Path,
                           audio_offset_ms: int = 0) -> list[str]:
    from . import tools

    url = canonical_url(platform, username)
    py = sys.executable
    if platform in ('twitch', 'kick'):
        cmd = [py, '-m', 'streamlink', '--loglevel', 'info', '--retry-open', '2']
        if platform == 'twitch':
            cmd.append('--twitch-disable-ads')
        cmd += [url, _STREAMLINK_QUALITY.get(quality, 'best'), '-o', str(out_ts)]
        return cmd

    ffmpeg = tools.ffmpeg_path()
    if not ffmpeg:
        raise StreamNotAvailable('ffmpeg no encontrado')

    if platform == 'stripchat':
        m3u8 = await resolve_stripchat_m3u8(username, quality)
        return [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
                '-user_agent', REQUEST_HEADERS['User-Agent'],
                '-i', m3u8, '-c', 'copy', '-f', 'mpegts', str(out_ts)]

    # Chaturbate splits low-latency HLS into separate audio and video playlists. One
    # ffmpeg pulls both and muxes them copying each as-is, which keeps the timestamps
    # the stream already carries — re-encoding the audio here rewrote them and threw
    # the sync off. Output is progressive mpegts so it survives a hard stop.
    urls = await resolve_chaturbate_urls(username, quality)
    offset = audio_offset_ms / 1000
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
           '-user_agent', REQUEST_HEADERS['User-Agent']]
    for i, u in enumerate(urls):
        # -itsoffset applies to the input that follows, so only to the audio one
        if i == 1 and offset:
            cmd += ['-itsoffset', f'{offset:.3f}']
        cmd += ['-i', u]
    if len(urls) >= 2:
        cmd += ['-map', '0:v:0', '-map', '1:a:0']
    cmd += ['-c', 'copy']
    if offset and len(urls) >= 2:
        cmd += ['-avoid_negative_ts', 'make_zero']
    cmd += ['-f', 'mpegts', str(out_ts)]
    return cmd
