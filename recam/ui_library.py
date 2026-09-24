from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from nicegui import ui

from . import tools
from .i18n import t
from .library import Library, LibraryItem
from .ui_common import live_preview, notify


def _sorts() -> dict[str, str]:
    return {
        'recent': t('Newest', 'Más recientes'),
        'oldest': t('Oldest', 'Más antiguas'),
        'largest': t('Largest', 'Más grandes'),
        'longest': t('Longest', 'Más largas'),
        'name': t('Name', 'Nombre'),
    }


_SORT_KEYS = {
    'recent': lambda i: -i.mtime,
    'oldest': lambda i: i.mtime,
    'largest': lambda i: -i.size,
    'longest': lambda i: -(i.duration or 0),
    'name': lambda i: i.path.stem.lower(),
}

# per-viewer conveniences stored in the browser: resume position, volume. All of
# it lives client-side; failures (blocked storage) are silently ignored.
_PLAYER_JS = '''
(function () {
    const v = document.getElementById('rb-player');
    if (!v || v.dataset.rbInit) return;
    v.dataset.rbInit = '1';
    try {
        const vol = localStorage.getItem('rb-vol');
        if (vol !== null) v.volume = parseFloat(vol);
    } catch (e) {}
    v.addEventListener('volumechange', () => {
        try { localStorage.setItem('rb-vol', v.volume); } catch (e) {}
    });
    v.addEventListener('loadedmetadata', () => {
        try {
            const t = parseFloat(localStorage.getItem('rb-pos:' + v.currentSrc));
            if (t && t > 5 && t < v.duration - 10) {
                v.currentTime = t;
                // a short note over the video, gone after 3 s
                const b = document.getElementById('rb-resume');
                if (b) {
                    const s = Math.floor(t), m = Math.floor(s / 60), h = Math.floor(m / 60);
                    const mm = h ? h + ':' + String(m % 60).padStart(2, '0') : String(m);
                    b.textContent = __RESUMED__ + ' · ' + mm + ':' + String(s % 60).padStart(2, '0');
                    b.classList.remove('hidden');
                    setTimeout(() => b.classList.add('hidden'), 3000);
                }
            }
        } catch (e) {}
    });
    v.addEventListener('timeupdate', () => {
        if (v._rbLast && Date.now() - v._rbLast < 3000) return;
        v._rbLast = Date.now();
        try {
            if (v.duration && v.currentTime > v.duration - 15)
                localStorage.removeItem('rb-pos:' + v.currentSrc);
            else
                localStorage.setItem('rb-pos:' + v.currentSrc, v.currentTime);
        } catch (e) {}
    });
})();
'''


