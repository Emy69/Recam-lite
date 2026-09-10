from __future__ import annotations

import contextlib
import html as html_mod
import os
import time

from nicegui import ui

from . import config as config_mod
from . import tools
from .i18n import t
from .models import Status, Streamer, state_label, status_label
from .monitor import Monitor
from .platforms import PLATFORM_COLORS, thumbnail_url
from .ui_common import (copy_to_clipboard, live_preview, live_thumbnail, notify,
                        open_log_file)

_EVENT_STYLE = {
    'start': ('fiber_manual_record', 'text-red-500'),
    'saved': ('check_circle', 'text-green-600'),
    'fail': ('warning', 'text-amber-500'),
}

# a responsive grid: as many tiles per row as the width allows
_GRID_STYLE = 'grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))'


def build(monitor: Monitor):
    """Build the Panel tab. Returns the callback the page timer should tick."""

    # the "live for …" labels; the page timer re-texts them without redrawing anything
    timeline_labels: dict[str, tuple[ui.label, Streamer]] = {}
    # one container per channel, so a change in one tile redraws only that tile
    tiles: dict[str, dict] = {}
    last_layout: list = [None]
    last_events: list = [None]

    def split_streamers() -> tuple[list[Streamer], list[Streamer]]:
        live = [s for s in monitor.streamers if s.is_live or s.key in monitor.recordings]
        live_keys = {s.key for s in live}
        rest = [s for s in monitor.streamers if s.key not in live_keys]
        # recording first, then whoever has been on the longest
        live.sort(key=lambda s: (s.key not in monitor.recordings,
                                 s.live_since or float('inf'), s.username.lower()))
        # the most recently live at the top, never-seen ones at the bottom
        rest.sort(key=lambda s: (-(s.last_online or s.last_broadcast_start),
                                 s.username.lower()))
        return live, rest

    def layout_signature() -> tuple:
        """Which channel sits where; a change here means the grid must be rebuilt."""
        live, rest = split_streamers()
        return tuple(s.key for s in live), tuple(s.key for s in rest)

    def tile_signature(s: Streamer) -> tuple:
        """Everything a tile shows that is not re-texted in place."""
        rec = monitor.recordings.get(s.key)
        return (s.status.value, s.auto_record, s.last_result, s.last_error,
                rec is not None, getattr(rec, 'state', None))

    def render_tile(s: Streamer) -> None:
        entry = tiles[s.key]
        entry['box'].clear()
        timeline_labels.pop(s.key, None)
        with entry['box']:
            _streamer_tile(monitor, s, sync, timeline_labels)
        entry['sig'] = tile_signature(s)

    def section(title: str, icon: str, color: str, items: list[Streamer],
                empty: str) -> None:
        with ui.row().classes('w-full items-center gap-2 mt-1'):
            ui.icon(icon, size='xs').classes(color)
            ui.label(title).classes('text-sm font-medium')
            ui.badge(str(len(items))).props('outline color=grey-6')
        if not items:
            ui.label(empty).classes('text-xs text-gray-500 pl-6')
            return
        with ui.element('div').classes('w-full grid gap-2 items-start').style(_GRID_STYLE):
            for s in items:
                tiles[s.key] = {'box': ui.element('div').classes('w-full'), 'sig': None}
                render_tile(s)

    @ui.refreshable
    def streamer_table() -> None:
        timeline_labels.clear()
        tiles.clear()
        last_layout[0] = layout_signature()
        if not monitor.streamers:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('videocam_off', size='xl').classes('text-gray-600')
                ui.label(t('Add your first channel by pasting its URL above',
                           'Añade tu primer canal pegando su URL arriba')) \
                    .classes('text-gray-500')
            return
        live, rest = split_streamers()
        section(t('Live now', 'En vivo ahora'), 'sensors', 'text-green-500', live,
                t('Nobody is live right now', 'Nadie está en vivo ahora mismo'))
        section(t('Not broadcasting', 'Sin emitir'), 'videocam_off', 'text-gray-500', rest,
                t('Everyone is live', 'Todos están en vivo'))

    def sync() -> None:
        """Redraw what changed: the whole grid only when a channel moved between
        sections (or was added/removed); otherwise just the tiles whose state
        changed. Rebuilding everything on each change made the page flicker."""
        with contextlib.suppress(Exception):   # a handler may outlive its own tile
            if layout_signature() != last_layout[0]:
                streamer_table.refresh()
                return
            for s in list(monitor.streamers):
                entry = tiles.get(s.key)
                if entry is not None and entry['sig'] != tile_signature(s):
                    render_tile(s)

    @ui.refreshable
    def activity() -> None:
        if not monitor.events:
            return
        with ui.card().classes('w-full gap-1 p-3').props('flat bordered'):
            ui.label(t('Recent activity', 'Actividad reciente')).classes('text-sm font-medium')
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
                placeholder=t('Paste a Chaturbate channel URL…',
                              'Pega la URL de un canal de Chaturbate…'),
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
                ui.notify(t('{} ({}) added. It gets checked on the next cycle.',
                            '{} ({}) añadido. Se comprueba en el próximo ciclo.')
                          .format(s.username, s.platform), type='positive')
                sync()

            url_input.on('keydown.enter', add)
            ui.button(t('Add', 'Añadir'), icon='add', on_click=add).props('unelevated')

        with ui.row().classes('w-full items-center gap-4'):
            ui.switch(t('Automatic monitoring', 'Vigilancia automática')) \
                .bind_value(monitor, 'enabled') \
                .tooltip(t('Off: no channel checks, no new recordings get started',
                           'Apagada: no se comprueban canales ni se inician grabaciones nuevas'))

            async def check_all() -> None:
                if not monitor.streamers or monitor.check_progress:
                    return
                check_btn.props('loading')
                try:
                    live = await monitor.check_all()
                finally:
                    check_btn.props(remove='loading')
                notify(t('{} live out of {}', '{} en vivo de {}')
                       .format(live, len(monitor.streamers)), type='positive')
                sync()

            check_btn = ui.button(t('Check now', 'Comprobar ahora'), icon='radar',
                                  on_click=check_all) \
                .props('flat dense no-caps') \
                .tooltip(t('Check every channel right now', 'Comprueba todos los canales ya'))
            ui.space()
            summary = ui.label().classes('text-sm text-gray-500')

        streamer_table()
        activity()

    def tick() -> None:
        rec_count = len(monitor.recordings)
        live = sum(1 for s in monitor.streamers
                   if s.status in (Status.ONLINE, Status.RECORDING))
        parts = [t('{} channels', '{} canales').format(len(monitor.streamers)),
                 t('{} live', '{} en vivo').format(live),
                 t('{} recording', '{} grabando').format(rec_count)]
        free = tools.disk_free(monitor.cfg.recordings_dir)
        if free is not None:
            parts.append(t('{} free', '{} libres').format(tools.human_size(free)))
        if monitor.check_progress:
            done, total = monitor.check_progress
            parts.append(t('checking {}/{}', 'comprobando {}/{}').format(done, total))
        summary.set_text(' · '.join(parts))
        sync()
        events = (monitor.events[-1]['ts'], len(monitor.events)) if monitor.events else None
        if events != last_events[0]:
            last_events[0] = events
            activity.refresh()
        # the "live for 12 min" lines drift on their own; cheaper to retext than redraw
        for label, s in list(timeline_labels.values()):
            text = _timeline_text(s)
            if label.text != text:
                label.set_text(text)

    return tick


