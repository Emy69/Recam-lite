from __future__ import annotations

import asyncio
import html as html_mod
import os
import re
import socket
import sys
import webbrowser
from contextlib import contextmanager

from nicegui import app, ui

from . import __version__, AUTHOR, LINKS, i18n, logbook, tools
from . import config as config_mod
from .i18n import t
from .models import Streamer
from .monitor import Monitor
from .ui_common import copy_to_clipboard, ffmpeg_downloader, notify, open_log_file

# the icon carries the brand colour; the button itself stays neutral
_LINK_STYLE = {
    'Discord': ('forum', 'text-indigo-300'),
    'Patreon': ('favorite', 'text-rose-400'),
    'GitHub': ('code', 'text-gray-400'),
    'X': ('tag', 'text-gray-400'),
    'Buy Me a Coffee': ('coffee', 'text-amber-400'),
}

# the repeating layout: what the option is on the left, the control on the right
_ROW = 'w-full grid grid-cols-[260px_1fr] gap-4 py-3 border-t border-white/5 items-center'
_FIELD = 'outlined dense'


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


def _short_version(text: str | None) -> str | None:
    """'ffmpeg version 7.1-full_build-www.gyan.dev Copyright…' -> '7.1'."""
    if not text:
        return None
    m = re.search(r'version\s+(\S+)', text)
    token = m.group(1) if m else text
    return token.split('-')[0].split('_')[0] or text


@contextmanager
def _group(title: str):
    """One settings card: an uppercase title, then rows. Yields the title row so a
    caller can append a badge to it."""
    with ui.card().classes('w-full px-4 py-1 rounded-[10px] gap-0').props('flat bordered'):
        with ui.row().classes('w-full items-center gap-2 pt-3 pb-2') as head:
            ui.label(title).classes('text-xs font-semibold uppercase tracking-[.08em] '
                                    'text-gray-400')
        yield head


@contextmanager
def _row(label: str, hint: str = ''):
    with ui.element('div').classes(_ROW):
        with ui.column().classes('gap-0.5 min-w-0'):
            ui.label(label).classes('text-sm font-medium')
            if hint:
                ui.label(hint).classes('text-xs text-gray-500 leading-snug')
        with ui.column().classes('gap-1.5 min-w-0 items-start') as control:
            yield control


