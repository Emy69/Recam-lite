from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

import httpx

from .i18n import t
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

# The 0.0.1 test build ships Chaturbate-only. The engine still carries the other
# platforms end to end; putting them back is just widening this tuple (and
# restoring the add-channel wording in monitor/ui_panel).
ENABLED_PLATFORMS: tuple[str, ...] = ('chaturbate',)

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
        if platform not in ENABLED_PLATFORMS:
            continue
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
            if not await _CB_THROTTLE.slot(max_wait=30):
                return Status.UNKNOWN   # the hold is long; don't queue behind it
            r = await client.get(f'https://chaturbate.com/api/chatvideocontext/{username}/')
            if r.status_code == 429:
                _CB_THROTTLE.report_429()
                return Status.UNKNOWN
            if r.status_code == 200:
                _CB_THROTTLE.report_ok()
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


class RateLimited(StreamNotAvailable):
    """The site answered 429; we back off instead of digging the hole deeper."""


class _HostThrottle:
    """Politeness for one API host: spaces requests out and, after a 429, holds
    everything back for a growing while.

    Bursting a check for every channel at once is what earns the 429s in the
    first place; and once the site is limiting us, every extra request extends
    the punishment, so during a hold callers give up fast instead of queueing.
    Single event loop assumed: reservations happen between awaits, so no lock.
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._next_slot = 0.0
        self._hold_until = 0.0
        self._penalty = 0.0

    def holding(self) -> bool:
        return time.monotonic() < self._hold_until

    def hold_remaining(self) -> float:
        return max(0.0, self._hold_until - time.monotonic())

    async def slot(self, max_wait: float) -> bool:
        """Reserve the next request slot; False if it is further than max_wait."""
        now = time.monotonic()
        start = max(now, self._next_slot, self._hold_until)
        if start - now > max_wait:
            return False
        self._next_slot = start + self.min_interval
        if start > now:
            await asyncio.sleep(start - now)
        return True

    def report_429(self) -> None:
        self._penalty = min(max(60.0, self._penalty * 2), 900.0)
        self._hold_until = time.monotonic() + self._penalty

    def report_ok(self) -> None:
        self._penalty = 0.0


_CB_THROTTLE = _HostThrottle(min_interval=1.2)


def rate_limited(platform: str) -> bool:
    """Is this platform currently holding off after a 429?"""
    return platform == 'chaturbate' and _CB_THROTTLE.holding()


def rate_limit_remaining(platform: str) -> float:
    return _CB_THROTTLE.hold_remaining() if platform == 'chaturbate' else 0.0


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
            raise StreamNotAvailable(t('stripchat is not responding ({})',
                                       'stripchat no responde ({})').format(r.status_code))
        item = (r.json() or {}).get('item') or {}
        if not item.get('id'):
            raise StreamNotAvailable(t('model not found on stripchat',
                                       'modelo no encontrado en stripchat'))
        if not item.get('isOnline'):
            raise StreamNotAvailable(t('not streaming right now', 'no está emitiendo ahora'))
        master_url = STRIPCHAT_HLS.format(id=item['id'])
        r2 = await client.get(master_url)
        if r2.status_code != 200 or '#EXTM3U' not in r2.text:
            raise StreamNotAvailable(t('stream is not public right now (private show?)',
                                       'emisión no pública ahora mismo (¿show privado?)'))
        if '#EXT-X-MOUFLON' in r2.text:
            # the playlist advertises decoy segments (media.mp4, always a 404) and hides
            # the real encrypted ones behind these tags. Without the key ffmpeg would
            # happily download nothing but 404s, so say so instead of recording garbage.
            raise StreamEncrypted(t('stream encrypted by Stripchat (Mouflon): not recordable without the decryption key',
                                    'emisión cifrada por Stripchat (Mouflon): no grabable sin clave de descifrado'))
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


def _ytdlp_resolve_sync(url: str, fmt: str) -> list[str]:
    """Blocking yt-dlp resolution; runs in a worker thread.

    In-process on purpose: a frozen build has no python to spawn `-m yt_dlp` on.
    """
    import yt_dlp
    opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True,
            'noprogress': True, 'simulate': True, 'format': fmt}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    urls = [f['url'] for f in (info.get('requested_formats') or []) if f.get('url')]
    if not urls and info.get('url'):
        urls = [info['url']]
    return urls


async def resolve_chaturbate_urls(username: str, quality: str) -> list[str]:
    """Fallback resolver: [video, audio] URLs of the public stream, via yt-dlp."""
    if not await _CB_THROTTLE.slot(max_wait=10):
        raise RateLimited(t('chaturbate is rate limiting (429); retrying in ~{}s',
                            'chaturbate está limitando las peticiones (429); se reintenta en ~{}s')
                          .format(int(_CB_THROTTLE.hold_remaining()) + 1))
    fmt = _YTDLP_FORMAT.get(quality, 'bv*+ba/b')
    try:
        urls = await asyncio.wait_for(
            asyncio.to_thread(_ytdlp_resolve_sync,
                              canonical_url('chaturbate', username), fmt),
            timeout=40)
    except asyncio.TimeoutError:
        raise StreamNotAvailable(t('chaturbate did not answer in time',
                                   'chaturbate no respondió a tiempo'))
    except Exception as exc:
        msg = str(exc).lower()
        if 'offline' in msg:
            raise StreamNotAvailable(t('not streaming right now', 'no está emitiendo ahora'))
        if 'private' in msg:
            raise StreamNotAvailable(t('in a private show', 'en show privado'))
        raise StreamNotAvailable(t('chaturbate: could not resolve the stream',
                                   'chaturbate: no se pudo resolver el directo'))
    if not urls:
        raise StreamNotAvailable(t('chaturbate: could not resolve the stream',
                                   'chaturbate: no se pudo resolver el directo'))
    return urls


async def resolve_chaturbate_master(username: str, quality: str) -> str:
    """A minimal master playlist for the public stream: one video variant plus its
    matching audio rendition, with absolute URLs.

    Feeding this to ffmpeg as a SINGLE input is what keeps the recording in sync.
    With the audio playlist as a second -i, ffmpeg shifts each input to start at
    zero independently; it opens the video first, spends a second or three probing
    it, and by then the audio's live edge has moved on — so the audio lands that
    far ahead of the picture, a different amount every capture. One input gets one
    common shift and the shared source timeline survives intact (measured: 1.6 s
    of audio lead with two inputs, frame-exact alignment with one).
    """
    if not await _CB_THROTTLE.slot(max_wait=10):
        raise RateLimited(t('chaturbate is rate limiting (429); retrying in ~{}s',
                            'chaturbate está limitando las peticiones (429); se reintenta en ~{}s')
                          .format(int(_CB_THROTTLE.hold_remaining()) + 1))
    async with httpx.AsyncClient(headers=REQUEST_HEADERS, timeout=20,
                                 follow_redirects=True) as client:
        r = await client.get(f'https://chaturbate.com/api/chatvideocontext/{username}/')
        if r.status_code == 429:
            _CB_THROTTLE.report_429()
            raise RateLimited(t('chaturbate answered 429 (too many requests); backing off and retrying',
                                'chaturbate devolvió 429 (demasiadas peticiones); pausa automática y reintento'))
        if r.status_code != 200:
            raise StreamNotAvailable(t('chaturbate is not responding ({})',
                                       'chaturbate no responde ({})').format(r.status_code))
        _CB_THROTTLE.report_ok()
        data = r.json() or {}
        status = data.get('room_status')
        if status in ('offline', 'away'):
            raise StreamNotAvailable(t('not streaming right now', 'no está emitiendo ahora'))
        if status and status != 'public':
            raise StreamNotAvailable(t('no public stream (status: {})',
                                       'sin emisión pública (estado: {})').format(status))
        master_url = data.get('hls_source')
        if not master_url:
            raise StreamNotAvailable(t('chaturbate did not hand out the stream URL',
                                       'chaturbate no dio la URL del directo'))
        r2 = await client.get(master_url)
        if r2.status_code != 200 or '#EXTM3U' not in r2.text:
            raise StreamNotAvailable(t('the stream playlist is not available',
                                       'el playlist del directo no está disponible'))

    origin = re.match(r'(https?://[^/]+)', master_url).group(1)
    base_dir = master_url.rsplit('/', 1)[0]

    def absolutize(uri: str) -> str:
        if '://' in uri:
            return uri
        if uri.startswith('//'):
            return 'https:' + uri
        if uri.startswith('/'):
            return origin + uri
        return base_dir + '/' + uri

    audio_lines: dict[str, str] = {}
    variants: list[tuple[int, int, str, str]] = []   # height, bandwidth, inf line, url
    info = None
    for line in r2.text.splitlines():
        line = line.strip()
        if line.startswith('#EXT-X-MEDIA:') and 'TYPE=AUDIO' in line:
            gid = re.search(r'GROUP-ID="([^"]+)"', line)
            uri = re.search(r'URI="([^"]+)"', line)
            if gid and uri:
                audio_lines[gid.group(1)] = line.replace(uri.group(1),
                                                         absolutize(uri.group(1)))
        elif line.startswith('#EXT-X-STREAM-INF:'):
            info = line
        elif info and line and not line.startswith('#'):
            height = re.search(r'RESOLUTION=\d+x(\d+)', info)
            bandwidth = re.search(r'BANDWIDTH=(\d+)', info)
            variants.append((int(height.group(1)) if height else 0,
                             int(bandwidth.group(1)) if bandwidth else 0,
                             info, absolutize(line)))
            info = None
    if not variants:
        raise StreamNotAvailable(t('the stream playlist lists no qualities',
                                   'el playlist del directo no lista calidades'))

    cap = _QUALITY_CAP.get(quality, 100_000)
    eligible = [v for v in variants if v[0] <= cap] or variants
    eligible.sort(key=lambda v: (v[0], v[1]))
    _, _, inf, variant_url = eligible[-1]

    lines = ['#EXTM3U', '#EXT-X-VERSION:6', '#EXT-X-INDEPENDENT-SEGMENTS']
    group = re.search(r'AUDIO="([^"]+)"', inf)
    if group and group.group(1) in audio_lines:
        lines.append(audio_lines[group.group(1)])
    lines += [inf, variant_url]
    return '\n'.join(lines) + '\n'


async def build_record_cmd(platform: str, username: str, quality: str,
                           out_ts: Path) -> list[str]:
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
        raise StreamNotAvailable(t('ffmpeg not found', 'ffmpeg no encontrado'))

    if platform == 'stripchat':
        m3u8 = await resolve_stripchat_m3u8(username, quality)
        return [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
                '-user_agent', REQUEST_HEADERS['User-Agent'],
                '-i', m3u8, '-c', 'copy', '-f', 'mpegts', str(out_ts)]

    # Chaturbate. Preferred path: a local master playlist handed to ffmpeg as ONE
    # input (see resolve_chaturbate_master for why this is what keeps A/V in sync).
    # No -user_agent here: the input is a local file and the option belongs to the
    # http protocol, so ffmpeg rejects it — the CDN serves fine without it.
    try:
        master = await resolve_chaturbate_master(username, quality)
    except StreamNotAvailable:
        raise
    except Exception:
        # the page changed on us; the yt-dlp route still records, just with the
        # audio-lead problem, which beats not recording at all
        urls = await resolve_chaturbate_urls(username, quality)
        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
               '-user_agent', REQUEST_HEADERS['User-Agent']]
        for u in urls:
            cmd += ['-i', u]
        if len(urls) >= 2:
            cmd += ['-map', '0:v:0', '-map', '1:a:0']
        return cmd + ['-c', 'copy', '-f', 'mpegts', str(out_ts)]

    master_path = out_ts.with_suffix('.m3u8')
    master_path.parent.mkdir(parents=True, exist_ok=True)
    master_path.write_text(master, encoding='utf-8')
    return [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-i', str(master_path),
            '-map', '0:v:0', '-map', '0:a:0',
            '-c', 'copy', '-f', 'mpegts', str(out_ts)]
