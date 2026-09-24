from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
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

# Chaturbate only in this build. The rest of the engine still handles the other
# platforms; re-enabling one means widening this tuple and fixing the
# add-channel wording in monitor/ui_panel.
ENABLED_PLATFORMS: tuple[str, ...] = ('chaturbate',)

_PATTERNS = [
    ('twitch', re.compile(r'(?<![\w.-])(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,30})', re.I)),
    ('kick', re.compile(r'(?<![\w.-])(?:https?://)?(?:www\.)?kick\.com/([A-Za-z0-9_\-]{2,30})', re.I)),
    ('stripchat', re.compile(r'(?<![\w.-])(?:https?://)?(?:[a-z]{2,3}\.)?stripchat\.com/([A-Za-z0-9_\-]+)', re.I)),
    ('chaturbate', re.compile(r'(?<![\w.-])(?:https?://)?(?:[a-z]{2,3}\.)?chaturbate\.com/([A-Za-z0-9_\-]+)', re.I)),
]

# first path segment of non-profile URLs, so a category page is not read
# as a channel
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


@dataclass
class Probe:
    """Result of one liveness poll."""
    status: Status
    started_at: float = 0.0   # broadcast start the site reported (unix seconds), if any
    viewers: int = 0
    # False when the throttle refused the slot and no request went out. UNKNOWN
    # then means "not asked" rather than "no answer"; retrying anyway spends,
    # through the priority lane, the request the throttle just refused.
    asked: bool = True


async def probe(client: httpx.AsyncClient, platform: str, username: str) -> Probe:
    """Cheap liveness poll against the public web/API. UNKNOWN if the site won't say.

    UNKNOWN does not block: auto-record channels are attempted anyway and
    yt-dlp/ffmpeg has the final word.
    """
    try:
        if platform == 'twitch':
            r = await client.get(f'https://www.twitch.tv/{username}')
            if r.status_code == 200:
                return Probe(Status.ONLINE if 'isLiveBroadcast' in r.text else Status.OFFLINE)
        elif platform == 'kick':
            r = await client.get(f'https://kick.com/api/v2/channels/{username}')
            if r.status_code == 200:
                return Probe(Status.ONLINE if r.json().get('livestream') else Status.OFFLINE)
        elif platform == 'chaturbate':
            if not await _CB_THROTTLE.slot(max_wait=30):
                # queue longer than the window: report it instead of faking a status
                return Probe(Status.UNKNOWN, asked=False)
            r = await client.get(f'https://chaturbate.com/api/chatvideocontext/{username}/')
            if r.status_code == 429:
                _CB_THROTTLE.report_429(_retry_after(r))
                return Probe(Status.UNKNOWN)
            if r.status_code == 200:
                _CB_THROTTLE.report_ok()
                data = r.json() or {}
                live = data.get('room_status') == 'public'
                # for an offline room this is when its latest broadcast began
                started = float(data.get('start_timestamp') or 0)
                viewers = int(data.get('num_viewers') or 0) if live else 0
                return Probe(Status.ONLINE if live else Status.OFFLINE, started, viewers)
        elif platform == 'stripchat':
            r = await client.get(f'https://stripchat.com/api/front/v2/users/username/{username}')
            if r.status_code == 200:
                item = (r.json() or {}).get('item') or {}
                return Probe(Status.ONLINE if item.get('isOnline') else Status.OFFLINE)
    except Exception:
        pass
    return Probe(Status.UNKNOWN)


async def check_online(client: httpx.AsyncClient, platform: str, username: str) -> Status:
    """Status-only view of probe(), for callers that need nothing more."""
    return (await probe(client, platform, username)).status


def thumbnail_url(platform: str, username: str) -> str | None:
    """Public still of a live room, which the site overwrites every few seconds.

    Chaturbate only; the other sites have no unauthenticated equivalent. This host
    is the one whose certificate the embedded browser accepts (roomimg.* does not).
    """
    if platform == 'chaturbate':
        return f'https://thumb.live.mmcdn.com/riw/{username}.jpg'
    return None


class StreamNotAvailable(Exception):
    """Nothing public to record right now."""


class StreamEncrypted(StreamNotAvailable):
    """Live and public, but encrypted (Stripchat's Mouflon).

    Needs a key the site rotates and does not publish, so retrying is pointless.
    """


class RateLimited(StreamNotAvailable):
    """The site answered 429. Back off instead of retrying."""


def _learned_file() -> Path:
    # per call, not at import: the tests repoint DATA_DIR at a temp folder
    from . import config as config_mod
    return config_mod.DATA_DIR / 'throttle.json'