def build(cfg: config_mod.Config, monitor: Monitor) -> None:
    cards: dict[str, ui.element] = {}
    nav_dot: dict[str, ui.element] = {}
    mascot = config_mod.BASE_DIR / 'mascot.png'

    with ui.element('div').classes('w-full max-w-[880px] mx-auto grid '
                                   'grid-cols-[180px_1fr] gap-6 items-start pb-20'):
        nav = ui.column().classes('sticky top-2 gap-0.5 text-xs')
        body = ui.column().classes('w-full gap-3 min-w-0')

    with body:
        # ------------------------------------------------------------- recording
        with ui.element('div').classes('w-full') as cards['recording']:
            with _group(t('Recording', 'Grabación')):
                with _row(t('Recordings folder', 'Carpeta de grabaciones'),
                          t('One subfolder per streamer inside this path.',
                            'Una subcarpeta por streamer dentro de esta ruta.')):
                    with ui.row().classes('w-full gap-1.5 flex-nowrap'):
                        dir_input = ui.input(value=cfg.recordings_dir) \
                            .props(_FIELD).classes('grow font-mono text-xs')

                        async def choose_folder() -> None:
                            import webview
                            win = app.native.main_window
                            try:
                                picked = await win.create_file_dialog(
                                    webview.FOLDER_DIALOG, directory=dir_input.value or '')
                            except Exception as exc:
                                notify(t('Could not open the folder picker: {}',
                                         'No se pudo abrir el selector de carpeta: {}')
                                       .format(exc), type='negative')
                                return
                            if picked:
                                dir_input.value = picked[0] if isinstance(
                                    picked, (list, tuple)) else str(picked)

                        if app.native.main_window is not None:
                            ui.button(t('Choose', 'Elegir'), icon='folder_open',
                                      on_click=choose_folder) \
                                .props('flat dense no-caps no-wrap').classes('bg-white/5 shrink-0')
                with _row(t('Quality', 'Calidad'),
                          t('The variant at or below this cap gets picked.',
                            'Se elige la variante igual o inferior a este tope.')):
                    quality = ui.select({'best': t('Best available', 'Máxima disponible'),
                                         '1080p': t('Up to 1080p', 'Hasta 1080p'),
                                         '720p': t('Up to 720p', 'Hasta 720p'),
                                         '480p': t('Up to 480p', 'Hasta 480p')},
                                        value=cfg.quality) \
                        .props(_FIELD + ' options-dense').classes('w-56')
                with _row(t('Check every', 'Comprobar cada'),
                          t('Seconds between rounds of checks. Under 60 risks a 429.',
                            'Segundos entre rondas de comprobación. Menos de 60 arriesga un 429.')):
                    with ui.row().classes('items-center gap-2 flex-nowrap'):
                        poll = ui.number(value=cfg.poll_seconds, min=15, max=3600, step=15) \
                            .props(_FIELD).classes('w-24 font-mono')
                        ui.label(t('sec · 15 – 3600', 'seg · 15 – 3600')) \
                            .classes('text-xs text-gray-500')
                with _row(t('Concurrent recordings', 'Grabaciones simultáneas'),
                          t('Cap on captures at once; the rest wait their turn.',
                            'Tope de capturas a la vez; las demás esperan su turno.')):
                    with ui.row().classes('items-center gap-2 flex-nowrap'):
                        maxc = ui.number(value=cfg.max_concurrent, min=1, max=20) \
                            .props(_FIELD).classes('w-24 font-mono')
                        ui.label('1 – 20').classes('text-xs text-gray-500')
                with _row(t('File name template', 'Plantilla de nombre'),
                          t('Variables: {streamer} {platform} {date} {time} · "/" makes subfolders.',
                            'Variables: {streamer} {platform} {date} {time} · «/» crea subcarpetas.')):
                    tmpl = ui.input(value=cfg.filename_template) \
                        .props(_FIELD).classes('w-full font-mono text-xs')
                    tmpl_preview = ui.label().classes('font-mono text-[11px] text-gray-600 truncate w-full')
                with _row(t('Audio nudge', 'Ajuste de audio'),
                          t('Leave it at 0. Audio ahead → positive; behind → negative. Applied '
                            'when converting to MP4, without re-encoding.',
                            'Déjalo en 0. Si el audio va adelantado → positivo; atrasado → '
                            'negativo. Se aplica al convertir a MP4, sin recodificar.')):
                    with ui.row().classes('items-center gap-2 flex-nowrap'):
                        audio_off = ui.number(value=cfg.audio_offset_ms,
                                              min=-2000, max=2000, step=50) \
                            .props(_FIELD).classes('w-24 font-mono')
                        ui.label(t('ms · ±2000, steps of 50', 'ms · ±2000, pasos de 50')) \
                            .classes('text-xs text-gray-500')

        # ----------------------------------------------------------- application
        with ui.element('div').classes('w-full') as cards['app']:
            with _group(t('Application', 'Aplicación')):
                with _row(t('Interface language', 'Idioma de la interfaz'),
                          t('Reload the page (or restart) to apply it everywhere.',
                            'Recarga la página (o reinicia) para aplicarlo en todo.')):
                    lang = ui.select({'en': 'English', 'es': 'Español'},
                                     value=getattr(cfg, 'language', 'en')) \
                        .props(_FIELD + ' options-dense').classes('w-56')
                if os.name == 'nt':
                    with _row(t('Start with Windows', 'Inicio con Windows'),
                              t('Creates a shortcut in your Startup folder; it opens '
                                'watching the channels with auto-record on.',
                                'Crea un acceso directo en tu carpeta Inicio; arranca '
                                'vigilando con auto-grabar.')):
                        auto_start = ui.switch(t('Launch at sign-in',
                                                 'Arrancar al iniciar sesión'),
                                               value=tools.startup_enabled()) \
                            .props('dense color=grey-3 keep-color')

                        def on_autostart(e) -> None:
                            if tools.set_startup(bool(e.value)):
                                notify(t('Will start with Windows (no console window)',
                                         'Arrancará con Windows (sin ventana de consola)')
                                       if e.value else
                                       t('No longer starts with Windows',
                                         'Ya no arranca con Windows'), type='positive')
                            else:
                                notify(t('Could not change the automatic start',
                                         'No se pudo cambiar el inicio automático'),
                                       type='negative')
                                auto_start.value = tools.startup_enabled()

                        auto_start.on_value_change(on_autostart)
                with _row(t('Access from the local network', 'Acceso desde la red local'),
                          t('Open the panel from your phone or another PC. Applies after '
                            'a restart.',
                            'Abre el panel desde el móvil u otro PC. Se aplica al reiniciar.')):
                    lan = ui.switch(t('Allow (phone, another PC)', 'Permitir (móvil, otro PC)'),
                                    value=cfg.lan_access).props('dense color=grey-3 keep-color')
                    ip = _lan_ip()
                    ui.label(t('now: http://127.0.0.1:{}', 'ahora: http://127.0.0.1:{}')
                             .format(cfg.port)
                             + (t('   with LAN: http://{}:{}', '   con LAN: http://{}:{}')
                                .format(ip, cfg.port) if ip else '')) \
                        .classes('font-mono text-[11px] text-gray-600')
                with _row(t('When the window closes', 'Al cerrar la ventana'),
                          t('The X asks: hide to the tray (keeps recording) or quit for '
                            'real. Minimizing just minimizes.',
                            'La X pregunta: esconder a la bandeja (sigue grabando) o cerrar '
                            'del todo. Minimizar minimiza normal.')):
                    ui.label(t('No option here: it always asks.',
                               'Sin opción: siempre pregunta.')).classes('text-xs text-gray-500')

        # ----------------------------------------------------------------- tools
        @ui.refreshable
        def tools_card() -> None:
            missing = tools.missing_tools()
            if 'tools' in nav_dot:
                nav_dot['tools'].set_visibility(bool(missing))
            ffmpeg = tools.ffmpeg_path()
            ffprobe = tools.ffprobe_path()
            cells = [
                ('ffmpeg', _short_version(tools.tool_version(ffmpeg)) if ffmpeg else None),
                ('ffprobe', _short_version(tools.tool_version(ffprobe)) if ffprobe else None),
                ('yt-dlp', tools.package_version('yt-dlp')),
                ('NiceGUI', tools.package_version('nicegui')),
            ]
            streamlink = tools.package_version('streamlink')
            if streamlink:   # not shipped in the Chaturbate-only frozen build
                cells.insert(3, ('streamlink', streamlink))
            with _group(t('Tools', 'Herramientas')) as head:
                if missing:
                    with head:
                        ui.label(t('ffmpeg missing', 'falta ffmpeg')) \
                            .classes('text-[11px] font-semibold text-rose-300 bg-rose-600/15 '
                                     'rounded px-1.5 py-0.5')
                with ui.element('div').classes('w-full grid grid-cols-2 md:grid-cols-4 gap-2 '
                                               'pt-2 pb-3 border-t border-white/5'):
                    for name, version in cells:
                        tone = ('bg-rose-600/10 border-rose-600/35' if version is None
                                else 'bg-white/[.03] border-white/5')
                        with ui.row().classes(f'items-center gap-2 rounded-md p-2 border {tone} '
                                              'flex-nowrap min-w-0'):
                            ui.icon('check_circle' if version else 'error', size='16px') \
                                .classes('text-green-400' if version else 'text-rose-400')
                            with ui.column().classes('gap-0 min-w-0'):
                                ui.label(name).classes('text-xs font-semibold')
                                ui.label(version or t('not found', 'no encontrado')) \
                                    .classes('font-mono text-[11px] truncate '
                                             + ('text-gray-500' if version else 'text-rose-400'))
                if missing:
                    with _row(t('Download ffmpeg', 'Descargar ffmpeg'),
                              t('About 110 MB, no admin rights. Alternative: '
                                'winget install Gyan.FFmpeg',
                                'Unos 110 MB, sin permisos de administrador. Alternativa: '
                                'winget install Gyan.FFmpeg')):
                        ffmpeg_downloader(tools_card.refresh)
                if tools.IS_FROZEN:
                    ui.label(t('Tool updates ship with new app builds.',
                               'Las actualizaciones de herramientas llegan con nuevas '
                               'versiones de la app.')).classes('text-xs text-gray-500 pb-3')
                else:
                    with _row(t('Update yt-dlp and streamlink',
                                'Actualizar yt-dlp y streamlink'),
                              t('These sites change often; if a platform stops recording, '
                                'update here and restart the app.',
                                'Estos sitios cambian a menudo; si una plataforma deja de '
                                'grabar, actualiza aquí y reinicia la app.')):
                        update_btn = ui.button(t('Update', 'Actualizar'),
                                               icon='system_update_alt') \
                            .props('outline dense no-caps color=grey-5')

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
                                notify(t('Updated. Restart the app to use the new versions.',
                                         'Actualizado. Reinicia la app para usar las '
                                         'versiones nuevas.'), type='positive')
                            else:
                                notify(t('The update failed; check the connection.',
                                         'La actualización falló; revisa la conexión.'),
                                       type='negative')

                        update_btn.on_click(update_tools)

        with ui.element('div').classes('w-full') as cards['tools']:
            tools_card()

        # ------------------------------------------------------------------- log
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

        with ui.element('div').classes('w-full') as cards['log']:
            with _group(t('Log', 'Registro')):
                with _row('recam.log',
                          t('Every capture notes why it started and why it ended: exit code, '
                            'reason and the last recorder lines.',
                            'Cada grabación anota por qué empezó y por qué se cerró: código '
                            'de salida, motivo y últimas líneas.')):
                    with ui.row().classes('gap-1.5 flex-nowrap'):
                        ui.button(t('View log', 'Ver registro'), icon='visibility',
                                  on_click=show_log_tail) \
                            .props('flat dense no-caps no-wrap').classes('bg-white/10')
                        ui.button(t('Open .log', 'Abrir .log'), icon='description',
                                  on_click=open_log_file) \
                            .props('flat dense no-caps no-wrap').classes('bg-white/5')
                    ui.label(str(logbook.LOG_FILE)) \
                        .classes('font-mono text-[11px] text-gray-600 break-all')

        # ----------------------------------------------------------------- about
        with ui.element('div').classes('w-full') as cards['about']:
            with _group(t('About', 'Acerca de')):
                with ui.row().classes('w-full items-center gap-3.5 pt-3 pb-3.5 border-t '
                                      'border-white/5 flex-nowrap'):
                    if mascot.exists():
                        ui.image(str(mascot)).classes('w-11 h-11 rounded-full flex-none')
                    with ui.column().classes('grow min-w-0 gap-1.5'):
                        ui.label(t('Recam v{} · test build · made by {}. If something breaks '
                                   'or confuses you, say so: that is what moves development '
                                   'forward.',
                                   'Recam v{} · versión de prueba · hecho por {}. Si algo '
                                   'falla o confunde, cuéntalo: es lo que hace avanzar el '
                                   'desarrollo.').format(__version__, AUTHOR)) \
                            .classes('text-xs text-gray-400')
                        with ui.row().classes('gap-1.5 flex-wrap'):
                            for name in _LINK_STYLE:
                                url = LINKS.get(name)
                                if not url:
                                    continue
                                icon, tint = _LINK_STYLE[name]
                                # links open in the system browser; ui.link would
                                # navigate the native window away from the app
                                ui.button(name, icon=icon,
                                          on_click=lambda u=url: webbrowser.open(u)) \
                                    .props('outline dense no-caps no-wrap size=sm color=grey-8') \
                                    .classes(f'[&_.q-icon]:{tint}')

    # ------------------------------------------------------------ left nav
    entries = [('recording', t('Recording', 'Grabación')),
               ('app', t('Application', 'Aplicación')),
               ('tools', t('Tools', 'Herramientas')),
               ('log', t('Log', 'Registro')),
               ('about', t('About', 'Acerca de'))]
    nav_items: dict[str, ui.element] = {}
    active = ['recording']

    def go(key: str) -> None:
        nav_items[active[0]].classes(remove='bg-white/5 text-white font-semibold')
        active[0] = key
        nav_items[key].classes(add='bg-white/5 text-white font-semibold')
        ui.run_javascript(f'document.getElementById("c{cards[key].id}")'
                          '?.scrollIntoView({behavior: "smooth", block: "start"})')

    with nav:
        for key, label in entries:
            with ui.element('div').classes('px-2.5 py-1.5 rounded-md cursor-pointer '
                                           'text-gray-400 hover:bg-white/5 flex items-center '
                                           'gap-1.5') as item:
                ui.label(label)
                if key == 'tools':
                    nav_dot[key] = ui.element('span').classes('w-1.5 h-1.5 rounded-full '
                                                              'bg-rose-600')
                    nav_dot[key].set_visibility(bool(tools.missing_tools()))
            item.on('click', lambda _e, k=key: go(k))
            nav_items[key] = item
        nav_items[active[0]].classes(add='bg-white/5 text-white font-semibold')
        ui.label(t('Changes are saved with the button at the bottom. Folder and access '
                   'apply after a restart.',
                   'Los cambios se guardan con el botón de abajo. Carpeta y acceso se '
                   'aplican al reiniciar.')) \
            .classes('text-[11px] text-gray-500 leading-normal px-2.5 mt-4')

    # ------------------------------------------------- unsaved changes + save
    def current() -> dict:
        return {
            'recordings_dir': (dir_input.value or '').strip()
                              or config_mod.Config().recordings_dir,
            'quality': quality.value or 'best',
            'poll_seconds': int(poll.value or 60),
            'max_concurrent': int(maxc.value or 4),
            'filename_template': (tmpl.value or '').strip() or config_mod.DEFAULT_TEMPLATE,
            'audio_offset_ms': int(audio_off.value or 0),
            'language': lang.value or 'en',
            'lan_access': bool(lan.value),
        }

    def template_preview() -> str:
        sample = config_mod.Config()
        sample.filename_template = current()['filename_template']
        try:
            stem = config_mod.build_output_stem(
                sample, Streamer(url='', platform='chaturbate', username='streamer'))
        except Exception:
            return t('→ invalid template', '→ plantilla no válida')
        return '→ ' + str(stem).replace('\\', '/') + '.mp4'

    def changes() -> int:
        return sum(1 for key, value in current().items() if getattr(cfg, key) != value)

    def on_change(_e=None) -> None:
        n = changes()
        footer.set_visibility(n > 0)
        dirty_lbl.set_text(t('{} unsaved change(s)', '{} cambio(s) sin guardar').format(n))
        tmpl_preview.set_text(template_preview())

    def discard() -> None:
        dir_input.value = cfg.recordings_dir
        quality.value = cfg.quality
        poll.value = cfg.poll_seconds
        maxc.value = cfg.max_concurrent
        tmpl.value = cfg.filename_template
        audio_off.value = cfg.audio_offset_ms
        lang.value = getattr(cfg, 'language', 'en')
        lan.value = cfg.lan_access
        on_change()

    def save() -> None:
        values = current()
        restart_needed = (values['recordings_dir'] != cfg.recordings_dir
                          or values['lan_access'] != cfg.lan_access)
        for key, value in values.items():
            setattr(cfg, key, value)
        try:
            cfg.recordings_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            notify(t('Could not create the folder: {}',
                     'No se pudo crear la carpeta: {}').format(exc), type='negative')
            return
        config_mod.save(cfg)
        i18n.set_language(cfg.language)
        on_change()
        notify(t('Settings saved', 'Ajustes guardados')
               + (t(' · restart the app to apply folder/access',
                    ' · reinicia la app para aplicar carpeta/acceso')
                  if restart_needed else ''), type='positive')

    # fixed to the bottom of the tab; the panel hides with the tab (keep-alive)
    with ui.element('div').classes('fixed bottom-0 left-0 right-0 h-14 bg-[#0e141b]/90 '
                                   'backdrop-blur border-t border-white/5 px-6 flex '
                                   'items-center gap-3 z-20') as footer:
        ui.element('span').classes('w-2 h-2 rounded-full bg-amber-500 flex-none')
        dirty_lbl = ui.label().classes('text-xs text-gray-300 whitespace-nowrap')
        ui.label(t('· folder and access apply after a restart',
                   '· carpeta y acceso se aplican al reiniciar')) \
            .classes('text-xs text-gray-500 truncate')
        ui.element('div').classes('grow')
        ui.button(t('Discard', 'Descartar'), on_click=discard) \
            .props('flat dense no-caps color=grey-5')
        # white, not red: red stays reserved for recording
        ui.button(t('Save settings', 'Guardar ajustes'), icon='save', on_click=save) \
            .props('unelevated no-caps no-wrap color=white text-color=dark')
    footer.set_visibility(False)

    for element in (dir_input, quality, poll, maxc, tmpl, audio_off, lang, lan):
        element.on_value_change(on_change)
    tmpl_preview.set_text(template_preview())
