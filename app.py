from __future__ import annotations

import multiprocessing

# In a frozen build the native-window child re-runs this script; freeze_support
# must intercept it before anything below (config, monitor, server) executes.
multiprocessing.freeze_support()

import asyncio
import contextlib
import json
import logging

from nicegui import app, ui

from recam import (config, i18n, logbook, native_close, status, tray, ui_dialogs,
                        ui_library, ui_panel, ui_settings, ui_theme, ui_tutorial)
from recam.i18n import t
from recam.library import Library
from recam.monitor import Monitor

cfg = config.load()
i18n.set_language(getattr(cfg, 'language', 'en'))
streamers = config.load_streamers()
library = Library(cfg)
monitor = Monitor(cfg, streamers, library)

# Seeking or closing a video makes the browser drop the connection mid-response.
# On Windows that shows up as ConnectionReset / "No response returned" /
# EndOfStream tracebacks even though the file was served fine, burying the
# errors that matter.
_BENIGN_DISCONNECT = ('No response returned', 'EndOfStream',
                      'slot belongs to has been deleted',
                      'ConnectionState.CLOSED')
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


def _patch_wsproto_shutdown() -> None:
    """Keep quitting from failing on a websocket the client closed first.

    On shutdown uvicorn's wsproto protocol sends CloseConnection to every open
    websocket; if the window died a moment earlier its connection is already
    CLOSED and wsproto raises LocalProtocolError. That aborts the uvicorn
    shutdown before the lifespan handlers run, so captures in flight never get
    finalized. Swallowing it and closing the transport is what the original code
    does right after the send.
    """
    with contextlib.suppress(Exception):
        from uvicorn.protocols.websockets import wsproto_impl
        from wsproto.utilities import LocalProtocolError
        original = wsproto_impl.WSProtocol.shutdown

        def shutdown(self) -> None:
            try:
                original(self)
            except LocalProtocolError:
                with contextlib.suppress(Exception):
                    self.transport.close()

        wsproto_impl.WSProtocol.shutdown = shutdown


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
    """Keep data/status.json warm so `python -m recam.cli now` works while the GUI runs."""
    while True:
        status.write(monitor)
        await asyncio.sleep(5)


def _start_status_writer() -> None:
    asyncio.create_task(_status_writer())


_quiet_disconnect_noise()
_patch_wsproto_shutdown()

app.add_media_files('/media', cfg.recordings_path)
app.on_startup(_install_loop_exception_handler)
app.on_startup(monitor.start)
app.on_startup(_start_status_writer)
app.on_shutdown(monitor.shutdown)

try:
    import webview  # noqa: F401  (pywebview is what makes the window native)
    NATIVE = True
except ImportError:
    NATIVE = False   # no pywebview: fall back to opening a browser tab

ICON_PATH = config.BASE_DIR / 'recam.ico'
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
    native_close.allow_close()   # else the veto would cancel the shutdown
    app.shutdown()


def _setup_tray() -> None:
    if not NATIVE:
        return
    icon = tray.start(ICON_PATH, _show_from_tray, _quit_app)
    if icon is not None:
        _tray_active['on'] = True
        logbook.event('Tray active: closing the window can hide it to the background')
    else:
        logbook.event('No tray: closing the window only offers quitting')


# --- the X asks instead of closing; minimizing just minimizes ---
# native_close vetoes the native close and forwards a 'closing' event. Each page
# client registers a dialog here so the question shows up on whichever page is open.
_close_dialogs: list[dict] = []


def _on_window_closing(_args=None) -> None:
    win = app.native.main_window
    if win:   # the X can also come from the taskbar while minimized
        with contextlib.suppress(Exception):
            win.show()
            win.restore()
    rec_count = len(monitor.recordings)
    warn = (t('{} capture(s) running. Quitting finalizes and saves them first; hiding '
              'keeps them recording.',
              '{} grabación(es) en curso. Cerrar del todo las finaliza y guarda primero; '
              'esconder las mantiene grabando.')
            .format(rec_count) if rec_count else '')
    opened = False
    for entry in _close_dialogs[:]:
        try:
            entry['warn_text'].set_text(warn)
            entry['warn'].set_visibility(bool(warn))
            entry['dialog'].open()
            opened = True
        except Exception:
            _close_dialogs.remove(entry)
    if not opened:
        # no page is connected to ask on; do the safe thing
        if _tray_active['on']:
            _hide_to_tray()
        else:
            _quit_app()


if NATIVE:
    native_close.install()
    app.on_startup(_setup_tray)
    app.on_shutdown(tray.stop)
    app.native.on('closing', _on_window_closing)


