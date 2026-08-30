from __future__ import annotations

import html as html_mod
import time

from nicegui import ui

from . import tools
from .models import STATUS_LABELS, Status, Streamer
from .monitor import Monitor
from .platforms import PLATFORM_COLORS
from .ui_common import copy_to_clipboard, notify, open_log_file, refresh


def build(monitor: Monitor):
    """Build the Panel tab. Returns the callback the page timer should tick."""

    @ui.refreshable
    def streamer_list() -> None:
        if not monitor.streamers:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('videocam_off', size='xl').classes('text-gray-600')
                ui.label('Añade tu primer canal pegando su URL arriba').classes('text-gray-500')
            return
        for s in list(monitor.streamers):
            _streamer_card(monitor, s, streamer_list)

    with ui.column().classes('w-full max-w-4xl mx-auto gap-3'):
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
                streamer_list.refresh()

            url_input.on('keydown.enter', add)
            ui.button('Añadir', icon='add', on_click=add).props('unelevated')

        with ui.row().classes('w-full items-center gap-4'):
            ui.switch('Vigilancia automática').bind_value(monitor, 'enabled') \
                .tooltip('Apagada: no se comprueban canales ni se inician grabaciones nuevas')
            ui.space()
            summary = ui.label().classes('text-sm text-gray-500')

        with ui.column().classes('w-full gap-2'):
            streamer_list()

    last_signature: list = [None]

    def tick() -> None:
        rec_count = len(monitor.recordings)
        live = sum(1 for s in monitor.streamers
                   if s.status in (Status.ONLINE, Status.RECORDING))
        summary.set_text(f'{len(monitor.streamers)} canales · {live} en vivo · '
                         f'{rec_count} grabando')
        # rebuilding the cards throws away focus and open menus, so only do it when
        # something a card actually shows has changed
        signature = tuple(
            (s.key, s.status.value, s.auto_record, s.last_result, s.last_error,
             s.key in monitor.recordings,
             getattr(monitor.recordings.get(s.key), 'state', None))
            for s in monitor.streamers
        )
        if signature != last_signature[0]:
            last_signature[0] = signature
            streamer_list.refresh()

    return tick


def _streamer_card(monitor: Monitor, s: Streamer, streamer_list) -> None:
    rec = monitor.recordings.get(s.key)
    status_label, status_color = STATUS_LABELS[s.status]

    async def do_record() -> None:
        # notify before awaiting: start_recording resolves stream URLs and is slow
        # enough for the panel to refresh and take this card with it
        notify(f'Intentando grabar a {s.username}… si no hay directo se cancela solo.',
               type='info')
        s.cooldown_until = 0
        await monitor.start_recording(s)
        refresh(streamer_list)

    async def do_stop() -> None:
        notify('Deteniendo… el archivo se procesará en unos segundos. '
               'La auto-grabación de este canal queda en pausa 10 minutos.', type='info')
        await monitor.stop_recording(s)
        refresh(streamer_list)

    async def do_check() -> None:
        status = await monitor.manual_check(s)
        notify(f'{s.username}: {STATUS_LABELS[status][0]}', type='info')
        refresh(streamer_list)

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
            refresh(streamer_list)

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
                          on_click=lambda: copy_to_clipboard(text)).props('unelevated')
                ui.button('Cerrar', on_click=d.close).props('flat')
        d.open()

    with ui.card().classes('w-full').props('flat bordered'):
        with ui.row().classes('w-full items-center gap-3 flex-nowrap'):
            fg = '#111' if s.platform == 'kick' else 'white'
            ui.badge(s.platform).style(
                f'background-color: {PLATFORM_COLORS.get(s.platform, "#666")}; color: {fg}')
            with ui.column().classes('gap-0 grow min-w-0'):
                ui.link(s.username, s.url, new_tab=True) \
                    .classes('text-base font-medium no-underline hover:underline')
                sub = _subtitle(s, rec)
                if sub:
                    ui.label(sub).classes('text-xs text-gray-500 truncate w-full')
            if rec:
                live = ui.label().classes('text-sm font-mono text-red-400 whitespace-nowrap')

                def update_live(rec=rec, live=live) -> None:
                    live.set_text(f'⏺ {tools.human_duration(rec.elapsed)} · '
                                  f'{tools.human_size(rec.size)} · {rec.state}')

                update_live()
                ui.timer(1.0, update_live)
            ui.badge(status_label).props(f'color={status_color}').classes('whitespace-nowrap')
            ui.switch(value=s.auto_record,
                      on_change=lambda e: (setattr(s, 'auto_record', e.value),
                                           monitor.persist())) \
                .props('dense').tooltip('Auto-grabar cuando esté en vivo')
            if rec:
                ui.button(icon='stop', on_click=do_stop).props('round flat color=red') \
                    .tooltip('Detener y guardar')
            else:
                ui.button(icon='fiber_manual_record', on_click=do_record) \
                    .props('round flat color=red').tooltip('Grabar ahora')
                ui.button(icon='refresh', on_click=do_check).props('round flat') \
                    .tooltip('Comprobar estado')
            with ui.button(icon='more_vert').props('round flat'):
                with ui.menu():
                    ui.menu_item('Ver registro', on_click=show_log)
                    ui.menu_item('Quitar de la lista', on_click=do_remove)


def _subtitle(s: Streamer, rec) -> str:
    parts = []
    if s.last_result:
        parts.append(s.last_result)
    elif s.last_error:
        parts.append(f'⚠ {s.last_error[:120]}')
    remaining = s.cooldown_until - time.time()
    if not rec and remaining > 90:
        parts.append(f'auto en pausa {int(remaining / 60)} min')
    return ' · '.join(parts)
