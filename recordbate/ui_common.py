from __future__ import annotations

import contextlib
import json
import os
import urllib.parse

from nicegui import ui

from . import logbook


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


def copy_to_clipboard(text: str, message: str = 'Copiado al portapapeles') -> None:
    # execCommand and not navigator.clipboard: the native window is not a secure context
    payload = json.dumps(text)
    ui.run_javascript(
        '(function(){const t=' + payload + ';const a=document.createElement("textarea");'
        'a.value=t;a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);'
        'a.focus();a.select();try{document.execCommand("copy");}catch(e){}'
        'document.body.removeChild(a);})();')
    notify(message, type='positive')


def live_preview(rec, extra_classes: str = ''):
    """Image area showing a running capture's live preview frame.

    Returns a refresh callable meant to run on the caller's timer: it swaps the
    frame in place whenever the recorder wrote a new one, using the file mtime
    as a cache buster (.thumbs lives under the recordings root, so the /media
    route serves it).
    """
    with ui.element('div').classes('relative w-full h-28 bg-red-950/40 flex '
                                   'items-center justify-center overflow-hidden '
                                   + extra_classes):
        image = ui.image('').classes('w-full h-full object-cover')
        image.set_visibility(False)
        placeholder = ui.icon('fiber_manual_record', size='md').classes('text-red-500')
        ui.label('● REC').classes('absolute top-1 left-1 text-[10px] font-medium '
                                  'bg-red-600/90 px-1.5 py-0.5 rounded z-10')
    seen = {'mtime': 0}

    def refresh_frame() -> None:
        thumb = getattr(rec, 'live_thumb', None)
        if thumb and thumb[1] != seen['mtime']:
            seen['mtime'] = thumb[1]
            image.set_source('/media/' + urllib.parse.quote(thumb[0]) + f'?v={thumb[1]}')
            image.set_visibility(True)
            placeholder.set_visibility(False)

    return refresh_frame


def open_log_file() -> None:
    try:
        if hasattr(os, 'startfile'):
            os.startfile(str(logbook.LOG_FILE))   # type: ignore[attr-defined]
        else:
            notify(f'Registro en: {logbook.LOG_FILE}', type='info')
    except OSError as exc:
        notify(f'No se pudo abrir el registro: {exc}', type='negative')
