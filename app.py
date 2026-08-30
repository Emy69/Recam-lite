from __future__ import annotations

import asyncio
import contextlib
import logging

from nicegui import app, ui

from recordbate import config, logbook, status, tray, ui_library, ui_panel, ui_settings
from recordbate.library import Library
from recordbate.monitor import Monitor

cfg = config.load()
streamers = config.load_streamers()
library = Library(cfg)
monitor = Monitor(cfg, streamers, library)

# Seeking or closing a video makes the browser drop the connection mid-response.
# On Windows that surfaces as a wall of ConnectionReset / "No response returned" /
# EndOfStream tracebacks even though the file was served fine, which buries the
# errors that actually matter.
_BENIGN_DISCONNECT = ('No response returned', 'EndOfStream',
                      'slot belongs to has been deleted')
_BENIGN_ERRORS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


class _DisconnectFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        exc = record.exc_info[1] if record.exc_info and len(record.exc_info) > 1 else None
        if isinstance(exc, _BENIGN_ERRORS):
            return False
        if exc is not None:
            text += ' ' + repr(exc)
        return not any(b in text for b in _BENIGN_DISCONNECT)


def _quiet_disconnect_noise() -> None:
    for name in ('uvicorn.error', 'asyncio', 'nicegui'):
        logging.getLogger(name).addFilter(_DisconnectFilter())


def _install_loop_exception_handler() -> None:
    """Same noise, but raised straight at the loop by ProactorBasePipeTransport."""
    with contextlib.suppress(Exception):
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()

        def handler(loop_, context: dict) -> None:
            if isinstance(context.get('exception'), _BENIGN_ERRORS):
                return
            if previous is not None:
                previous(loop_, context)
            else:
                loop_.default_exception_handler(context)

        loop.set_exception_handler(handler)


async def _status_writer() -> None:
    """Keep data/status.json warm so `recordbate-cli now` works while the GUI runs."""
    while True:
        status.write(monitor)
        await asyncio.sleep(5)


def _start_status_writer() -> None:
    asyncio.create_task(_status_writer())


_quiet_disconnect_noise()

app.add_media_files('/media', cfg.recordings_path)
app.on_startup(_install_loop_exception_handler)
app.on_startup(monitor.start)
app.on_startup(_start_status_writer)
app.on_shutdown(monitor.shutdown)

try:
    import webview  # noqa: F401  — pywebview is what makes the window native
    NATIVE = True
except ImportError:
    NATIVE = False   # no pywebview: fall back to opening a browser tab

ICON_PATH = config.BASE_DIR / 'recordbate.ico'
_tray_active = {'on': False}


def _hide_to_tray() -> None:
    win = app.native.main_window
    if win:
        win.hide()


def _show_from_tray() -> None:
    win = app.native.main_window
    if win:
        win.show()
        win.restore()


def _quit_app() -> None:
    tray.stop()
    app.shutdown()


def _setup_tray() -> None:
    if not NATIVE:
        return
    icon = tray.start(ICON_PATH, _show_from_tray, _quit_app)
    if icon is None:
        logbook.event('Sin bandeja: la ventana se minimiza a la barra de tareas')
        return
    _tray_active['on'] = True
    # only hook this up once there is a tray icon to restore the window from
    app.native.on('minimized', lambda _=None: _hide_to_tray())
    logbook.event('Bandeja activa: minimizar esconde la ventana y sigue grabando')


if NATIVE:
    app.on_startup(_setup_tray)
    app.on_shutdown(tray.stop)


@ui.page('/')
def index() -> None:
    """Every client (PC, phone…) builds its own view over the shared engine."""
    ui.colors(primary='#e11d48')
    ui.dark_mode(True)

    with ui.header().classes('items-center gap-4 px-4'):
        ui.icon('radio_button_checked').classes('text-2xl')
        ui.label('RecordBate').classes('text-xl font-bold')
        with ui.tabs() as tabs:
            tab_panel = ui.tab('Panel', icon='monitor_heart')
            tab_lib = ui.tab('Biblioteca', icon='video_library')
            tab_cfg = ui.tab('Ajustes', icon='settings')
        ui.space()
        header_status = ui.label('').classes('text-sm opacity-80')
        if NATIVE and _tray_active['on']:
            ui.button(icon='close_fullscreen', on_click=_hide_to_tray) \
                .props('flat round').tooltip('Enviar a la bandeja (sigue grabando de fondo)')

    with ui.tab_panels(tabs, value=tab_panel).classes('w-full'):
        with ui.tab_panel(tab_panel):
            panel_tick = ui_panel.build(monitor)
        with ui.tab_panel(tab_lib):
            library_rescan = ui_library.build(library, monitor)
        with ui.tab_panel(tab_cfg):
            ui_settings.build(cfg, monitor)

    seen_version = [-1]   # differs from library.version, so the first tick scans

    async def tick() -> None:
        panel_tick()
        rec_count = len(monitor.recordings)
        header_status.set_text(f'⏺ {rec_count} grabando' if rec_count else '')
        if seen_version[0] != library.version:
            seen_version[0] = library.version
            await library_rescan()

    ui.timer(0.1, tick, once=True)
    ui.timer(2.0, tick)


if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        host='0.0.0.0' if cfg.lan_access else '127.0.0.1',
        port=cfg.port,
        title='RecordBate',
        favicon=str(ICON_PATH) if ICON_PATH.exists() else '🎥',
        dark=True,
        language='es',
        reload=False,
        native=NATIVE,
        window_size=(1240, 840) if NATIVE else None,
        show=not NATIVE,
        show_welcome_message=False,
    )