def _load_learned() -> dict:
    try:
        return json.loads(_learned_file().read_text(encoding='utf-8'))
    except Exception:
        return {}      # missing or unreadable: nothing learned


def _save_learned(name: str, interval: float) -> None:
    """Persist the spacing a host turned out to need.

    Wall clock, not monotonic: this has to survive a restart and monotonic
    resets with the process.
    """
    try:
        learned = _load_learned()
        learned[name] = {'interval': round(interval, 2), 'at': int(time.time())}
        path = _learned_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(learned, indent=2), encoding='utf-8')
    except Exception:
        pass           # bookkeeping must not break a poll


class _HostThrottle:
    """Rate limiter for one API host: spaces requests out and holds after a 429.

    Checking every channel at once is what earns the 429s, and while the site is
    limiting, extra requests only extend the hold, so callers give up fast
    instead of queueing. Assumes a single event loop: reservations happen
    between awaits, so there is no lock.

    A pass over N channels books N slots in a row, so an ordinary reservation can
    sit N * min_interval away. Starting a capture must not queue behind that, or
    a live channel fails as rate-limited without the site saying so, so urgent
    reservations take the next free moment and push the queue back.
    """

    # spacing widens x1.5 per 429, up to 5 s, and returns to the base only after
    # an hour without one. The real limit is undocumented, so it is found by trial.
    RELAX_AFTER = 3600.0
    MAX_INTERVAL = 5.0

    def __init__(self, min_interval: float, name: str = 'the site') -> None:
        self.name = name
        self.base_interval = min_interval
        self.min_interval = min_interval
        self._next_slot = 0.0
        self._last_sent = 0.0
        self._hold_until = 0.0
        self._penalty = 0.0
        self._last_429 = 0.0
        self._restored = False

    def _restore_once(self) -> None:
        """Reload the learned spacing before the first request goes out.

        The site's limit does not reset because the app restarted, so starting
        at the base spacing means collecting another 429 on every launch.

        Read on first use, not in __init__: these throttles are built at import
        time, before DATA_DIR is known.
        """
        self._restored = True
        saved = _load_learned().get(self.name) or {}
        try:
            interval = float(saved.get('interval', 0))
            # Clamped rather than rejected when negative: the clock can go
            # backwards between runs (manual change, NTP step) and a rounded
            # timestamp can land just past now. Too wide costs some latency,
            # too narrow costs another 429.
            age = max(0.0, time.time() - float(saved.get('at', 0)))
        except (TypeError, ValueError):
            return
        # nothing worth restoring, or old enough that it would have relaxed anyway
        if interval <= self.base_interval or age > self.RELAX_AFTER:
            return
        self.min_interval = min(interval, self.MAX_INTERVAL)
        # keep the relax deadline where the 429 put it, or the hour would start
        # again on every launch
        self._last_429 = time.monotonic() - age

    def holding(self) -> bool:
        return time.monotonic() < self._hold_until

    def hold_remaining(self) -> float:
        return max(0.0, self._hold_until - time.monotonic())

    async def slot(self, max_wait: float, urgent: bool = False) -> bool:
        """Reserve a request slot. False if the next one is further than max_wait.

        urgent=True skips the queue of ordinary reservations; the hold and the
        spacing after the last sent request still apply.
        """
        if not self._restored:
            self._restore_once()
        now = time.monotonic()
        if self.min_interval > self.base_interval and now - self._last_429 > self.RELAX_AFTER:
            self.min_interval = self.base_interval
        if urgent:
            # from the last request sent, not the last reserved: reservations
            # can sit a whole pass ahead
            start = max(now, self._hold_until, self._last_sent + self.min_interval)
        else:
            start = max(now, self._next_slot, self._hold_until)
        if start - now > max_wait:
            return False
        self._next_slot = max(self._next_slot, start) + self.min_interval
        if start > now:
            await asyncio.sleep(start - now)
        self._last_sent = time.monotonic()
        return True

    def report_429(self, retry_after: float = 0.0) -> None:
        """The site said 429. `retry_after` is its own Retry-After, if it sent one."""
        # requests in flight when the first 429 lands get one too; still one
        # incident, so do not double the penalty several times over
        if self.holding():
            return
        # A live 429 outranks the file, which only exists to spare a cold start.
        # Restoring over it would also reset the relax deadline, so the hour of
        # quiet would never finish counting down.
        self._restored = True
        self._last_429 = time.monotonic()
        self.min_interval = min(self.min_interval * 1.5, self.MAX_INTERVAL)
        self._penalty = min(max(60.0, self._penalty * 2), 900.0)
        self._hold_until = time.monotonic() + max(self._penalty, retry_after)
        # log it here: a 429 hit while polling used to raise the banner and
        # leave no trace at all
        from . import logbook
        # min_interval 0 (the tests use one) has no rate to quote
        _save_learned(self.name, self.min_interval)
        rate = f' ({60 / self.min_interval:.0f} req/min)' if self.min_interval > 0 else ''
        logbook.event(f'RATE LIMIT 429 from {self.name}: holding '
                      f'{self._hold_until - time.monotonic():.0f}s, spacing widened to '
                      f'{self.min_interval:.2f}s{rate}')

    def report_ok(self) -> None:
        self._penalty = 0.0

    def release(self) -> None:
        """Lift the hold early for a manual retry. The penalty still grows if
        the site answers 429 again."""
        self._hold_until = 0.0


