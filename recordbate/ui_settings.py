from __future__ import annotations

import asyncio
import html as html_mod
import socket
import sys

from nicegui import ui

from . import config as config_mod
from . import logbook, tools
from .monitor import Monitor
from .ui_common import copy_to_clipboard, open_log_file


def _lan_ip() -> str | None:
    """Our address on the local network. No packet is sent; connecting a UDP socket
    just makes the OS pick the interface it would route through."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def build(cfg: config_mod.Config, monitor: Monitor) -> None:
    with ui.column().classes('w-full max-w-3xl mx-auto gap-4'):
        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label('Grabación').classes('text-lg font-medium')
            dir_input = ui.input('Carpeta de grabaciones', value=cfg.recordings_dir) \
                .classes('w-full')
            with ui.row().classes('w-full gap-4'):
                quality = ui.select({'best': 'Máxima disponible', '1080p': 'Hasta 1080p',
                                     '720p': 'Hasta 720p', '480p': 'Hasta 480p'},
                                    value=cfg.quality, label='Calidad').classes('w-48')
                poll = ui.number('Comprobar cada (seg)', value=cfg.poll_seconds,
                                 min=15, max=3600, step=15).classes('w-44')
                maxc = ui.number('Grabaciones simultáneas', value=cfg.max_concurrent,
                                 min=1, max=20).classes('w-44')
            tmpl = ui.input('Plantilla de nombre de archivo', value=cfg.filename_template) \
                .classes('w-full')
            ui.label('Variables: {streamer} {platform} {date} {time} — usa / para subcarpetas') \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full items-center gap-3'):
                audio_off = ui.number('Ajuste de audio (ms)', value=cfg.audio_offset_ms,
                                      min=-2000, max=2000, step=50).classes('w-44')
                ui.label('Déjalo en 0: el desfase que se acumula durante la grabación se '
                         'corrige solo al convertir a MP4. Úsalo únicamente si el audio '
                         'sale movido desde el primer segundo (atrasado → negativo, '
                         'adelantado → positivo). Solo cam sites.') \
                    .classes('text-xs text-gray-500 grow')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label('Acceso').classes('text-lg font-medium')
            lan = ui.switch('Permitir acceso desde la red local (móvil, otro PC)',
                            value=cfg.lan_access)
            ip = _lan_ip()
            ui.label(f'Ahora mismo: http://127.0.0.1:{cfg.port}'
                     + (f'  ·  con acceso LAN: http://{ip}:{cfg.port}' if ip else '')) \
                .classes('text-xs text-gray-500')
            ui.label('Los cambios de carpeta y de acceso se aplican al reiniciar la app.') \
                .classes('text-xs text-amber-500')
            ui.label('Minimiza (o el botón de la cabecera) para enviar la app a la bandeja '
                     'del sistema: la ventana desaparece pero sigue grabando de fondo. '
                     'Para recuperarla, clic en el icono de la bandeja. La X (o «Salir» en '
                     'la bandeja) cierra la app y finaliza las grabaciones en curso.') \
                .classes('text-xs text-gray-500')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label('Herramientas').classes('text-lg font-medium')
            ffmpeg = tools.ffmpeg_path()
            rows = [
                ('ffmpeg', tools.tool_version(ffmpeg) if ffmpeg else None),
                ('streamlink', tools.package_version('streamlink')),
                ('yt-dlp', tools.package_version('yt-dlp')),
                ('NiceGUI', tools.package_version('nicegui')),
            ]
            for name, version in rows:
                with ui.row().classes('items-center gap-2'):
                    ui.icon('check_circle' if version else 'error',
                            color='green' if version else 'red')
                    ui.label(f'{name}: {version or "no encontrado"}').classes('text-sm')
            if not ffmpeg:
                ui.label('Instala ffmpeg con:  winget install Gyan.FFmpeg') \
                    .classes('text-xs text-red-500')

            update_btn = ui.button('Actualizar yt-dlp y streamlink',
                                   icon='system_update_alt').props('outline')
            ui.label('Estos sitios cambian a menudo; si una plataforma deja de grabar, '
                     'actualiza aquí y reinicia la app.').classes('text-xs text-gray-500')

            async def update_tools() -> None:
                update_btn.props('loading')
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'pip', 'install', '-U', 'yt-dlp', 'streamlink',
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=tools.CREATE_NO_WINDOW)
                rc = await proc.wait()
                update_btn.props(remove='loading')
                if rc == 0:
                    ui.notify('Actualizado. Reinicia la app para usar las versiones nuevas.',
                              type='positive')
                else:
                    ui.notify('La actualización falló; revisa la conexión.', type='negative')

            update_btn.on_click(update_tools)

        def show_log_tail() -> None:
            text = logbook.read_tail()
            with ui.dialog() as d, ui.card().classes('w-[840px] max-w-full'):
                ui.label('Registro completo (lo más reciente)').classes('font-medium')
                with ui.element('div').classes(
                        'w-full max-h-[70vh] overflow-auto bg-black rounded p-2'):
                    ui.html('<pre style="white-space:pre-wrap;font-size:11px;margin:0;'
                            f'color:#e0e0e0">{html_mod.escape(text)}</pre>')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Copiar todo', icon='content_copy',
                              on_click=lambda: copy_to_clipboard(text)).props('unelevated')
                    ui.button('Cerrar', on_click=d.close).props('flat')
            d.open()

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label('Registro').classes('text-lg font-medium')
            ui.label('Cada grabación anota por qué empezó y por qué se cerró: código de '
                     'salida, motivo y las últimas líneas del grabador.') \
                .classes('text-xs text-gray-500')
            ui.label(f'Archivo: {logbook.LOG_FILE}').classes('text-xs text-gray-500 break-all')
            with ui.row().classes('gap-2'):
                ui.button('Ver registro', icon='visibility',
                          on_click=show_log_tail).props('unelevated')
                ui.button('Abrir .log', icon='description',
                          on_click=open_log_file).props('outline')

        def save() -> None:
            new_dir = (dir_input.value or '').strip() or config_mod.Config().recordings_dir
            restart_needed = (new_dir != cfg.recordings_dir
                              or bool(lan.value) != cfg.lan_access)
            cfg.recordings_dir = new_dir
            cfg.quality = quality.value or 'best'
            cfg.poll_seconds = int(poll.value or 60)
            cfg.max_concurrent = int(maxc.value or 4)
            cfg.filename_template = (tmpl.value or '').strip() or config_mod.DEFAULT_TEMPLATE
            cfg.audio_offset_ms = int(audio_off.value or 0)
            cfg.lan_access = bool(lan.value)
            try:
                cfg.recordings_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                ui.notify(f'No se pudo crear la carpeta: {exc}', type='negative')
                return
            config_mod.save(cfg)
            ui.notify('Ajustes guardados'
                      + (' · reinicia la app para aplicar carpeta/acceso'
                         if restart_needed else ''), type='positive')

        ui.button('Guardar ajustes', icon='save', on_click=save).props('unelevated')
