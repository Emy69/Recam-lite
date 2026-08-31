from __future__ import annotations

import asyncio
import contextlib
import html as html_mod
import os
import time

from nicegui import ui

from . import config as config_mod
from . import tools
from .models import STATUS_LABELS, Status, Streamer
from .monitor import Monitor
from .platforms import PLATFORM_COLORS
from .ui_common import copy_to_clipboard, notify, open_log_file, refresh

# rows sort by usefulness: recording first, then live, then the rest
_ORDER = {Status.RECORDING: 0, Status.ONLINE: 1, Status.UNKNOWN: 2, Status.OFFLINE: 3}

_EVENT_STYLE = {
    'start': ('fiber_manual_record', 'text-red-500'),
    'saved': ('check_circle', 'text-green-600'),
    'fail': ('warning', 'text-amber-500'),
}

# shared column widths so the header lines up with the rows
_COL_STATE = 'w-32 flex-none flex justify-center'
_COL_AUTO = 'w-12 flex-none flex justify-center'
_COL_ACTIONS = 'w-32 flex-none flex justify-end items-center gap-0'


def build(monitor: Monitor):
    """Build the Panel tab. Returns the callback the page timer should tick."""

    def ordered_streamers() -> list[Streamer]:
        return sorted(monitor.streamers,
                      key=lambda s: (0 if s.key in monitor.recordings
                                     else _ORDER.get(s.status, 2),
                                     s.username.lower()))

    @ui.refreshable
    def streamer_table() -> None:
        if not monitor.streamers:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('videocam_off', size='xl').classes('text-gray-600')
                ui.label('Añade tu primer canal pegando su URL arriba').classes('text-gray-500')
            return
        with ui.card().classes('w-full p-0 gap-0 overflow-hidden').props('flat bordered'):
            with ui.row().classes('w-full items-center gap-3 px-3 py-2 flex-nowrap '
                                  'text-[11px] uppercase tracking-wider text-gray-500'):
                ui.label('Canal').classes('w-64 flex-none')
                ui.label('Última actividad').classes('grow min-w-0')
                ui.label('Estado').classes(_COL_STATE.replace('flex ', '') + ' text-center')
                ui.label('Auto').classes(_COL_AUTO.replace('flex ', '') + ' text-center')
                ui.element('div').classes(_COL_ACTIONS)
            for s in ordered_streamers():
                _streamer_row(monitor, s, streamer_table)

    @ui.refreshable
    def activity() -> None:
        if not monitor.events:
            return
        with ui.card().classes('w-full gap-1 p-3').props('flat bordered'):
            ui.label('Actividad reciente').classes('text-sm font-medium')
            for ev in list(monitor.events)[-8:][::-1]:
                icon, color = _EVENT_STYLE.get(ev['kind'], ('info', 'text-gray-500'))
                with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                    ui.icon(icon, size='xs').classes(color)
                    ui.label(ev['text'][:120]).classes('text-xs truncate grow') \
                        .tooltip(ev['text'])
                    ui.label(tools.human_ago(ev['ts'])) \
                        .classes('text-xs text-gray-500 whitespace-nowrap')

    with ui.column().classes('w-full max-w-5xl mx-auto gap-3'):
        with ui.row().classes('w-full items-center gap-2'):
            url_input = ui.input(
                placeholder='Pega la URL del canal (Twitch, Kick, Stripchat, Chaturbate)…',
            ).props('outlined dense clearable').classes('grow')

            def add() -> None:
                text = (url_input.value or '').strip()
                if not text:
                    return
                try:
                    s = monitor.add_streamer(text)
                except ValueError as exc:
                    ui.notify(str(exc), type='warning')
                    return
                url_input.value = ''
                ui.notify(f'{s.username} ({s.platform}) añadido. '
                          'Se comprueba en el próximo ciclo.', type='positive')
                streamer_table.refresh()

            url_input.on('keydown.enter', add)
            ui.button('Añadir', icon='add', on_click=add).props('unelevated')

        with ui.row().classes('w-full items-center gap-4'):
            ui.switch('Vigilancia automática').bind_value(monitor, 'enabled') \
                .tooltip('Apagada: no se comprueban canales ni se inician grabaciones nuevas')

            async def check_all() -> None:
                if not monitor.streamers:
                    return
                notify(f'Comprobando {len(monitor.streamers)} canales…', type='info')
                results = await asyncio.gather(
                    *(monitor.manual_check(s) for s in monitor.streamers),
                    return_exceptions=True)
                live = sum(1 for r in results if r == Status.ONLINE)
                notify(f'{live} en vivo de {len(monitor.streamers)}', type='positive')
                refresh(streamer_table)

            ui.button('Comprobar ahora', icon='radar', on_click=check_all) \
                .props('flat dense no-caps').tooltip('Comprueba todos los canales ya')
            ui.space()
            summary = ui.label().classes('text-sm text-gray-500')

        streamer_table()
        activity()

    last_signature: list = [None]

    def tick() -> None:
        rec_count = len(monitor.recordings)
        live = sum(1 for s in monitor.streamers
                   if s.status in (Status.ONLINE, Status.RECORDING))
        parts = [f'{len(monitor.streamers)} canales', f'{live} en vivo',
                 f'{rec_count} grabando']
        free = tools.disk_free(monitor.cfg.recordings_dir)
        if free is not None:
            parts.append(f'{tools.human_size(free)} libres')
        summary.set_text(' · '.join(parts))
        # rebuilding the rows throws away focus and open menus, so only do it when
        # something a row actually shows has changed
        signature = tuple(
            (s.key, s.status.value, s.auto_record, s.last_result, s.last_error,
             s.key in monitor.recordings,
             getattr(monitor.recordings.get(s.key), 'state', None))
            for s in monitor.streamers
        ) + ((monitor.events[-1]['ts'], len(monitor.events)) if monitor.events else ())
        if signature != last_signature[0]:
            last_signature[0] = signature
            streamer_table.refresh()
            activity.refresh()

    return tick