def _streamer_tile(monitor: Monitor, s: Streamer, sync, timeline_labels: dict) -> None:
    rec = monitor.recordings.get(s.key)
    label, color = status_label(s.status)

    async def do_record() -> None:
        # notify before awaiting: start_recording resolves stream URLs and is slow
        # enough for the grid to refresh and take this tile with it
        notify(t('Trying to record {}… it cancels itself if nothing is live.',
                 'Intentando grabar a {}… si no hay directo se cancela solo.')
               .format(s.username), type='info')
        s.cooldown_until = 0
        await monitor.start_recording(s)
        sync()

    async def do_stop() -> None:
        notify(t('Stopping… the file gets processed shortly. '
                 'Auto-record for this channel pauses for 10 minutes.',
                 'Deteniendo… el archivo se procesará en unos segundos. '
                 'La auto-grabación de este canal queda en pausa 10 minutos.'), type='info')
        await monitor.stop_recording(s)
        sync()

    async def do_check() -> None:
        status = await monitor.manual_check(s)
        notify(f'{s.username}: {status_label(status)[0]}', type='info')
        sync()

    async def do_remove() -> None:
        with ui.dialog() as confirm, ui.card():
            ui.label(t('Remove {} from the list?', '¿Quitar a {} de la lista?')
                     .format(s.username))
            ui.label(t('Saved recordings are not touched.',
                       'Las grabaciones ya guardadas no se tocan.')) \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(t('Cancel', 'Cancelar'),
                          on_click=lambda: confirm.submit(False)).props('flat')
                ui.button(t('Remove', 'Quitar'), color='red',
                          on_click=lambda: confirm.submit(True))
        if await confirm:
            await monitor.remove_streamer(s)
            notify(t('{} removed from the list', '{} eliminado de la lista')
                   .format(s.username), type='positive')
            sync()

    def open_folder() -> None:
        folder = monitor.cfg.recordings_path / config_mod.sanitize_segment(s.username)
        target = folder if folder.is_dir() else monitor.cfg.recordings_path
        with contextlib.suppress(OSError, AttributeError):
            os.startfile(str(target))   # type: ignore[attr-defined]

    def show_log() -> None:
        live = monitor.recordings.get(s.key)   # re-read: it may have ended since paint
        header = [t('Channel: {} ({})', 'Canal:   {} ({})').format(s.username, s.platform),
                  f'URL:     {s.url}',
                  t('Status:  {}', 'Estado:  {}').format(status_label(s.status)[0])]
        if live:
            header.append(t('Exit:    recording now ({})',
                            'Salida:  grabando ahora ({})').format(state_label(live.state)))
            header.append(t('Command: {}', 'Comando: {}').format(live.cmd))
            body_lines = list(live.log)
        else:
            if s.last_exit:
                header.append(t('Exit:    {}', 'Salida:  {}').format(s.last_exit))
            if s.last_reason or s.last_error:
                header.append(t('Reason:  {}', 'Motivo:  {}')
                              .format(s.last_reason or s.last_error))
            if s.last_cmd:
                header.append(t('Command: {}', 'Comando: {}').format(s.last_cmd))
            body_lines = s.last_log
        text = ('\n'.join(header) + '\n\n--- '
                + t('recorder output', 'salida del grabador') + ' ---\n'
                + ('\n'.join(body_lines)
                   or t('(the recorder printed nothing)', '(el grabador no imprimió nada)')))
        with ui.dialog() as d, ui.card().classes('w-[780px] max-w-full'):
            ui.label(t('Log · {}', 'Registro · {}').format(s.username)).classes('font-medium')
            with ui.element('div').classes('w-full max-h-96 overflow-auto bg-black rounded p-2'):
                ui.html(f'<pre style="white-space:pre-wrap;font-size:11px;margin:0;'
                        f'color:#e0e0e0">{html_mod.escape(text)}</pre>')
            ui.label(t('The exact command and why it ended.',
                       'Aquí está el comando exacto y el motivo por el que se cerró.')) \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(t('Open full .log', 'Abrir .log completo'), icon='description',
                          on_click=open_log_file).props('flat')
                ui.button(t('Copy', 'Copiar'), icon='content_copy',
                          on_click=lambda: copy_to_clipboard(
                              text, t('Log copied', 'Registro copiado'))) \
                    .props('unelevated')
                ui.button(t('Close', 'Cerrar'), on_click=d.close).props('flat')
        d.open()

    border = ' outline outline-1 outline-red-800/60' if rec else ''
    with ui.card().classes('w-full p-2.5 gap-1.5' + border).props('flat bordered'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            fg = '#111' if s.platform == 'kick' else 'white'
            ui.badge(s.platform).style(
                f'background-color: {PLATFORM_COLORS.get(s.platform, "#666")}; '
                f'color: {fg}').classes('flex-none')
            ui.link(s.username, s.url, new_tab=True) \
                .classes('text-sm font-medium no-underline hover:underline '
                         '!text-gray-100 truncate grow min-w-0')
            ui.badge(label).props(f'color={color}').classes('whitespace-nowrap flex-none')
        if rec:
            refresh_frame = live_preview(rec, extra_classes='rounded-lg')
        elif s.is_live and (still := thumbnail_url(s.platform, s.username)):
            # keyed by the broadcast start, so redrawing the tile reuses the cached still
            live_thumbnail(still, int(s.live_since or s.last_check), extra_classes='rounded-lg')
        timeline = ui.label(_timeline_text(s)).classes('text-xs text-gray-400 truncate w-full')
        timeline_labels[s.key] = (timeline, s)
        with ui.element('div').classes('w-full min-h-[1rem]'):
            if rec:
                live = ui.label().classes('text-xs font-mono text-red-400 truncate w-full')

                def update_live(rec=rec, live=live) -> None:
                    live.set_text(f'⏺ {tools.human_duration(rec.elapsed)} · '
                                  f'{tools.human_size(rec.size)} · {state_label(rec.state)}')
                    refresh_frame()

                update_live()
                ui.timer(1.0, update_live)
            else:
                sub = _subtitle(s)
                if sub:
                    ui.label(sub).classes('text-xs text-gray-500 truncate w-full') \
                        .tooltip(sub)
        with ui.row().classes('w-full items-center gap-0 flex-nowrap'):
            if rec:
                ui.button(icon='stop', on_click=do_stop).props('round flat dense color=red') \
                    .tooltip(t('Stop and save', 'Detener y guardar'))
            else:
                ui.button(icon='fiber_manual_record', on_click=do_record) \
                    .props('round flat dense color=red') \
                    .tooltip(t('Record now', 'Grabar ahora'))
                ui.button(icon='refresh', on_click=do_check).props('round flat dense') \
                    .tooltip(t('Check status', 'Comprobar estado'))
            with ui.button(icon='more_vert').props('round flat dense'):
                with ui.menu():
                    ui.menu_item(t('View log', 'Ver registro'), on_click=show_log)
                    ui.menu_item(t('Open its recordings folder',
                                   'Abrir su carpeta de grabaciones'), on_click=open_folder)
                    ui.menu_item(t('Copy channel URL', 'Copiar URL del canal'),
                                 on_click=lambda: copy_to_clipboard(
                                     s.url, t('URL copied', 'URL copiada')))
                    ui.menu_item(t('Remove from the list', 'Quitar de la lista'),
                                 on_click=do_remove)
            ui.space()
            ui.switch(value=s.auto_record,
                      on_change=lambda e: (setattr(s, 'auto_record', e.value),
                                           monitor.persist())) \
                .props('dense size=sm') \
                .tooltip(t('Auto-record when it goes live',
                           'Auto-grabar cuando esté en vivo'))


def _timeline_text(s: Streamer) -> str:
    """One line placing the channel in time: how long it has been on, or off."""
    now = time.time()
    if s.is_live:
        parts = [t('live for {}', 'en vivo desde hace {}').format(
            tools.human_span(now - s.live_since)) if s.live_since else t('live', 'en vivo')]
        if s.viewers:
            parts.append(t('{} viewers', '{} espectadores').format(s.viewers))
        return ' · '.join(parts)
    if s.last_online:
        return t('offline for {}', 'sin emitir desde hace {}').format(
            tools.human_span(now - s.last_online))
    if s.last_broadcast_start:
        # never caught it live ourselves; the site still tells when it last started
        return t('last broadcast started {}', 'última emisión empezó {}').format(
            tools.human_ago(s.last_broadcast_start))
    if not s.last_check:
        return t('not checked yet', 'sin comprobar aún')
    return t('never seen live', 'no se ha visto en vivo')


def _subtitle(s: Streamer) -> str:
    parts = []
    if s.last_result:
        parts.append(s.last_result)
    elif s.last_error:
        parts.append(f'⚠ {s.last_error[:120]}')
    remaining = s.cooldown_until - time.time()
    if remaining > 90:
        parts.append(t('auto paused {} min', 'auto en pausa {} min')
                     .format(int(remaining / 60)))
    return ' · '.join(parts)
