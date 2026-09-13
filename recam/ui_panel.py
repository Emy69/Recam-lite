from __future__ import annotations

import contextlib
import html as html_mod
import os
import time

from nicegui import ui

from . import config as config_mod
from . import tools, ui_tutorial
from .i18n import t
from .models import Status, Streamer, state_label, status_label
from .monitor import Monitor
from .platforms import clear_rate_limit, rate_limit_remaining, thumbnail_url
from .ui_common import (copy_to_clipboard, ffmpeg_downloader, live_preview, live_thumbnail,
                        notify, open_log_file, read_clipboard_text)

_EVENT_STYLE = {
    'start': ('fiber_manual_record', 'text-rose-600'),
    'saved': ('check_circle', 'text-green-400'),
    'fail': ('warning', 'text-amber-500'),
}

# short tag for the tile header, in the site's own colour
_PLATFORM_TAG = {
    'chaturbate': ('CB', '#F47321'),
    'stripchat': ('SC', '#E6224B'),
    'twitch': ('TW', '#9146FF'),
    'kick': ('KK', '#53FC18'),
}

# a responsive grid: as many tiles per row as the width allows
_GRID_STYLE = 'grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))'
_FEED_LENGTH = 30


def build(monitor: Monitor):
    """Build the Panel tab. Returns the callback the page timer should tick."""

    # the "live for …" labels; the page timer re-texts them without redrawing anything
    timeline_labels: dict[str, tuple[ui.label, Streamer]] = {}
    # one container per channel, so a change in one tile redraws only that tile
    tiles: dict[str, dict] = {}
    last_layout: list = [None]

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

    def section(title: str, dot: str, items: list[Streamer], empty: str) -> None:
        with ui.row().classes('w-full items-center gap-2 mt-1'):
            ui.element('span').classes(f'w-2 h-2 rounded-full {dot}')
            ui.label(title).classes('text-xs font-semibold uppercase tracking-[.08em] '
                                    'text-gray-400')
            ui.badge(str(len(items))).props('outline color=grey-7')
        if not items:
            ui.label(empty).classes('text-xs text-gray-500 pl-4')
            return
        with ui.element('div').classes('w-full grid gap-2.5 items-start').style(_GRID_STYLE):
            for s in items:
                tiles[s.key] = {'box': ui.element('div').classes('w-full'), 'sig': None}
                render_tile(s)

    @ui.refreshable
    def streamer_table() -> None:
        timeline_labels.clear()
        tiles.clear()
        last_layout[0] = layout_signature()
        if not monitor.streamers:
            with ui.card().classes('w-full p-4 gap-2.5 rounded-[10px]').props('flat bordered'):
                with ui.row().classes('items-center gap-2.5'):
                    ui.icon('videocam_off', size='sm').classes('text-gray-500')
                    ui.label(t('No channels yet', 'Aún no hay canales')).classes('font-semibold')
                ui.label(t('Paste a Chaturbate channel URL above and press Add. It gets '
                           'checked on the next cycle.',
                           'Pega arriba la URL de un canal de Chaturbate y pulsa Añadir. Se '
                           'comprueba en el próximo ciclo.')).classes('text-xs text-gray-400')
                ui.button(t('Open the tutorial', 'Abrir el tutorial'), icon='school',
                          on_click=ui_tutorial.show).props('outline dense no-caps color=grey-5')
            return
        live, rest = split_streamers()
        section(t('Live now', 'En vivo ahora'), 'bg-green-500', live,
                t('Nobody is live right now', 'Nadie está en vivo ahora mismo'))
        section(t('Not broadcasting', 'Sin emitir'), 'bg-slate-500', rest,
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

    with ui.column().classes('w-full max-w-5xl mx-auto gap-4'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            url_input = ui.input(
                placeholder=t('Paste a Chaturbate channel URL…',
                              'Pega la URL de un canal de Chaturbate…'),
            ).props('outlined dense clearable').classes('grow')
            with url_input.add_slot('prepend'):
                ui.icon('link', size='xs').classes('text-gray-500')

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
                ui.notify(t('{} added. Turn on Auto-record on its card to capture it '
                            'whenever it goes live.',
                            '{} añadido. Activa Auto-grabar en su tarjeta para capturarlo '
                            'cuando esté en vivo.')
                          .format(s.username), type='positive')
                sync()

            url_input.on('keydown.enter', add)

            async def paste(and_add: bool) -> None:
                text = await read_clipboard_text()
                if not text:
                    notify(t('Nothing to paste: copy a channel URL first',
                             'Nada que pegar: copia antes la URL de un canal'), type='warning')
                    return
                url_input.value = text.splitlines()[0].strip()
                if and_add:
                    add()
                else:
                    url_input.run_method('focus')

            # the native window has no right-click menu of its own
            with url_input, ui.context_menu():
                ui.menu_item(t('Paste', 'Pegar'), on_click=lambda: paste(False))
                ui.menu_item(t('Paste and add', 'Pegar y añadir'), on_click=lambda: paste(True))
            # neutral on purpose: red is reserved for recording
            ui.button(t('Add', 'Añadir'), icon='add', on_click=add) \
                .props('unelevated no-caps no-wrap color=blue-grey-9').classes('shrink-0')

        with ui.row().classes('w-full items-center gap-4 flex-nowrap'):
            ui.switch(t('Automatic monitoring', 'Vigilancia automática')) \
                .props('color=positive dense').bind_value(monitor, 'enabled') \
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
                .props('flat dense no-caps no-wrap').classes('bg-white/5 text-gray-300 shrink-0') \
                .tooltip(t('Check every channel right now', 'Comprueba todos los canales ya'))
            ui.space()
            with ui.row().classes('items-center gap-3.5 text-xs text-gray-500 flex-nowrap'):
                channels_lbl = ui.label().classes('text-gray-300 whitespace-nowrap')
                with ui.row().classes('items-center gap-1.5 flex-nowrap text-green-400'):
                    ui.element('span').classes('w-1.5 h-1.5 rounded-full bg-green-500')
                    live_lbl = ui.label().classes('whitespace-nowrap')
                with ui.row().classes('items-center gap-1.5 flex-nowrap text-rose-400'):
                    ui.element('span').classes('w-1.5 h-1.5 rounded-full bg-rose-600')
                    rec_lbl = ui.label().classes('whitespace-nowrap')
                free_lbl = ui.label().classes('whitespace-nowrap')
                with ui.row().classes('items-center gap-2 flex-nowrap') as check_row:
                    check_lbl = ui.label().classes('whitespace-nowrap')
                    check_bar = ui.linear_progress(0, size='3px', show_value=False,
                                                   color='grey-5') \
                        .props('track-color=grey-9').classes('w-14')
                check_row.set_visibility(False)

        # two situations worth a line above the grid: the site holding us off,
        # and the recorder missing its tools
        with ui.row().classes('w-full items-center gap-2.5 rounded-lg bg-rose-600/10 border '
                              'border-rose-600/35 px-3 py-2 flex-nowrap') as net_banner:
            ui.icon('wifi_off', size='xs').classes('text-rose-400 flex-none')
            with ui.column().classes('grow min-w-0 gap-0'):
                ui.label(t('Network error', 'Error de red')).classes('text-sm font-semibold')
                net_text = ui.label().classes('text-xs text-gray-400')

            async def retry_now() -> None:
                clear_rate_limit('chaturbate')
                await check_all()

            ui.button(t('Retry now', 'Reintentar ahora'), icon='refresh', on_click=retry_now) \
                .props('flat dense no-caps no-wrap').classes('bg-white/5 shrink-0')
        net_banner.set_visibility(False)

        def tools_ready() -> None:
            tools_banner.set_visibility(False)
            streamer_table.refresh()   # the record buttons come back enabled

        with ui.row().classes('w-full items-center gap-2.5 rounded-lg bg-amber-500/10 border '
                              'border-amber-500/35 px-3 py-2 flex-nowrap') as tools_banner:
            ui.icon('build', size='xs').classes('text-amber-500 flex-none')
            with ui.column().classes('grow min-w-0 gap-0'):
                ui.label(t('ffmpeg is missing', 'Falta ffmpeg')).classes('text-sm font-semibold')
                ui.label(t('Recordings need ffmpeg and ffprobe. Download them here (about '
                           '110 MB, no admin rights) or run: winget install Gyan.FFmpeg',
                           'Para grabar hacen falta ffmpeg y ffprobe. Descárgalos aquí (unos '
                           '110 MB, sin permisos de administrador) o ejecuta: winget install '
                           'Gyan.FFmpeg')).classes('text-xs text-gray-400')
            with ui.column().classes('gap-1 shrink-0 items-end'):
                ffmpeg_downloader(tools_ready)
        tools_banner.set_visibility(bool(tools.missing_tools()))

        streamer_table()

    def retext(label, text: str) -> None:
        if label.text != text:
            label.set_text(text)

    def tick() -> None:
        rec_count = len(monitor.recordings)
        live = sum(1 for s in monitor.streamers if s.is_live)
        retext(channels_lbl, t('{} channels', '{} canales').format(len(monitor.streamers)))
        retext(live_lbl, t('{} live', '{} en vivo').format(live))
        retext(rec_lbl, t('{} recording', '{} grabando').format(rec_count))
        free = tools.disk_free(monitor.cfg.recordings_dir)
        retext(free_lbl, t('{} free', '{} libres').format(tools.human_size(free))
               if free is not None else '')
        if monitor.check_progress:
            done, total = monitor.check_progress
            retext(check_lbl, t('checking {}/{}', 'comprobando {}/{}').format(done, total))
            check_bar.set_value(done / total if total else 0)
            check_row.set_visibility(True)
        else:
            check_row.set_visibility(False)
        hold = rate_limit_remaining('chaturbate')
        if hold > 0:
            retext(net_text, t('Could not reach chaturbate.com (429 — too many requests). '
                               'Backing off; retrying in {} min.',
                               'No se pudo conectar con chaturbate.com (429: demasiadas '
                               'peticiones). Esperando; reintento en {} min.')
                   .format(int(hold // 60) + 1))
        if net_banner.visible != (hold > 0):
            net_banner.set_visibility(hold > 0)
        sync()
        # the "live for 12 min" lines drift on their own; cheaper to retext than redraw
        for label, s in list(timeline_labels.values()):
            retext(label, _timeline_text(s))

    return tick


class ActivityFeed:
    """The activity drawer on the right, opened from the header's history button.

    Events newer than the last time the drawer was read are grouped as "since
    you left" and marked; closing the drawer (or the button) marks them read.
    """

    def __init__(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self.read_ts = time.time()
        self._seen: tuple | None = None
        # no 'overlay=false' here: NiceGUI would send the string "false", which Vue
        # reads as true and the drawer would cover the page instead of pushing it
        self.drawer = ui.right_drawer(value=False, bordered=True) \
            .props('width=300').classes('bg-[#0e141b] p-0')

        @ui.refreshable
        def feed() -> None:
            self._render()

        self._feed = feed
        with self.drawer:
            with ui.column().classes('w-full h-full gap-0 flex-nowrap'):
                with ui.row().classes('w-full items-center gap-1 h-11 pl-4 pr-2 '
                                      'border-b border-white/5 flex-nowrap'):
                    ui.label(t('Activity', 'Actividad')) \
                        .classes('text-xs font-semibold uppercase tracking-[.08em] '
                                 'text-gray-400 grow')
                    ui.button(t('Mark read', 'Marcar leído'), on_click=self.mark_read) \
                        .props('flat dense no-caps size=sm color=grey-6')
                    ui.button(icon='close', on_click=self.drawer.hide) \
                        .props('flat dense round size=sm color=grey-5')
                with ui.element('div').classes('w-full grow overflow-auto py-2'):
                    self._feed()
                with ui.row().classes('w-full items-center gap-1.5 px-4 py-2.5 border-t '
                                      'border-white/5 text-xs text-gray-500 cursor-pointer '
                                      'hover:text-gray-300 flex-nowrap') as foot:
                    ui.icon('description', size='xs')
                    ui.label(t('Open the full log', 'Abrir registro completo'))
                foot.on('click', open_log_file)
        self.drawer.on_value_change(self._on_toggle)

    # -- state ------------------------------------------------------------- #
    @property
    def is_open(self) -> bool:
        return bool(self.drawer.value)

    def unread(self) -> int:
        return sum(1 for e in self.monitor.events if e['ts'] > self.read_ts)

    def toggle(self) -> None:
        self.drawer.toggle()

    def mark_read(self) -> None:
        self.read_ts = time.time()
        self._feed.refresh()

    def tick(self) -> None:
        """Called by the page timer: redraw the list when something new arrived."""
        events = self.monitor.events
        seen = (events[-1]['ts'], len(events)) if events else None
        if seen != self._seen:
            self._seen = seen
            self._feed.refresh()

    def _on_toggle(self, e) -> None:
        if e.value:
            self._feed.refresh()   # fresh "ago" texts on opening
        else:
            self.mark_read()

    # -- rendering ------------------------------------------------------------ #
    def _render(self) -> None:
        events = list(self.monitor.events)[-_FEED_LENGTH:][::-1]
        if not events:
            ui.label(t('Nothing has happened yet', 'Todavía no ha pasado nada')) \
                .classes('text-xs text-gray-500 px-4 py-2')
            return
        new = [e for e in events if e['ts'] > self.read_ts]
        old = [e for e in events if e['ts'] <= self.read_ts]
        if new:
            ui.label(t('Since you left · {}', 'Desde que te fuiste · {}').format(len(new))) \
                .classes('text-[11px] text-gray-500 px-4 pt-1.5 pb-1')
            for ev in new:
                self._item(ev, unread=True)
        if old:
            ui.label(t('Earlier', 'Antes') if new else t('Recent', 'Reciente')) \
                .classes('text-[11px] text-gray-500 px-4 pt-3 pb-1')
            for ev in old:
                self._item(ev, unread=False)

    @staticmethod
    def _item(ev: dict, unread: bool) -> None:
        icon, color = _EVENT_STYLE.get(ev['kind'], ('info', 'text-gray-500'))
        marker = 'border-amber-500 bg-white/[.02]' if unread else 'border-transparent'
        with ui.element('div').classes(f'w-full flex gap-2.5 px-4 py-2 border-l-2 {marker}'):
            ui.icon(icon, size='xs').classes(f'{color} mt-0.5 flex-none')
            with ui.column().classes('grow min-w-0 gap-0.5'):
                with ui.row().classes('w-full items-baseline gap-2 flex-nowrap'):
                    ui.label(ev.get('who') or t('system', 'sistema')) \
                        .classes('text-xs font-semibold truncate grow min-w-0'
                                 + ('' if unread else ' text-gray-300'))
                    ui.label(tools.human_ago(ev['ts'])) \
                        .classes('font-mono text-[10px] text-gray-500 whitespace-nowrap')
                ui.label(ev['text']).classes('text-xs leading-snug line-clamp-2 '
                                             + ('text-gray-400' if unread else 'text-gray-500'))


def build_activity(monitor: Monitor) -> ActivityFeed:
    """Create the activity drawer for this page (call it at page level, not in a tab)."""
    return ActivityFeed(monitor)


def _status_chip(s: Streamer, recording: bool) -> None:
    base = 'text-[11px] font-semibold tracking-wide px-2 py-0.5 rounded whitespace-nowrap flex-none '
    if recording:
        ui.label('● ' + status_label(Status.RECORDING)[0]).classes(base + 'bg-primary text-white')
    elif s.status is Status.ONLINE:
        ui.label('● ' + status_label(s.status)[0]) \
            .classes(base + 'border border-green-500/45 bg-green-500/10 text-green-400')
    elif s.status is Status.OFFLINE:
        ui.label(status_label(s.status)[0]).classes(base + 'bg-white/5 text-gray-400')
    else:
        ui.label(status_label(s.status)[0]) \
            .classes(base + 'border border-dashed border-white/20 text-gray-500')


def _result_line(s: Streamer) -> None:
    """Last outcome, always rendered so tiles with and without one line up."""
    text = _subtitle(s)
    with ui.row().classes('w-full items-center gap-1 flex-nowrap min-h-4'):
        if s.last_error and not s.last_result:
            ui.icon('warning', size='14px').classes('text-amber-500 flex-none')
        elif s.last_result:
            ui.icon('check_circle', size='14px').classes('text-gray-500 flex-none')
        ui.label(text).classes('text-xs text-gray-500 truncate min-w-0').tooltip(text)


def _streamer_tile(monitor: Monitor, s: Streamer, sync, timeline_labels: dict) -> None:
    rec = monitor.recordings.get(s.key)

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
        live_rec = monitor.recordings.get(s.key)
        so_far = (f'{tools.human_duration(live_rec.elapsed)} · {tools.human_size(live_rec.size)}'
                  if live_rec else '')
        with ui.dialog() as confirm, ui.card().classes('w-[380px] max-w-full'):
            ui.label(t('Stop recording {}?', '¿Detener la grabación de {}?').format(s.username))                 .classes('font-medium')
            if so_far:
                ui.label(so_far).classes('font-mono text-xs text-gray-400')
            ui.label(t('The capture so far is saved. Auto-record for this channel '
                       'pauses for 10 minutes.',
                       'Lo grabado hasta ahora se guarda. La auto-grabación de este '
                       'canal queda en pausa 10 minutos.')).classes('text-xs text-gray-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(t('Cancel', 'Cancelar'),
                          on_click=lambda: confirm.submit(False)).props('flat no-caps')
                ui.button(t('Stop and save', 'Detener y guardar'), icon='stop',
                          on_click=lambda: confirm.submit(True))                     .props('unelevated no-caps color=primary')
        if not await confirm:
            return
        if s.key not in monitor.recordings:   # it ended on its own meanwhile
            sync()
            return
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

    border = ' border-rose-600/60' if rec else ''
    with ui.card().classes('w-full p-3 gap-2 rounded-[10px]' + border).props('flat bordered'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap min-w-0'):
            tag, colour = _PLATFORM_TAG.get(s.platform, (s.platform[:2].upper(), '#9ca3af'))
            ui.label(tag).classes('font-mono text-[10px] font-semibold tracking-wide rounded '
                                  'px-1 leading-4 flex-none') \
                .style(f'color: {colour}; border: 1px solid {colour}66').tooltip(s.platform)
            ui.link(s.username, s.url, new_tab=True) \
                .classes('text-sm font-semibold no-underline hover:underline '
                         '!text-gray-100 truncate grow min-w-0')
            _status_chip(s, rec is not None)
        if rec:
            refresh_frame = live_preview(rec)
        elif s.is_live and (still := thumbnail_url(s.platform, s.username)):
            # keyed by the broadcast start, so redrawing the tile reuses the cached still
            live_thumbnail(still, int(s.live_since or s.last_check))
        timeline = ui.label(_timeline_text(s)).classes('text-xs text-gray-400 truncate w-full')
        timeline_labels[s.key] = (timeline, s)
        if rec:
            live = ui.label().classes('text-xs font-mono text-rose-400 truncate w-full min-h-4')

            def update_live(rec=rec, live=live) -> None:
                live.set_text(f'{tools.human_duration(rec.elapsed)} · '
                              f'{tools.human_size(rec.size)} · {state_label(rec.state)}')
                refresh_frame()

            update_live()
            ui.timer(1.0, update_live)
        else:
            _result_line(s)
        with ui.row().classes('w-full items-center gap-2 flex-nowrap mt-0.5'):
            with ui.button_group().props('flat').classes('bg-white/5 rounded-md p-0.5'):
                if rec:
                    ui.button(icon='stop', on_click=do_stop) \
                        .props('flat dense size=sm color=primary').classes('bg-rose-600/15') \
                        .tooltip(t('Stop and save', 'Detener y guardar'))
                else:
                    # red only on hover: the grid should not shout red on every tile
                    record = ui.button(icon='fiber_manual_record', on_click=do_record) \
                        .props('flat dense size=sm color=grey-5').classes('hover:text-rose-500')
                    if tools.missing_tools():
                        record.props('disable').tooltip(
                            t('Install ffmpeg first (Settings › Tools)',
                              'Instala ffmpeg primero (Ajustes › Herramientas)'))
                    else:
                        record.tooltip(t('Record now', 'Grabar ahora'))
                ui.button(icon='refresh', on_click=do_check) \
                    .props('flat dense size=sm color=grey-5') \
                    .tooltip(t('Check status', 'Comprobar estado'))
                with ui.button(icon='more_horiz').props('flat dense size=sm color=grey-5'):
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
            ui.label(t('Auto-record', 'Auto-grabar')).classes('text-xs text-gray-500')
            ui.switch(value=s.auto_record,
                      on_change=lambda e: (setattr(s, 'auto_record', e.value),
                                           monitor.persist())) \
                .props('dense size=sm color=grey-3 keep-color') \
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
        parts.append(s.last_error[:120])
    remaining = s.cooldown_until - time.time()
    if remaining > 90:
        parts.append(t('auto paused {} min', 'auto en pausa {} min')
                     .format(int(remaining / 60)))
    return ' · '.join(parts)
