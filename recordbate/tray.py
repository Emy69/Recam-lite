from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from . import logbook
from .i18n import t

_icon = None


def start(icon_path: Path, on_show: Callable[[], None],
          on_quit: Callable[[], None]) -> object | None:
    """Run the tray icon on a daemon thread. None means there is no tray.

    The caller needs that answer: without a tray, minimising must not hide the
    window or there would be no way to get it back.
    """
    global _icon
    try:
        import pystray
        from PIL import Image
    except Exception as exc:
        logbook.event(f'Bandeja no disponible (falta pystray/Pillow): {exc!r}')
        return None

    try:
        image = Image.open(icon_path)
    except Exception:
        from PIL import Image as _Image
        image = _Image.new('RGBA', (64, 64), (225, 29, 72, 255))

    menu = pystray.Menu(
        pystray.MenuItem(t('Show RecordBate', 'Mostrar RecordBate'),
                         lambda icon, item: on_show(), default=True),
        pystray.MenuItem(t('Quit', 'Salir'), lambda icon, item: on_quit()),
    )
    _icon = pystray.Icon('recordbate', image, 'RecordBate', menu)

    def _run() -> None:
        try:
            _icon.run()
        except Exception as exc:
            logbook.event(f'Fallo en la bandeja: {exc!r}')

    threading.Thread(target=_run, name='tray', daemon=True).start()
    return _icon


def stop() -> None:
    global _icon
    if _icon is not None:
        try:
            _icon.stop()
        except Exception:
            pass
        _icon = None
