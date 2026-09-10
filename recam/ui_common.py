from __future__ import annotations

import asyncio
import contextlib
import json
import os
import urllib.parse

from nicegui import ui

from . import logbook, tools
from .i18n import t


def notify(*args, **kwargs) -> None:
    """ui.notify that survives its own card disappearing.

    A handler that awaits gives the panel time to refresh and drop the element the
    toast is anchored to, and NiceGUI then raises "The parent element this slot
    belongs to has been deleted". Losing a toast beats losing the click.
    """
    with contextlib.suppress(Exception):
        ui.notify(*args, **kwargs)


def refresh(refreshable) -> None:
    with contextlib.suppress(Exception):
        refreshable.refresh()


def copy_to_clipboard(text: str, message: str = '') -> None:
    # execCommand and not navigator.clipboard: the native window is not a secure context
    payload = json.dumps(text)
    ui.run_javascript(
        '(function(){const t=' + payload + ';const a=document.createElement("textarea");'
        'a.value=t;a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);'
        'a.focus();a.select();try{document.execCommand("copy");}catch(e){}'
        'document.body.removeChild(a);})();')
    notify(message or t('Copied to the clipboard', 'Copiado al portapapeles'),
           type='positive')


def live_preview(rec, extra_classes: str = ''):
    """Image area showing a running capture's live preview frame.

    Returns a refresh callable meant to run on the caller's timer: it swaps the
    frame in place whenever the recorder wrote a new one, using the file mtime
    as a cache buster (.thumbs lives under the recordings root, so the /media
    route serves it).
    """
    with ui.element('div').classes('relative w-full aspect-video rounded-md bg-red-950/40 '
                                   'flex items-center justify-center overflow-hidden '
                                   + extra_classes):
        image = ui.image('').classes('absolute inset-0 w-full h-full object-cover')
        image.set_visibility(False)
        placeholder = ui.icon('fiber_manual_record', size='md').classes('text-red-500')
        ui.label('● REC').classes('absolute top-1.5 left-1.5 text-[10px] font-bold '
                                  'tracking-wider bg-rose-600 px-1.5 rounded z-10')
    seen = {'mtime': 0}

    def refresh_frame() -> None:
        thumb = getattr(rec, 'live_thumb', None)
        if thumb and thumb[1] != seen['mtime']:
            seen['mtime'] = thumb[1]
            image.set_source('/media/' + urllib.parse.quote(thumb[0]) + f'?v={thumb[1]}')
            image.set_visibility(True)
            placeholder.set_visibility(False)

    return refresh_frame


def live_thumbnail(url: str, cache_key: int, extra_classes: str = '') -> None:
    """Image area showing the site's own still of a live room we are not recording.

    Fetched once per `cache_key` (the caller passes the broadcast start): the
    browser reuses the copy across redraws of the tile, and a new broadcast gets
    a fresh one. Deliberately not refreshed on a timer.
    """
    with ui.element('div').classes('relative w-full aspect-video rounded-md bg-green-950/40 '
                                   'flex items-center justify-center overflow-hidden '
                                   + extra_classes):
        image = ui.image(f'{url}?t={cache_key}') \
            .classes('absolute inset-0 w-full h-full object-cover')
        placeholder = ui.icon('sensors', size='md').classes('text-green-500')
        placeholder.set_visibility(False)
        # a still that fails to load shows the icon rather than a broken frame
        image.on('error', lambda: (image.set_visibility(False),
                                   placeholder.set_visibility(True)))
        ui.label(t('● LIVE', '● EN VIVO')) \
            .classes('absolute top-1.5 left-1.5 text-[10px] font-bold tracking-wider '
                     'bg-green-700 px-1.5 rounded z-10')


def ffmpeg_downloader(on_done) -> None:
    """The download button with its progress, for when ffmpeg/ffprobe are missing.

    Shared by Settings and the Panel banner; `on_done` runs after a successful
    install (typically a refresh of whatever showed the button).
    """
    with ui.row().classes('items-center gap-2.5 flex-nowrap'):
        button = ui.button(t('Download ffmpeg', 'Descargar ffmpeg'), icon='download') \
            .props('unelevated dense no-caps no-wrap color=primary')
        status = ui.label().classes('font-mono text-xs text-gray-400 whitespace-nowrap')
    bar = ui.linear_progress(0, size='4px', show_value=False, color='primary') \
        .props('track-color=grey-9 rounded').classes('w-[360px] max-w-full')
    bar.set_visibility(False)

    async def download() -> None:
        progress = {'done': 0, 'total': 0, 'stage': 'download'}
        button.props('loading')
        bar.set_visibility(True)

        def paint() -> None:
            if progress['stage'] == 'download':
                total = progress['total']
                if total:
                    bar.set_value(progress['done'] / total)
                status.set_text(t('{} of {}', '{} de {}').format(
                    tools.human_size(progress['done']),
                    tools.human_size(total) if total else '?'))
            else:
                bar.set_value(1)
                status.set_text(t('Unpacking…', 'Descomprimiendo…'))

        painter = ui.timer(0.3, paint)
        try:
            await asyncio.to_thread(tools.download_ffmpeg, progress)
        except Exception as exc:
            painter.cancel()
            button.props(remove='loading')
            bar.set_visibility(False)
            status.set_text('')
            notify(t('Download failed: {}', 'La descarga falló: {}').format(exc),
                   type='negative')
            return
        painter.cancel()
        notify(t('ffmpeg and ffprobe installed in {}', 'ffmpeg y ffprobe instalados en {}')
               .format(tools.TOOLS_DIR), type='positive')
        on_done()

    button.on_click(download)


def open_log_file() -> None:
    try:
        if hasattr(os, 'startfile'):
            os.startfile(str(logbook.LOG_FILE))   # type: ignore[attr-defined]
        else:
            notify(t('Log at: {}', 'Registro en: {}').format(logbook.LOG_FILE),
                   type='info')
    except OSError as exc:
        notify(t('Could not open the log: {}', 'No se pudo abrir el registro: {}')
               .format(exc), type='negative')
