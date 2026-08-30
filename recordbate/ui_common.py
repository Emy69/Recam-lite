from __future__ import annotations

import contextlib
import json
import os

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


def open_log_file() -> None:
    try:
        if hasattr(os, 'startfile'):
            os.startfile(str(logbook.LOG_FILE))   # type: ignore[attr-defined]
        else:
            notify(f'Registro en: {logbook.LOG_FILE}', type='info')
    except OSError as exc:
        notify(f'No se pudo abrir el registro: {exc}', type='negative')