# 1.5 s caps it at 40 requests a minute. 0.8 s earned 429s within a minute with
# 31 channels; the limit is undocumented but ~60/min fits what has been seen.
# The monitor keeps passes short by polling idle watch-only channels less often.
_CB_THROTTLE = _HostThrottle(min_interval=1.5, name='chaturbate')


def _retry_after(r: httpx.Response) -> float:
    try:
        return float(r.headers.get('retry-after', 0))
    except ValueError:
        return 0.0


def rate_limited(platform: str) -> bool:
    """Is this platform currently holding off after a 429?"""
    return platform == 'chaturbate' and _CB_THROTTLE.holding()


def rate_limit_remaining(platform: str) -> float:
    return _CB_THROTTLE.hold_remaining() if platform == 'chaturbate' else 0.0


def clear_rate_limit(platform: str) -> None:
    if platform == 'chaturbate':
        _CB_THROTTLE.release()


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
            # the playlist advertises decoy segments (media.mp4, always 404) and hides
            # the real encrypted ones behind these tags. Without the key ffmpeg would
            # download only 404s, so fail here instead of recording nothing.
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
    if not await _CB_THROTTLE.slot(max_wait=10, urgent=True):
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

    Feeding this to ffmpeg as a single input is what keeps the recording in sync.
    With the audio playlist as a second -i, ffmpeg shifts each input to start at
    zero independently: it opens the video first, spends a second or three probing
    it, and by then the audio's live edge has moved on, so the audio lands that far
    ahead of the picture by a different amount every capture. One input gets one
    common shift and the shared source timeline survives (measured: 1.6 s of audio
    lead with two inputs, frame-exact alignment with one).
    """
    if not await _CB_THROTTLE.slot(max_wait=10, urgent=True):
        raise RateLimited(t('chaturbate is rate limiting (429); retrying in ~{}s',
                            'chaturbate está limitando las peticiones (429); se reintenta en ~{}s')
                          .format(int(_CB_THROTTLE.hold_remaining()) + 1))
    async with httpx.AsyncClient(headers=REQUEST_HEADERS, timeout=20,
                                 follow_redirects=True) as client:
        r = await client.get(f'https://chaturbate.com/api/chatvideocontext/{username}/')
        if r.status_code == 429:
            _CB_THROTTLE.report_429(_retry_after(r))
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
    if group:
        if group.group(1) not in audio_lines:
            # the variant points at a separate audio rendition that could not be
            # attached; a master with only the video-only variant records silent.
            # Tell the caller to fall back to the dual-input resolver.
            raise RuntimeError('audio rendition not found in master')
        lines.append(audio_lines[group.group(1)])
    # no AUDIO attribute means the audio is muxed into the variant's own segments,
    # which -map 0:a:0? in the capture command picks up
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

    # Chaturbate. Preferred path: a local master playlist given to ffmpeg as one
    # input (see resolve_chaturbate_master for why that keeps A/V in sync).
    # No -user_agent: the input is a local file and the option belongs to the
    # http protocol, so ffmpeg rejects it. The CDN serves fine without it.
    try:
        master = await resolve_chaturbate_master(username, quality)
    except StreamNotAvailable:
        raise
    except Exception:
        # page layout changed; the yt-dlp route still records, with the
        # audio-lead problem
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
    # -map 0:a:0? : the '?' keeps ffmpeg from failing when a stream carries no
    # audio, while still capturing it whenever it is there
    return [ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning',
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-i', str(master_path),
            '-map', '0:v:0', '-map', '0:a:0?',
            '-c', 'copy', '-f', 'mpegts', str(out_ts)]
