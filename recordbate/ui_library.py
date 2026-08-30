from __future__ import annotations

import urllib.parse
from datetime import datetime

from nicegui import ui

from . import tools
from .library import Library, LibraryItem
from .ui_common import notify


def build(library: Library, monitor=None):
    """Build the Biblioteca tab. Returns an async rescan callback."""
    state = {'streamer': 'Todos', 'search': ''}

    # one reusable player: closing it only pauses, because destroying elements from
    # their own 'hide' event breaks the client session
    with ui.dialog() as player_dialog, ui.card().tight().classes('w-[880px] max-w-full'):
        player_video = ui.video('', autoplay=True).classes('w-full')
        with ui.row().classes('w-full justify-between items-center p-2'):
            player_title = ui.label('').classes('text-sm truncate')
            ui.button('Cerrar', on_click=player_dialog.close).props('flat dense')
    player_dialog.on('hide', lambda: player_video.run_method('pause'))

    def play_item(item: LibraryItem) -> None:
        player_title.set_text(item.path.name)
        player_video.set_source('/media/' + urllib.parse.quote(item.rel))
        player_dialog.open()

    @ui.refreshable
    def grid() -> None:
        active = list(monitor.recordings.values()) if monitor else []
        active = [r for r in active
                  if (state['streamer'] == 'Todos' or r.streamer.username == state['streamer'])
                  and state['search'].lower() in r.streamer.username.lower()]
        items = [i for i in library.items
                 if (state['streamer'] == 'Todos' or i.streamer == state['streamer'])
                 and state['search'].lower() in i.path.stem.lower()]
        parts = [f'{len(items)} vídeos']
        if active:
            parts.append(f'{len(active)} grabando')
        parts.append(tools.human_size(sum(i.size for i in items)))
        count_label.set_text(' · '.join(parts))
        if not active and not items:
            with ui.card().classes('w-full items-center p-10').props('flat bordered'):
                ui.icon('video_library', size='xl').classes('text-gray-600')
                ui.label('Aún no hay grabaciones (o el filtro no encuentra nada)') \
                    .classes('text-gray-500')
            return
        with ui.row().classes('w-full gap-3'):
            for rec in active:
                _recording_card(rec)
            for item in items:
                _video_card(library, item, rescan, play_item)

    async def rescan() -> None:
        await library.scan()
        options = ['Todos'] + sorted({i.streamer for i in library.items if i.streamer})
        if state['streamer'] not in options:
            state['streamer'] = 'Todos'
        filter_select.set_options(options, value=state['streamer'])
        grid.refresh()

    with ui.column().classes('w-full max-w-6xl mx-auto gap-3'):
        with ui.row().classes('w-full items-center gap-2'):
            ui.button(icon='refresh', on_click=rescan).props('flat round').tooltip('Actualizar')
            filter_select = ui.select(['Todos'], value='Todos', label='Streamer') \
                .props('outlined dense options-dense').classes('w-48')
            search = ui.input(placeholder='Buscar…').props('outlined dense clearable') \
                .classes('w-56')
            ui.space()
            count_label = ui.label().classes('text-sm text-gray-500')
            ui.button('Abrir carpeta', icon='folder_open',
                      on_click=lambda: library.open_root()).props('flat')

        def on_filter() -> None:
            state['streamer'] = filter_select.value or 'Todos'
            state['search'] = search.value or ''
            grid.refresh()

        filter_select.on_value_change(on_filter)
        search.on_value_change(on_filter)

        grid()

    return rescan


def _recording_card(rec) -> None:
    """Placeholder card for a capture in flight; there is no finished file yet."""
    with ui.card().tight().classes('w-64').props('flat bordered'):
        with ui.element('div').classes(
                'w-full h-36 bg-red-950/40 flex items-center justify-center'):
            ui.icon('fiber_manual_record', size='lg').classes('text-red-500')
        with ui.column().classes('p-3 pt-2 w-full gap-1'):
            ui.label(rec.streamer.username).classes('text-sm font-medium truncate w-full')
            live = ui.label().classes('text-xs text-red-400 font-mono')

            def update(rec=rec, live=live) -> None:
                live.set_text(f'● grabando · {tools.human_duration(rec.elapsed)} · '
                              f'{tools.human_size(rec.size)}')

            update()
            ui.timer(1.0, update)
            ui.label('aparecerá al terminar').classes('text-xs text-gray-500')


def _video_card(library: Library, item: LibraryItem, rescan, play_item) -> None:
    date_str = datetime.fromtimestamp(item.mtime).strftime('%d/%m/%Y %H:%M')

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

    with ui.card().tight().classes('w-64').props('flat bordered'):
        if item.thumb:
            ui.image(str(item.thumb)).classes('w-full h-36 object-cover bg-black')
        else:
            with ui.element('div').classes(
                    'w-full h-36 bg-gray-900 flex items-center justify-center'):
                ui.icon('smart_display', size='lg').classes('text-gray-700')
        with ui.column().classes('p-3 pt-2 w-full gap-1'):
            ui.label(item.path.stem).classes('text-sm font-medium truncate w-full') \
                .tooltip(item.rel)
            meta = ' · '.join(filter(None, [
                item.streamer, date_str,
                tools.human_duration(item.duration), tools.human_size(item.size)]))
            ui.label(meta).classes('text-xs text-gray-500')
            with ui.row().classes('w-full items-center gap-1'):
                if item.is_ts:
                    ui.button('Convertir a MP4', icon='auto_fix_high', on_click=convert) \
                        .props('flat dense no-caps').tooltip('Grabación sin procesar (.ts)')
                else:
                    ui.button(icon='play_arrow', on_click=lambda: play_item(item)) \
                        .props('flat round dense').tooltip('Reproducir aquí')
                ui.space()
                with ui.button(icon='more_vert').props('flat round dense'):
                    with ui.menu():
                        ui.menu_item('Abrir con el reproductor del sistema',
                                     on_click=lambda: library.open_external(item))
                        ui.menu_item('Mostrar en la carpeta',
                                     on_click=lambda: library.open_in_explorer(item))
                        ui.menu_item('Renombrar', on_click=rename)
                        ui.menu_item('Enviar a la papelera', on_click=delete)