@ui.page('/')
def index() -> None:
    """Every client (PC, phone…) builds its own view over the shared engine."""
    ui_theme.apply()

    # welcome dialog: shown on every start until the user opts out
    if getattr(cfg, 'show_beta_notice', True):
        beta_dialog = ui_dialogs.beta_notice(cfg, ui_tutorial.show)
        # on_connect, not a timer: an open() pushed before the websocket handshake
        # finishes would be lost and the welcome would never show
        ui.context.client.on_connect(lambda: beta_dialog.open())

    _close_dialogs.append(ui_dialogs.close_question(_tray_active['on'], _hide_to_tray, _quit_app))

    # the activity feed lives in a drawer on the right, toggled from the header
    activity = ui_panel.build_activity(monitor)

    with ui.header().classes('items-center gap-2 px-4 h-[52px]'):
        # Hidden in the native window: its title bar already shows the same dot
        # and word 56px above, so repeating them costs 73px of the bar. A browser
        # tab puts the title in the tab strip instead, so over LAN this header is
        # the only thing naming the app and it keeps them.
        if not NATIVE:
            ui.icon('radio_button_checked').classes('text-xl text-rose-600')
            ui.label('Recam').classes('text-[15px] font-semibold tracking-tight mr-4')
        with ui.tabs().props('dense no-caps inline-label indicator-color=primary '
                             'active-color=white').classes('h-[52px]') as tabs:
            tab_panel = ui.tab(t('Panel', 'Panel'), icon='monitor_heart')
            tab_lib = ui.tab(t('Library', 'Biblioteca'), icon='video_library')
            tab_cfg = ui.tab(t('Settings', 'Ajustes'), icon='settings')
        ui.space()
        rec_badge = ui.badge('').props('rounded color=primary') \
            .classes('text-xs font-semibold px-2.5 py-1')
        rec_badge.set_visibility(False)
        with ui.button(icon='history', on_click=activity.toggle) \
                .props('flat dense color=grey-5') as history_btn:
            # the dot must not swallow clicks meant for the button under it
            unread_badge = ui.badge('').props('floating rounded color=amber') \
                .classes('pointer-events-none')
            unread_badge.set_visibility(False)
        history_btn.tooltip(t('Activity', 'Actividad'))
        ui.button(icon='help_outline', on_click=ui_tutorial.show) \
            .props('flat round dense color=grey-5').tooltip(t('Tutorial', 'Tutorial'))
        if NATIVE and _tray_active['on']:
            ui.button(icon='visibility_off', on_click=_hide_to_tray) \
                .props('flat round dense color=grey-5') \
                .tooltip(t('Hide to the tray (keeps recording)',
                           'Esconder a la bandeja (sigue grabando)'))

    with ui.tab_panels(tabs, value=tab_panel).classes('w-full bg-transparent'):
        with ui.tab_panel(tab_panel):
            panel_tick = ui_panel.build(monitor)
        with ui.tab_panel(tab_lib):
            library_rescan = ui_library.build(library, monitor)
        with ui.tab_panel(tab_cfg):
            ui_settings.build(cfg, monitor)

    seen_version = [-1]   # differs from library.version, so the first tick scans
    seen_count = [-1]
    drawer_open = [False]

    async def tick() -> None:
        panel_tick()
        activity.tick()
        unread_badge.set_visibility(activity.unread() > 0)
        if activity.is_open != drawer_open[0]:
            drawer_open[0] = activity.is_open
            if drawer_open[0]:
                history_btn.classes(add='bg-white/10')
            else:
                history_btn.classes(remove='bg-white/10')
        rec_count = len(monitor.recordings)
        if rec_count != seen_count[0]:
            # keep the recording count visible in the header and the tab title
            seen_count[0] = rec_count
            rec_badge.set_text(t('{} recording', '{} grabando').format(rec_count))
            rec_badge.set_visibility(rec_count > 0)
            title = f'⏺ {rec_count} · Recam' if rec_count else 'Recam'
            ui.run_javascript(f'document.title = {json.dumps(title)}')
        if seen_version[0] != library.version:
            seen_version[0] = library.version
            await library_rescan()

    ui.timer(0.1, tick, once=True)
    ui.timer(2.0, tick)


if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        host='0.0.0.0' if cfg.lan_access else '127.0.0.1',
        port=cfg.port,
        title='Recam',
        favicon=str(ICON_PATH) if ICON_PATH.exists() else '🎥',
        dark=True,
        language='es' if i18n.current == 'es' else 'en-US',
        reload=False,
        native=NATIVE,
        window_size=(1240, 840) if NATIVE else None,
        show=not NATIVE,
        show_welcome_message=False,
    )
