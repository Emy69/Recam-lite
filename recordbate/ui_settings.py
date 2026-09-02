from __future__ import annotations

import asyncio
import html as html_mod
import os
import socket
import sys
import webbrowser

from nicegui import ui

from . import __version__, AUTHOR, LINKS, i18n, logbook, tools
from . import config as config_mod
from .i18n import t
from .monitor import Monitor
from .ui_common import copy_to_clipboard, open_log_file

# icon + Quasar colour per link, so the About card reads at a glance
_LINK_STYLE = {
    'Patreon': ('favorite', 'red'),
    'GitHub': ('code', 'grey-4'),
    'X': ('tag', 'grey-4'),
    'Buy Me a Coffee': ('coffee', 'amber'),
    'Discord': ('forum', 'indigo-4'),
}


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
            ui.label(t('Recording', 'Grabación')).classes('text-lg font-medium')
            dir_input = ui.input(t('Recordings folder', 'Carpeta de grabaciones'),
                                 value=cfg.recordings_dir).classes('w-full')
            with ui.row().classes('w-full gap-4'):
                quality = ui.select({'best': t('Best available', 'Máxima disponible'),
                                     '1080p': t('Up to 1080p', 'Hasta 1080p'),
                                     '720p': t('Up to 720p', 'Hasta 720p'),
                                     '480p': t('Up to 480p', 'Hasta 480p')},
                                    value=cfg.quality,
                                    label=t('Quality', 'Calidad')).classes('w-48')
                poll = ui.number(t('Check every (sec)', 'Comprobar cada (seg)'),
                                 value=cfg.poll_seconds,
                                 min=15, max=3600, step=15).classes('w-44')
                maxc = ui.number(t('Concurrent recordings', 'Grabaciones simultáneas'),
                                 value=cfg.max_concurrent, min=1, max=20).classes('w-44')
            tmpl = ui.input(t('File name template', 'Plantilla de nombre de archivo'),
                            value=cfg.filename_template).classes('w-full')
            ui.label(t('Variables: {streamer} {platform} {date} {time} — use / for subfolders',
                       'Variables: {streamer} {platform} {date} {time} — usa / para subcarpetas')) \
                .classes('text-xs text-gray-500')
            with ui.row().classes('w-full items-center gap-3'):
                audio_off = ui.number(t('Audio nudge (ms)', 'Ajuste de audio (ms)'),
                                      value=cfg.audio_offset_ms,
                                      min=-2000, max=2000, step=50).classes('w-44')
                ui.label(t('Leave it at 0: recordings come out in sync on their own. '
                           'If one sounds shifted, adjust this (audio ahead → positive, '
                           'behind → negative); it applies when converting to MP4, '
                           'without re-encoding.',
                           'Déjalo en 0: las grabaciones salen sincronizadas por sí solas. '
                           'Si alguna se oye movida, ajusta esto (adelantado → positivo, '
                           'atrasado → negativo); se aplica al convertir a MP4, sin '
                           'recodificar.')).classes('text-xs text-gray-500 grow')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label(t('Language', 'Idioma')).classes('text-lg font-medium')
            lang = ui.select({'en': 'English', 'es': 'Español'},
                             value=getattr(cfg, 'language', 'en'),
                             label=t('Interface language', 'Idioma de la interfaz')) \
                .props('outlined dense options-dense').classes('w-48')
            ui.label(t('Saved with the settings below; reload the page (or restart) '
                       'to apply everywhere.',
                       'Se guarda con los ajustes de abajo; recarga la página (o '
                       'reinicia) para aplicarlo en todo.')).classes('text-xs text-gray-500')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label(t('Access', 'Acceso')).classes('text-lg font-medium')
            lan = ui.switch(t('Allow access from the local network (phone, another PC)',
                              'Permitir acceso desde la red local (móvil, otro PC)'),
                            value=cfg.lan_access)
            ip = _lan_ip()
            ui.label(t('Right now: http://127.0.0.1:{}', 'Ahora mismo: http://127.0.0.1:{}')
                     .format(cfg.port)
                     + (t('  ·  with LAN access: http://{}:{}',
                          '  ·  con acceso LAN: http://{}:{}').format(ip, cfg.port)
                        if ip else '')).classes('text-xs text-gray-500')
            ui.label(t('Folder and access changes apply after restarting the app.',
                       'Los cambios de carpeta y de acceso se aplican al reiniciar la app.')) \
                .classes('text-xs text-amber-500')
            ui.label(t('Minimizing minimizes normally. The window X asks whether to hide '
                       'the app to the tray (it keeps recording in the background) or '
                       'quit for real.',
                       'Minimizar minimiza normal. La X pregunta si esconder la app a la '
                       'bandeja (sigue grabando de fondo) o cerrarla del todo.')) \
                .classes('text-xs text-gray-500')

        if os.name == 'nt':
            with ui.card().classes('w-full gap-2').props('flat bordered'):
                ui.label(t('Start with Windows', 'Inicio con Windows')) \
                    .classes('text-lg font-medium')
                auto_start = ui.switch(t('Launch RecordBate at sign-in',
                                         'Arrancar RecordBate al iniciar sesión'),
                                       value=tools.startup_enabled())

                def on_autostart(e) -> None:
                    if tools.set_startup(bool(e.value)):
                        ui.notify(t('Will start with Windows (no console window)',
                                    'Arrancará con Windows (sin ventana de consola)')
                                  if e.value else
                                  t('No longer starts with Windows',
                                    'Ya no arranca con Windows'), type='positive')
                    else:
                        ui.notify(t('Could not change the automatic start',
                                    'No se pudo cambiar el inicio automático'),
                                  type='negative')
                        auto_start.value = tools.startup_enabled()

                auto_start.on_value_change(on_autostart)
                ui.label(t('Creates (or removes) a shortcut in your user Startup folder. '
                           'At sign-in the app opens and starts watching the channels '
                           'with auto-record on; minimize it to the tray and it keeps '
                           'recording in the background.',
                           'Crea (o quita) un acceso directo en la carpeta Inicio de tu '
                           'usuario. Al iniciar sesión la app se abre y empieza a vigilar '
                           'los canales con auto-grabar activado; minimízala a la bandeja '
                           'y seguirá grabando de fondo.')).classes('text-xs text-gray-500')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label(t('Tools', 'Herramientas')).classes('text-lg font-medium')
            ffmpeg = tools.ffmpeg_path()
            rows = [
                ('ffmpeg', tools.tool_version(ffmpeg) if ffmpeg else None),
                ('streamlink', tools.package_version('streamlink')),
                ('yt-dlp', tools.package_version('yt-dlp')),
                ('NiceGUI', tools.package_version('nicegui')),
            ]
            for name, version in rows:
                if name == 'streamlink' and version is None:
                    continue   # not shipped in the Chaturbate-only frozen build
                with ui.row().classes('items-center gap-2'):
                    ui.icon('check_circle' if version else 'error',
                            color='green' if version else 'red')
                    ui.label(f'{name}: {version or t("not found", "no encontrado")}') \
                        .classes('text-sm')
            if not ffmpeg:
                ui.label(t('Install ffmpeg with:  winget install Gyan.FFmpeg',
                           'Instala ffmpeg con:  winget install Gyan.FFmpeg')) \
                    .classes('text-xs text-red-500')

            if tools.IS_FROZEN:
                ui.label(t('Tool updates ship with new app builds.',
                           'Las actualizaciones de herramientas llegan con nuevas '
                           'versiones de la app.')).classes('text-xs text-gray-500')
            else:
                update_btn = ui.button(t('Update yt-dlp and streamlink',
                                         'Actualizar yt-dlp y streamlink'),
                                       icon='system_update_alt').props('outline')
                ui.label(t('These sites change often; if a platform stops recording, '
                           'update here and restart the app.',
                           'Estos sitios cambian a menudo; si una plataforma deja de '
                           'grabar, actualiza aquí y reinicia la app.')) \
                    .classes('text-xs text-gray-500')

                async def update_tools() -> None:
                    update_btn.props('loading')
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'pip', 'install', '-U',
                        'yt-dlp', 'streamlink',
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=tools.CREATE_NO_WINDOW)
                    rc = await proc.wait()
                    update_btn.props(remove='loading')
                    if rc == 0:
                        ui.notify(t('Updated. Restart the app to use the new versions.',
                                    'Actualizado. Reinicia la app para usar las '
                                    'versiones nuevas.'), type='positive')
                    else:
                        ui.notify(t('The update failed; check the connection.',
                                    'La actualización falló; revisa la conexión.'),
                                  type='negative')

                update_btn.on_click(update_tools)

        def show_log_tail() -> None:
            text = logbook.read_tail()
            with ui.dialog() as d, ui.card().classes('w-[840px] max-w-full'):
                ui.label(t('Full log (most recent)', 'Registro completo (lo más reciente)')) \
                    .classes('font-medium')
                with ui.element('div').classes(
                        'w-full max-h-[70vh] overflow-auto bg-black rounded p-2'):
                    ui.html('<pre style="white-space:pre-wrap;font-size:11px;margin:0;'
                            f'color:#e0e0e0">{html_mod.escape(text)}</pre>')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button(t('Copy all', 'Copiar todo'), icon='content_copy',
                              on_click=lambda: copy_to_clipboard(
                                  text, t('Log copied', 'Registro copiado'))) \
                        .props('unelevated')
                    ui.button(t('Close', 'Cerrar'), on_click=d.close).props('flat')
            d.open()

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label(t('Log', 'Registro')).classes('text-lg font-medium')
            ui.label(t('Every capture notes why it started and why it ended: exit code, '
                       'reason and the last recorder lines.',
                       'Cada grabación anota por qué empezó y por qué se cerró: código de '
                       'salida, motivo y las últimas líneas del grabador.')) \
                .classes('text-xs text-gray-500')
            ui.label(t('File: {}', 'Archivo: {}').format(logbook.LOG_FILE)) \
                .classes('text-xs text-gray-500 break-all')
            with ui.row().classes('gap-2'):
                ui.button(t('View log', 'Ver registro'), icon='visibility',
                          on_click=show_log_tail).props('unelevated')
                ui.button(t('Open .log', 'Abrir .log'), icon='description',
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
            cfg.language = lang.value or 'en'
            cfg.lan_access = bool(lan.value)
            try:
                cfg.recordings_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                ui.notify(t('Could not create the folder: {}',
                            'No se pudo crear la carpeta: {}').format(exc), type='negative')
                return
            config_mod.save(cfg)
            i18n.set_language(cfg.language)
            ui.notify(t('Settings saved', 'Ajustes guardados')
                      + (t(' · restart the app to apply folder/access',
                           ' · reinicia la app para aplicar carpeta/acceso')
                         if restart_needed else ''), type='positive')

        ui.button(t('Save settings', 'Guardar ajustes'), icon='save', on_click=save) \
            .props('unelevated')

        with ui.card().classes('w-full gap-2').props('flat bordered'):
            ui.label(t('About', 'Acerca de')).classes('text-lg font-medium')
            ui.label(t('RecordBate is made by {}. This is an early test build — '
                       'follow the project and send feedback here:',
                       'RecordBate está hecho por {}. Esta es una versión de prueba '
                       'temprana — sigue el proyecto y envía tu feedback aquí:')
                     .format(AUTHOR)).classes('text-sm text-gray-400')
            with ui.row().classes('w-full gap-2 flex-wrap'):
                for name, url in LINKS.items():
                    icon, color = _LINK_STYLE.get(name, ('link', 'grey-4'))
                    ui.button(name, icon=icon,
                              on_click=lambda u=url: webbrowser.open(u)) \
                        .props(f'outline no-caps color={color}')

        ui.label(f'RecordBate v{__version__} · {AUTHOR}') \
            .classes('text-xs text-gray-600 self-center')