def build(library: Library, monitor=None):
    """Build the library tab. Returns an async rescan callback."""
    state = {'streamer': 'ALL', 'search': '', 'sort': 'recent',
             'select': False, 'speed': 1.0}
    selected: set[str] = set()
    all_label = t('All', 'Todos')

    # one reusable player: closing it only pauses, because destroying elements from
    # their own 'hide' event breaks the client session. Native controls are off;
    # the card draws its own row and polls the <video> while the dialog is open.
    playing: dict = {'item': None, 'duration': 0.0, 'paused': False}

    def player_js(code: str) -> None:
        ui.run_javascript("const v = document.getElementById('rb-player'); if (v) {" + code + '}')

    with ui.dialog() as player_dialog, \
            ui.card().tight().classes('w-[880px] max-w-full rounded-xl overflow-hidden'):
        with ui.row().classes('w-full items-center gap-2.5 pl-3.5 pr-2 py-2 flex-nowrap min-w-0'):
            player_title = ui.label('').classes('font-semibold truncate grow min-w-0')
            player_meta = ui.label('').classes('font-mono text-[11px] text-gray-500 whitespace-nowrap')
            ui.button(icon='close', on_click=player_dialog.close) \
                .props('flat round dense color=grey-5')
        with ui.element('div').classes('relative w-full bg-black'):
            player_video = ui.video('', autoplay=True, controls=False) \
                .props('id=rb-player').classes('w-full block')
            ui.label('').props('id=rb-resume') \
                .classes('absolute bottom-3 left-3.5 text-[11px] text-gray-300 bg-black/60 '
                         'px-2 py-0.5 rounded hidden')
        with ui.column().classes('w-full gap-1.5 px-3.5 pt-2.5 pb-3'):
            with ui.row().classes('w-full items-center gap-2.5 flex-nowrap'):
                time_now = ui.label('0:00').classes('font-mono text-[11px] text-gray-400 w-10')
                seek = ui.slider(min=0, max=1, step=0.5, value=0) \
                    .props('dense color=white track-color=grey-8 thumb-size=12px').classes('grow')
                time_total = ui.label('0:00') \
                    .classes('font-mono text-[11px] text-gray-500 w-10 text-right')
            with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                ui.button(icon='replay_10', on_click=lambda: player_js('v.currentTime -= 10')) \
                    .props('flat round color=grey-3').tooltip(t('Back 10 s', 'Atrás 10 s'))
                play_btn = ui.button(icon='pause',
                                     on_click=lambda: player_js('v.paused ? v.play() : v.pause()')) \
                    .props('round unelevated color=white text-color=dark') \
                    .classes('w-[38px] h-[38px]')
                ui.button(icon='forward_10', on_click=lambda: player_js('v.currentTime += 10')) \
                    .props('flat round color=grey-3').tooltip(t('Forward 10 s', 'Adelante 10 s'))
                ui.element('div').classes('w-px h-5 bg-white/10 mx-1.5')
                ui.icon('volume_up', size='xs').classes('text-gray-400')
                volume = ui.slider(min=0, max=1, step=0.05, value=1) \
                    .props('dense color=grey-4 track-color=grey-8 thumb-size=10px') \
                    .classes('w-[72px]')
                ui.space()
                speed = ui.toggle({1: '1×', 1.25: '1.25×', 1.5: '1.5×', 2: '2×'}, value=1,
                                  on_change=lambda e: player_js(f'v.playbackRate = {e.value}')) \
                    .props('dense no-caps unelevated toggle-color=grey-8 text-color=grey-5 '
                           'toggle-text-color=white') \
                    .classes('bg-white/5 rounded-md font-mono')
                ui.button(icon='open_in_new',
                          on_click=lambda: playing['item'] and library.open_external(playing['item'])) \
                    .props('flat round dense color=grey-5') \
                    .tooltip(t('Open with the system player', 'Abrir con el reproductor del sistema'))
                ui.button(icon='fullscreen',
                          on_click=lambda: player_js('if (v.requestFullscreen) v.requestFullscreen()')) \
                    .props('flat round dense color=grey-5') \
                    .tooltip(t('Full screen', 'Pantalla completa'))
        # user-driven only: Quasar emits 'change' on release, never for values set in code
        seek.on('change', lambda e: player_js(f'v.currentTime = {float(e.args)}'))
        volume.on('change', lambda e: player_js(f'v.volume = {float(e.args)}'))

    async def poll_player() -> None:
        try:
            state = await ui.run_javascript(
                "(() => { const v = document.getElementById('rb-player');"
                ' return v ? {t: v.currentTime || 0, d: v.duration || 0, p: v.paused, '
                'vol: v.volume} : null; })()', timeout=1.0)
        except Exception:
            return
        if not state:
            return
        if state['d'] and state['d'] != playing['duration']:
            playing['duration'] = state['d']
            seek.props(f'max={state["d"]}')
            time_total.set_text(tools.human_duration(state['d']))
        seek.value = state['t']
        time_now.set_text(tools.human_duration(state['t']))
        if state['p'] != playing['paused']:
            playing['paused'] = state['p']
            play_btn.props('icon=play_arrow' if state['p'] else 'icon=pause')
        if abs((volume.value or 0) - state['vol']) > 0.02:
            volume.value = state['vol']

    player_timer = ui.timer(0.5, poll_player, active=False)

    def on_player_hide() -> None:
        player_video.run_method('pause')
        player_timer.active = False

    player_dialog.on('hide', on_player_hide)

    def play_item(item: LibraryItem) -> None:
        playing.update(item=item, duration=0.0, paused=False)
        speed.value = 1
        seek.value = 0
        play_btn.props('icon=pause')
        # (re)arm the client-side handlers on every open; the guard inside makes
        # this idempotent, and doing it here guarantees the client is connected
        ui.run_javascript(_PLAYER_JS.replace('__RESUMED__', json.dumps(
            t('Resumed where you left off', 'Retomado donde lo dejaste'))))
        player_js('v.playbackRate = 1')
        player_title.set_text(item.path.name)
        player_meta.set_text(' · '.join(part for part in (
            tools.human_duration(item.duration) if item.duration else '',
            tools.human_size(item.size)) if part))
        player_video.set_source('/media/' + urllib.parse.quote(item.rel))
        player_dialog.open()
        player_timer.active = True

    def visible_items() -> list[LibraryItem]:
        items = [i for i in library.items
                 if (state['streamer'] == 'ALL' or i.streamer == state['streamer'])
                 and state['search'].lower() in i.path.stem.lower()]
        items.sort(key=_SORT_KEYS[state['sort']])
        return items

    def toggle_item(item: LibraryItem, value: bool) -> None:
        (selected.add if value else selected.discard)(item.rel)
        listing.refresh()
        actionbar.refresh()

    @ui.refreshable
    def actionbar() -> None:
        if not state['select']:
            return
        items = {i.rel: i for i in visible_items()}
        chosen = [items[r] for r in selected if r in items]
        size = tools.human_size(sum(i.size for i in chosen))
        # a quiet bar: only the destructive action is solid red
        with ui.row().classes('w-full items-center gap-1 h-10 rounded-lg bg-[#131a22] '
                              'border border-rose-600/45 pl-3.5 pr-1.5 flex-nowrap'):
            ui.icon('check_box', size='xs').classes('text-rose-400')
            ui.label(t('{} selected', '{} seleccionadas').format(len(chosen))) \
                .classes('text-sm font-semibold')
            ui.label(f'· {size}').classes('text-sm text-gray-500')
            ui.space()

            def select_all() -> None:
                selected.update(i.rel for i in visible_items())
                listing.refresh()
                actionbar.refresh()

            def select_none() -> None:
                selected.clear()
                listing.refresh()
                actionbar.refresh()

            ui.button(t('All', 'Todas'), on_click=select_all).props('flat dense no-caps')
            ui.button(t('None', 'Ninguna'), on_click=select_none).props('flat dense no-caps')

            async def delete_selected() -> None:
                if not chosen:
                    return
                with ui.dialog() as d, ui.card():
                    ui.label(t('Send {} recordings ({}) to the recycle bin?',
                               '¿Enviar {} grabaciones ({}) a la papelera?')
                             .format(len(chosen), size))
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button(t('Cancel', 'Cancelar'),
                                  on_click=lambda: d.submit(False)).props('flat')
                        ui.button(t('To recycle bin', 'A la papelera'), color='red',
                                  on_click=lambda: d.submit(True))
                if not await d:
                    return
                failed = 0
                for item in chosen:
                    try:
                        library.delete(item)
                    except Exception:
                        failed += 1
                set_select(False)
                msg = t('{} sent to the recycle bin', '{} enviadas a la papelera') \
                    .format(len(chosen) - failed)
                if failed:
                    msg += t(' · {} failed', ' · {} fallaron').format(failed)
                notify(msg, type='warning' if failed else 'positive')
                await rescan()

            ui.button(t('To recycle bin', 'A la papelera'), icon='delete',
                      on_click=delete_selected).props('unelevated dense no-caps color=primary')
            ui.button(t('Done', 'Listo'), on_click=lambda: set_select(False)) \
                .props('flat dense no-caps')

    @ui.refreshable
    def listing() -> None:
        active = list(monitor.recordings.values()) if monitor else []
        active = [r for r in active
                  if (state['streamer'] == 'ALL' or r.streamer.username == state['streamer'])
                  and state['search'].lower() in r.streamer.username.lower()]
        items = visible_items()
        parts = [t('{} videos', '{} vídeos').format(len(items))]
        if active:
            parts.append(t('{} recording', '{} grabando').format(len(active)))
        total_dur = sum(i.duration or 0 for i in items)
        if total_dur:
            parts.append(tools.human_span(total_dur))
        parts.append(tools.human_size(sum(i.size for i in items)))
        count_label.set_text(' · '.join(parts))
        if not active and not items:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('video_library', size='xl').classes('text-gray-600')
                ui.label(t('No recordings yet (or the filter finds nothing)',
                           'Aún no hay grabaciones (o el filtro no encuentra nada)')) \
                    .classes('text-gray-500')
            return

        # one collapsible section per profile; recordings in flight lead their own
        # profile, and profiles with something recording float to the top
        order: list[str] = []
        groups: dict[str, dict] = {}

        def slot(name: str) -> dict:
            if name not in groups:
                groups[name] = {'recs': [], 'items': []}
                order.append(name)
            return groups[name]

        for rec in active:
            slot(rec.streamer.username)['recs'].append(rec)
        for item in items:
            slot(item.streamer or t('No folder', 'Sin carpeta'))['items'].append(item)

        # profiles tile up in a grid too; a single filtered profile gets the full width
        columns = '1fr' if len(order) == 1 else 'repeat(auto-fill, minmax(340px, 1fr))'
        with ui.element('div').classes('w-full grid gap-2.5 mt-1 items-start') \
                .style(f'grid-template-columns: {columns}'):
            for name in order:
                group = groups[name]
                vids = group['items']
                recording = bool(group['recs'])
                meta = []
                if vids:
                    meta.append(t('{} videos', '{} vídeos').format(len(vids))
                                if len(vids) != 1 else t('1 video', '1 vídeo'))
                    meta.append(tools.human_size(sum(i.size for i in vids)))
                if recording:
                    meta.append(t('recording now', 'grabando ahora'))
                elif vids:
                    meta.append(t('latest {}', 'última {}')
                                .format(tools.human_ago(max(i.mtime for i in vids))))
                border = 'border-rose-600/45' if recording else 'border-white/5'
                with ui.expansion(value=True) \
                        .props('dense expand-icon-class="text-grey-5"') \
                        .classes(f'w-full rounded-[10px] border {border} bg-[#131a22] '
                                 'overflow-hidden') as profile:
                    with profile.add_slot('header'):
                        _profile_header(name, meta, recording)
                    with ui.element('div').classes('w-full grid gap-2 px-2.5 pb-2.5') \
                            .style('grid-template-columns: '
                                   'repeat(auto-fill, minmax(150px, 1fr))'):
                        for rec in group['recs']:
                            _recording_tile(rec)
                        for item in vids:
                            _video_tile(library, item, rescan, play_item,
                                        state['select'], item.rel in selected,
                                        toggle_item)

    async def rescan() -> None:
        await library.scan()
        streamers = sorted({i.streamer for i in library.items if i.streamer})
        if state['streamer'] != 'ALL' and state['streamer'] not in streamers:
            state['streamer'] = 'ALL'
        filter_select.set_options(
            {'ALL': all_label, **{name: name for name in streamers}},
            value=state['streamer'])
        selected.intersection_update({i.rel for i in library.items})
        listing.refresh()
        actionbar.refresh()

    with ui.column().classes('w-full max-w-5xl mx-auto gap-3'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            ui.button(icon='refresh', on_click=rescan).props('flat round dense color=grey-5') \
                .tooltip(t('Refresh', 'Actualizar'))
            # field labels sit inline as prefixes: same meaning, a shorter bar
            filter_select = ui.select({'ALL': all_label}, value='ALL') \
                .props('outlined dense options-dense').classes('w-44')
            with filter_select.add_slot('prepend'):
                ui.label(t('Streamer', 'Streamer')).classes('text-[11px] text-gray-500')
            sort_select = ui.select(_sorts(), value='recent') \
                .props('outlined dense options-dense').classes('w-40')
            with sort_select.add_slot('prepend'):
                ui.label(t('Sort', 'Orden')).classes('text-[11px] text-gray-500')
            # the search box is the one field that yields width when the bar gets tight
            search = ui.input(placeholder=t('Search…', 'Buscar…')) \
                .props('outlined dense clearable').classes('w-56 shrink min-w-[120px]')
            with search.add_slot('prepend'):
                ui.icon('search', size='xs').classes('text-gray-500')

            def set_select(on: bool) -> None:
                state['select'] = on
                if not on:
                    selected.clear()
                if on:
                    select_btn.classes(add='bg-white/10 text-white')
                else:
                    select_btn.classes(remove='bg-white/10 text-white')
                listing.refresh()
                actionbar.refresh()

            select_btn = ui.button(t('Select', 'Seleccionar'), icon='checklist',
                                   on_click=lambda: set_select(not state['select'])) \
                .props('flat dense no-caps no-wrap').classes('shrink-0') \
                .tooltip(t('Select several (for bulk delete)',
                           'Seleccionar varias (para borrar en lote)'))
            ui.space()
            count_label = ui.label().classes('text-xs text-gray-500 truncate min-w-0')
            ui.button(t('Open folder', 'Abrir carpeta'), icon='folder_open',
                      on_click=lambda: library.open_root()) \
                .props('flat dense no-caps no-wrap').classes('bg-white/5 shrink-0')

        def on_filter() -> None:
            state['streamer'] = filter_select.value or 'ALL'
            state['search'] = search.value or ''
            state['sort'] = sort_select.value or 'recent'
            listing.refresh()
            actionbar.refresh()

        filter_select.on_value_change(on_filter)
        sort_select.on_value_change(on_filter)
        search.on_value_change(on_filter)

        actionbar()
        listing()

    return rescan


def _profile_header(name: str, meta: list[str], recording: bool) -> None:
    """Expansion header: initial avatar, name (plus REC when capturing), one line of figures."""
    avatar = 'bg-rose-600/20 text-rose-300' if recording else 'bg-white/10 text-gray-300'
    with ui.row().classes('w-full items-center gap-2.5 h-12 flex-nowrap min-w-0'):
        ui.label(name[:1].upper()).classes(
            'w-7 h-7 rounded-full text-xs font-bold flex items-center justify-center '
            f'flex-none {avatar}')
        with ui.column().classes('grow min-w-0 gap-0'):
            with ui.row().classes('w-full items-center gap-2 flex-nowrap min-w-0'):
                ui.label(name).classes('text-[13px] font-semibold truncate')
                if recording:
                    ui.badge('REC').props('color=primary').classes('text-[10px] font-bold')
            ui.label(' · '.join(meta)).classes('text-[11px] text-gray-500 truncate w-full')


def _recording_tile(rec) -> None:
    """A capture in flight, with a live preview frame that refreshes as it runs."""
    with ui.card().tight().classes('w-full rounded-lg bg-[#0f151c] border-rose-600/60') \
            .props('flat bordered'):
        refresh_frame = live_preview(rec)
        with ui.column().classes('p-1.5 pl-2 w-full gap-0'):
            live = ui.label().classes('text-xs font-mono text-rose-400 truncate w-full')

            def update(rec=rec) -> None:
                live.set_text(f'● {tools.human_duration(rec.elapsed)} · '
                              f'{tools.human_size(rec.size)}')
                refresh_frame()

            update()
            ui.timer(2.0, update)
            ui.label(t('appears when it ends', 'aparecerá al terminar')) \
                .classes('text-[11px] text-gray-500 truncate w-full')


def _video_tile(library: Library, item: LibraryItem, rescan, play_item,
                select_mode: bool, is_selected: bool, toggle_item) -> None:
    exact_date = datetime.fromtimestamp(item.mtime).strftime('%d/%m/%Y %H:%M')

    def rename() -> None:
        with ui.dialog() as d, ui.card().classes('w-96'):
            ui.label(t('Rename', 'Renombrar')).classes('font-medium')
            name_input = ui.input(t('New name', 'Nuevo nombre'),
                                  value=item.path.stem).classes('w-full')
            error = ui.label().classes('text-xs text-red-500')

            async def confirm() -> None:
                try:
                    library.rename(item, name_input.value or '')
                except (ValueError, OSError) as exc:
                    error.set_text(str(exc))
                    return
                d.close()
                notify(t('Renamed', 'Renombrado'), type='positive')
                await rescan()

            name_input.on('keydown.enter', confirm)
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(t('Cancel', 'Cancelar'), on_click=d.close).props('flat')
                ui.button(t('Save', 'Guardar'), on_click=confirm)
        d.open()

    async def delete() -> None:
        with ui.dialog() as d, ui.card():
            ui.label(t('Send "{}" to the recycle bin?',
                       '¿Enviar "{}" a la papelera?').format(item.path.name))
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(t('Cancel', 'Cancelar'),
                          on_click=lambda: d.submit(False)).props('flat')
                ui.button(t('To recycle bin', 'A la papelera'), color='red',
                          on_click=lambda: d.submit(True))
        if await d:
            try:
                library.delete(item)
            except Exception as exc:
                notify(t('Could not delete: {}', 'No se pudo borrar: {}').format(exc),
                       type='negative')
                return
            notify(t('Sent to the recycle bin', 'Enviado a la papelera'), type='positive')
            await rescan()

    async def convert() -> None:
        notify(t('Converting to MP4…', 'Convirtiendo a MP4…'), type='info')
        result = await library.convert_ts(item)
        if result:
            notify(t('Converted: {}', 'Convertido: {}').format(result.name),
                   type='positive')
        else:
            notify(t('Could not convert (is ffmpeg available?)',
                     'No se pudo convertir (¿está ffmpeg disponible?)'), type='negative')
        await rescan()

    async def primary() -> None:
        # the whole tile is a target: select in select mode, play (or convert) otherwise
        if select_mode:
            toggle_item(item, not is_selected)   # tiles re-render on toggle
        elif item.is_ts:
            await convert()
        else:
            play_item(item)

    ring = ' border-rose-500 ring-1 ring-rose-500/60' if is_selected else ''
    with ui.card().tight().classes('w-full relative rounded-lg bg-[#0f151c]' + ring) \
            .props('flat bordered') as card:
        if select_mode:
            # the whole card is the target in this mode; the box only shows the state
            ui.checkbox(value=is_selected) \
                .props('dense keep-color color=primary size=sm') \
                .classes('absolute top-1 left-1 z-10 rounded bg-black/60 pointer-events-none')
            card.classes(add='cursor-pointer')
            card.on('click', primary)
        thumb = ui.element('div').classes(
            'relative w-full aspect-video cursor-pointer overflow-hidden bg-black')
        with thumb:
            if item.thumb:
                ui.image(str(item.thumb)).classes('w-full h-full object-cover')
            else:
                with ui.element('div').classes(
                        'w-full h-full flex items-center justify-center bg-gray-900'):
                    ui.icon('smart_display', size='md').classes('text-gray-700')
            if item.duration:
                ui.label(tools.human_duration(item.duration)).classes(
                    'absolute bottom-1 right-1 text-[10px] font-mono '
                    'bg-black/75 px-1 rounded')
            if item.is_ts:
                # top right: the top-left corner belongs to the selection box
                ui.label(t('RAW', 'SIN PROCESAR')).classes(
                    'absolute top-1 right-1 text-[9px] font-bold '
                    'bg-amber-500 text-black px-1 rounded')
        if not select_mode:
            thumb.on('click', primary)
        with ui.column().classes('p-1.5 pl-2 w-full gap-0'):
            title = ui.label(item.path.stem) \
                .classes('text-xs font-semibold truncate w-full cursor-pointer') \
                .tooltip(item.rel)
            if not select_mode:
                title.on('click', primary)
            with ui.row().classes('w-full items-center gap-1 flex-nowrap h-[18px]'):
                ui.label(f'{tools.human_ago(item.mtime)} · '
                         f'{tools.human_size(item.size)}') \
                    .classes('text-[11px] text-gray-500 truncate grow min-w-0') \
                    .tooltip(exact_date)
                if select_mode:
                    return   # less noise while picking: no per-video actions
                if item.is_ts:
                    ui.button(icon='auto_fix_high', on_click=convert) \
                        .props('flat round dense size=sm color=amber') \
                        .tooltip(t('Convert to MP4 (raw capture)',
                                   'Convertir a MP4 (grabación sin procesar)'))
                with ui.button(icon='more_horiz').props('flat round dense size=sm color=grey-5'):
                    with ui.menu():
                        ui.menu_item(t('Open with the system player',
                                       'Abrir con el reproductor del sistema'),
                                     on_click=lambda: library.open_external(item))
                        ui.menu_item(t('Show in folder', 'Mostrar en la carpeta'),
                                     on_click=lambda: library.open_in_explorer(item))
                        ui.menu_item(t('Rename', 'Renombrar'), on_click=rename)
                        ui.menu_item(t('Send to recycle bin', 'Enviar a la papelera'),
                                     on_click=delete)
