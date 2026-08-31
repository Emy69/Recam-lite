from __future__ import annotations

import urllib.parse
from datetime import datetime

from nicegui import ui

from . import tools
from .library import Library, LibraryItem
from .ui_common import notify

_SORTS = {
    'recent': 'Más recientes',
    'oldest': 'Más antiguas',
    'largest': 'Más grandes',
    'longest': 'Más largas',
    'name': 'Nombre',
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
            if (t && t > 5 && t < v.duration - 10) v.currentTime = t;
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
    """Build the Biblioteca tab. Returns an async rescan callback."""
    state = {'streamer': 'Todos', 'search': '', 'sort': 'recent',
             'select': False, 'speed': 1.0}
    selected: set[str] = set()

    # one reusable player: closing it only pauses, because destroying elements from
    # their own 'hide' event breaks the client session
    with ui.dialog() as player_dialog, ui.card().tight().classes('w-[880px] max-w-full'):
        player_video = ui.video('', autoplay=True).props('id=rb-player').classes('w-full')
        with ui.row().classes('w-full items-center gap-1 p-2 flex-nowrap'):
            ui.button(icon='replay_10',
                      on_click=lambda: ui.run_javascript(
                          "const v=document.getElementById('rb-player');"
                          'if (v) v.currentTime -= 10;')) \
                .props('flat round dense').tooltip('Atrás 10 s')
            ui.button(icon='forward_10',
                      on_click=lambda: ui.run_javascript(
                          "const v=document.getElementById('rb-player');"
                          'if (v) v.currentTime += 10;')) \
                .props('flat round dense').tooltip('Adelante 10 s')

            def cycle_speed() -> None:
                speeds = [1.0, 1.25, 1.5, 1.75, 2.0]
                idx = speeds.index(state['speed']) if state['speed'] in speeds else 0
                state['speed'] = speeds[(idx + 1) % len(speeds)]
                speed_btn.set_text(f'{state["speed"]:g}×')
                ui.run_javascript("const v=document.getElementById('rb-player');"
                                  f"if (v) v.playbackRate = {state['speed']};")

            speed_btn = ui.button('1×', on_click=cycle_speed) \
                .props('flat dense no-caps').tooltip('Velocidad de reproducción')
            player_title = ui.label('').classes('text-sm truncate grow text-right')
            ui.button('Cerrar', on_click=player_dialog.close).props('flat dense')
    player_dialog.on('hide', lambda: player_video.run_method('pause'))

    def play_item(item: LibraryItem) -> None:
        state['speed'] = 1.0
        speed_btn.set_text('1×')
        # (re)arm the client-side handlers on every open; the guard inside makes
        # this idempotent, and doing it here guarantees the client is connected
        ui.run_javascript(_PLAYER_JS)
        ui.run_javascript("const v=document.getElementById('rb-player');"
                          'if (v) v.playbackRate = 1;')
        player_title.set_text(item.path.name)
        player_video.set_source('/media/' + urllib.parse.quote(item.rel))
        player_dialog.open()

    def visible_items() -> list[LibraryItem]:
        items = [i for i in library.items
                 if (state['streamer'] == 'Todos' or i.streamer == state['streamer'])
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
        with ui.row().classes('w-full items-center gap-2 bg-red-950/30 rounded-xl p-2'):
            ui.label(f'{len(chosen)} seleccionadas · {size}').classes('text-sm')
            ui.space()

            def select_all() -> None:
                selected.update(i.rel for i in visible_items())
                listing.refresh()
                actionbar.refresh()

            def select_none() -> None:
                selected.clear()
                listing.refresh()
                actionbar.refresh()

            ui.button('Todas', on_click=select_all).props('flat dense no-caps')
            ui.button('Ninguna', on_click=select_none).props('flat dense no-caps')

            async def delete_selected() -> None:
                if not chosen:
                    return
                with ui.dialog() as d, ui.card():
                    ui.label(f'¿Enviar {len(chosen)} grabaciones ({size}) a la papelera?')
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button('Cancelar', on_click=lambda: d.submit(False)).props('flat')
                        ui.button('A la papelera', color='red',
                                  on_click=lambda: d.submit(True))
                if not await d:
                    return
                failed = 0
                for item in chosen:
                    try:
                        library.delete(item)
                    except Exception:
                        failed += 1
                selected.clear()
                state['select'] = False
                notify(f'{len(chosen) - failed} enviadas a la papelera'
                       + (f' · {failed} fallaron' if failed else ''),
                       type='warning' if failed else 'positive')
                actionbar.refresh()
                await rescan()

            ui.button('A la papelera', icon='delete', color='red',
                      on_click=delete_selected).props('dense no-caps')

            def exit_select() -> None:
                state['select'] = False
                selected.clear()
                listing.refresh()
                actionbar.refresh()

            ui.button('Listo', on_click=exit_select).props('flat dense no-caps')

    @ui.refreshable
    def listing() -> None:
        active = list(monitor.recordings.values()) if monitor else []
        active = [r for r in active
                  if (state['streamer'] == 'Todos' or r.streamer.username == state['streamer'])
                  and state['search'].lower() in r.streamer.username.lower()]
        items = visible_items()
        parts = [f'{len(items)} vídeos']
        if active:
            parts.append(f'{len(active)} grabando')
        total_dur = sum(i.duration or 0 for i in items)
        if total_dur:
            parts.append(tools.human_duration(total_dur))
        parts.append(tools.human_size(sum(i.size for i in items)))
        count_label.set_text(' · '.join(parts))
        if not active and not items:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('video_library', size='xl').classes('text-gray-600')
                ui.label('Aún no hay grabaciones (o el filtro no encuentra nada)') \
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
            slot(item.streamer or 'Sin carpeta')['items'].append(item)

        with ui.column().classes('w-full gap-2 mt-1'):
            for name in order:
                group = groups[name]
                vids = group['items']
                head_parts = [name]
                if group['recs']:
                    head_parts[0] = f'⏺ {name}'
                if vids:
                    head_parts.append(f'{len(vids)} vídeo{"s" if len(vids) != 1 else ""}')
                    head_parts.append(tools.human_size(sum(i.size for i in vids)))
                else:
                    head_parts.append('grabando ahora')
                with ui.expansion(' · '.join(head_parts), icon='person', value=True) \
                        .classes('w-full rounded-xl border border-white/5 bg-[#131a22]') \
                        .props('dense header-class="text-sm font-medium"'):
                    with ui.element('div').classes('w-full grid gap-2 pb-2') \
                            .style('grid-template-columns: '
                                   'repeat(auto-fill, minmax(200px, 1fr))'):
                        for rec in group['recs']:
                            _recording_tile(rec)
                        for item in vids:
                            _video_tile(library, item, rescan, play_item,
                                        state['select'], item.rel in selected,
                                        toggle_item)

    async def rescan() -> None:
        await library.scan()
        options = ['Todos'] + sorted({i.streamer for i in library.items if i.streamer})
        if state['streamer'] not in options:
            state['streamer'] = 'Todos'
        filter_select.set_options(options, value=state['streamer'])
        selected.intersection_update({i.rel for i in library.items})
        listing.refresh()
        actionbar.refresh()

    with ui.column().classes('w-full max-w-5xl mx-auto gap-3'):
        with ui.row().classes('w-full items-center gap-2'):
            ui.button(icon='refresh', on_click=rescan).props('flat round').tooltip('Actualizar')
            filter_select = ui.select(['Todos'], value='Todos', label='Streamer') \
                .props('outlined dense options-dense').classes('w-44')
            sort_select = ui.select(_SORTS, value='recent', label='Ordenar') \
                .props('outlined dense options-dense').classes('w-40')
            search = ui.input(placeholder='Buscar…').props('outlined dense clearable') \
                .classes('w-48')

            def toggle_select() -> None:
                state['select'] = not state['select']
                if not state['select']:
                    selected.clear()
                listing.refresh()
                actionbar.refresh()

            ui.button(icon='checklist', on_click=toggle_select).props('flat round') \
                .tooltip('Seleccionar varias (para borrar en lote)')
            ui.space()
            count_label = ui.label().classes('text-sm text-gray-500')
            ui.button('Abrir carpeta', icon='folder_open',
                      on_click=lambda: library.open_root()).props('flat')

        def on_filter() -> None:
            state['streamer'] = filter_select.value or 'Todos'
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


def _recording_tile(rec) -> None:
    """A capture in flight, with a live preview frame that refreshes as it runs."""
    with ui.card().tight().classes('w-full outline outline-1 outline-red-800/60') \
            .props('flat bordered'):
        with ui.element('div').classes('relative w-full h-28 bg-red-950/40 '
                                       'flex items-center justify-center overflow-hidden'):
            image = ui.image('').classes('w-full h-full object-cover')
            image.set_visibility(False)
            placeholder = ui.icon('fiber_manual_record', size='md').classes('text-red-500')
            ui.label('● REC').classes('absolute top-1 left-1 text-[10px] font-medium '
                                      'bg-red-600/90 px-1.5 py-0.5 rounded z-10')
        with ui.column().classes('p-2 pt-1.5 w-full gap-0'):
            live = ui.label().classes('text-xs text-red-400 font-mono truncate w-full')
            seen = {'mtime': 0}

            def update(rec=rec) -> None:
                live.set_text(f'● {tools.human_duration(rec.elapsed)} · '
                              f'{tools.human_size(rec.size)}')
                thumb = getattr(rec, 'live_thumb', None)
                if thumb and thumb[1] != seen['mtime']:
                    seen['mtime'] = thumb[1]
                    # the mtime doubles as a cache buster for each new frame
                    image.set_source('/media/' + urllib.parse.quote(thumb[0])
                                     + f'?v={thumb[1]}')
                    image.set_visibility(True)
                    placeholder.set_visibility(False)

            update()
            ui.timer(2.0, update)
            ui.label('grabando · aparecerá al terminar').classes('text-xs text-gray-500')


def _video_tile(library: Library, item: LibraryItem, rescan, play_item,
                select_mode: bool, is_selected: bool, toggle_item) -> None:
    exact_date = datetime.fromtimestamp(item.mtime).strftime('%d/%m/%Y %H:%M')

    def rename() -> None:
        with ui.dialog() as d, ui.card().classes('w-96'):
            ui.label('Renombrar').classes('font-medium')
            name_input = ui.input('Nuevo nombre', value=item.path.stem).classes('w-full')
            error = ui.label().classes('text-xs text-red-500')

            async def confirm() -> None:
                try:
                    library.rename(item, name_input.value or '')
                except (ValueError, OSError) as exc:
                    error.set_text(str(exc))
                    return
                d.close()
                notify('Renombrado', type='positive')
                await rescan()

            name_input.on('keydown.enter', confirm)
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancelar', on_click=d.close).props('flat')
                ui.button('Guardar', on_click=confirm)
        d.open()

    async def delete() -> None:
        with ui.dialog() as d, ui.card():
            ui.label(f'¿Enviar "{item.path.name}" a la papelera?')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancelar', on_click=lambda: d.submit(False)).props('flat')
                ui.button('A la papelera', color='red', on_click=lambda: d.submit(True))
        if await d:
            try:
                library.delete(item)
            except Exception as exc:
                notify(f'No se pudo borrar: {exc}', type='negative')
                return
            notify('Enviado a la papelera', type='positive')
            await rescan()

    async def convert() -> None:
        notify('Convirtiendo a MP4…', type='info')
        result = await library.convert_ts(item)
        if result:
            notify(f'Convertido: {result.name}', type='positive')
        else:
            notify('No se pudo convertir (¿está ffmpeg disponible?)', type='negative')
        await rescan()

    async def primary() -> None:
        # the whole row is a target: select in select mode, play (or convert) otherwise
        if select_mode:
            toggle_item(item, not is_selected)   # rows re-render on toggle
        elif item.is_ts:
            await convert()
        else:
            play_item(item)

    outline = ' outline outline-2 outline-red-600' if is_selected else ''
    with ui.card().tight().classes('w-full relative' + outline).props('flat bordered'):
        if select_mode:
            ui.checkbox(value=is_selected,
                        on_change=lambda e: toggle_item(item, bool(e.value))) \
                .props('dense keep-color color=red') \
                .classes('absolute top-1 left-1 z-10 bg-black/60 rounded')
        thumb = ui.element('div').classes(
            'relative w-full h-28 cursor-pointer overflow-hidden bg-black')
        with thumb:
            if item.thumb:
                ui.image(str(item.thumb)).classes('w-full h-full object-cover')
            else:
                with ui.element('div').classes(
                        'w-full h-full flex items-center justify-center bg-gray-900'):
                    ui.icon('smart_display', size='md').classes('text-gray-700')
            if item.duration:
                ui.label(tools.human_duration(item.duration)).classes(
                    'absolute bottom-1 right-1 text-[11px] font-mono '
                    'bg-black/75 px-1.5 py-0.5 rounded')
            if item.is_ts:
                ui.label('SIN PROCESAR').classes(
                    'absolute top-1 left-1 text-[10px] font-medium '
                    'bg-amber-500/90 text-black px-1.5 py-0.5 rounded')
        thumb.on('click', primary)
        with ui.column().classes('p-2 pt-1.5 w-full gap-0'):
            title = ui.label(item.path.stem) \
                .classes('text-xs font-medium truncate w-full cursor-pointer') \
                .tooltip(item.rel)
            title.on('click', primary)
            with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                ui.label(f'{tools.human_ago(item.mtime)} · '
                         f'{tools.human_size(item.size)}') \
                    .classes('text-[11px] text-gray-500 truncate grow min-w-0') \
                    .tooltip(exact_date)
                if item.is_ts:
                    ui.button(icon='auto_fix_high', on_click=convert) \
                        .props('flat round dense size=sm') \
                        .tooltip('Convertir a MP4 (grabación sin procesar)')
                with ui.button(icon='more_vert').props('flat round dense size=sm'):
                    with ui.menu():
                        ui.menu_item('Abrir con el reproductor del sistema',
                                     on_click=lambda: library.open_external(item))
                        ui.menu_item('Mostrar en la carpeta',
                                     on_click=lambda: library.open_in_explorer(item))
                        ui.menu_item('Renombrar', on_click=rename)
                        ui.menu_item('Enviar a la papelera', on_click=delete)