def _streamer_row(monitor: Monitor, s: Streamer, streamer_table) -> None:
    rec = monitor.recordings.get(s.key)
    status_label, status_color = STATUS_LABELS[s.status]

    async def do_record() -> None:
        # notify before awaiting: start_recording resolves stream URLs and is slow
        # enough for the table to refresh and take this row with it
        notify(f'Intentando grabar a {s.username}… si no hay directo se cancela solo.',
               type='info')
        s.cooldown_until = 0
        await monitor.start_recording(s)
        refresh(streamer_table)

    async def do_stop() -> None:
        notify('Deteniendo… el archivo se procesará en unos segundos. '
               'La auto-grabación de este canal queda en pausa 10 minutos.', type='info')
        await monitor.stop_recording(s)
        refresh(streamer_table)

    async def do_check() -> None:
        status = await monitor.manual_check(s)
        notify(f'{s.username}: {STATUS_LABELS[status][0]}', type='info')
        refresh(streamer_table)

    async def do_remove() -> None:
        with ui.dialog() as confirm, ui.card():
            ui.label(f'¿Quitar a {s.username} de la lista?')
            ui.label('Las grabaciones ya guardadas no se tocan.') \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancelar', on_click=lambda: confirm.submit(False)).props('flat')
                ui.button('Quitar', color='red', on_click=lambda: confirm.submit(True))
        if await confirm:
            await monitor.remove_streamer(s)
            notify(f'{s.username} eliminado de la lista', type='positive')
            refresh(streamer_table)

    def open_folder() -> None:
        folder = monitor.cfg.recordings_path / config_mod.sanitize_segment(s.username)
        target = folder if folder.is_dir() else monitor.cfg.recordings_path
        with contextlib.suppress(OSError, AttributeError):
            os.startfile(str(target))   # type: ignore[attr-defined]

    def show_log() -> None:
        live = monitor.recordings.get(s.key)   # re-read: it may have ended since paint
        header = [f'Canal:   {s.username} ({s.platform})',
                  f'URL:     {s.url}',
                  f'Estado:  {STATUS_LABELS[s.status][0]}']
        if live:
            header.append(f'Salida:  grabando ahora ({live.state})')
            header.append(f'Comando: {live.cmd}')
            body_lines = list(live.log)
        else:
            if s.last_exit:
                header.append(f'Salida:  {s.last_exit}')
            if s.last_reason or s.last_error:
                header.append(f'Motivo:  {s.last_reason or s.last_error}')
            if s.last_cmd:
                header.append(f'Comando: {s.last_cmd}')
            body_lines = s.last_log
        text = ('\n'.join(header) + '\n\n--- salida del grabador ---\n'
                + ('\n'.join(body_lines) or '(el grabador no imprimió nada)'))
        with ui.dialog() as d, ui.card().classes('w-[780px] max-w-full'):
            ui.label(f'Registro · {s.username}').classes('font-medium')
            with ui.element('div').classes('w-full max-h-96 overflow-auto bg-black rounded p-2'):
                ui.html(f'<pre style="white-space:pre-wrap;font-size:11px;margin:0;'
                        f'color:#e0e0e0">{html_mod.escape(text)}</pre>')
            ui.label('Aquí está el comando exacto y el motivo por el que se cerró.') \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Abrir .log completo', icon='description',
                          on_click=open_log_file).props('flat')
                ui.button('Copiar', icon='content_copy',
                          on_click=lambda: copy_to_clipboard(text, 'Registro copiado')) \
                    .props('unelevated')
                ui.button('Cerrar', on_click=d.close).props('flat')
        d.open()

    with ui.row().classes('rb-row w-full items-center gap-3 px-3 py-1.5 flex-nowrap '
                          'border-t border-white/5'):
        with ui.row().classes('w-64 flex-none items-center gap-2 flex-nowrap min-w-0'):
            fg = '#111' if s.platform == 'kick' else 'white'
            ui.badge(s.platform).style(
                f'background-color: {PLATFORM_COLORS.get(s.platform, "#666")}; '
                f'color: {fg}').classes('flex-none')
            ui.link(s.username, s.url, new_tab=True) \
                .classes('text-sm font-medium no-underline hover:underline '
                         '!text-gray-100 truncate')
        with ui.element('div').classes('grow min-w-0'):
            if rec:
                live = ui.label().classes('text-xs font-mono text-red-400 truncate w-full')

                def update_live(rec=rec, live=live) -> None:
                    live.set_text(f'⏺ {tools.human_duration(rec.elapsed)} · '
                                  f'{tools.human_size(rec.size)} · {rec.state}')

                update_live()
                ui.timer(1.0, update_live)
            else:
                sub = _subtitle(s)
                if sub:
                    ui.label(sub).classes('text-xs text-gray-500 truncate w-full') \
                        .tooltip(sub)
        with ui.element('div').classes(_COL_STATE):
            ui.badge(status_label).props(f'color={status_color}') \
                .classes('whitespace-nowrap')
        with ui.element('div').classes(_COL_AUTO):
            ui.switch(value=s.auto_record,
                      on_change=lambda e: (setattr(s, 'auto_record', e.value),
                                           monitor.persist())) \
                .props('dense').tooltip('Auto-grabar cuando esté en vivo')
        with ui.element('div').classes(_COL_ACTIONS):
            if rec:
                ui.button(icon='stop', on_click=do_stop).props('round flat dense color=red') \
                    .tooltip('Detener y guardar')
            else:
                ui.button(icon='fiber_manual_record', on_click=do_record) \
                    .props('round flat dense color=red').tooltip('Grabar ahora')
                ui.button(icon='refresh', on_click=do_check).props('round flat dense') \
                    .tooltip('Comprobar estado')
            with ui.button(icon='more_vert').props('round flat dense'):
                with ui.menu():
                    ui.menu_item('Ver registro', on_click=show_log)
                    ui.menu_item('Abrir su carpeta de grabaciones', on_click=open_folder)
                    ui.menu_item('Copiar URL del canal',
                                 on_click=lambda: copy_to_clipboard(s.url, 'URL copiada'))
                    ui.menu_item('Quitar de la lista', on_click=do_remove)


def _subtitle(s: Streamer) -> str:
    parts = []
    if s.last_result:
        parts.append(s.last_result)
    elif s.last_error:
        parts.append(f'⚠ {s.last_error[:120]}')
    remaining = s.cooldown_until - time.time()
    if remaining > 90:
        parts.append(f'auto en pausa {int(remaining / 60)} min')
    return ' · '.join(parts)
