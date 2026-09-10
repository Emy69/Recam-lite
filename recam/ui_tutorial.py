"""The in-app tutorial: a short step-by-step tour, opened from the header (?)
or from the test-build welcome. Built fresh on every call so it always renders
in the caller's client and language."""
from __future__ import annotations

import webbrowser

from nicegui import ui

from . import config as config_mod
from . import tools
from .i18n import t


def _steps() -> list[dict]:
    ffmpeg_tip = ''
    if tools.missing_tools():
        ffmpeg_tip = t('ffmpeg was not found on this PC — recordings need it. Settings › Tools '
                       'downloads it in one click.',
                       'No se encontró ffmpeg en este PC — las grabaciones lo necesitan. '
                       'Ajustes › Herramientas lo descarga con un clic.')
    return [
        {
            'icon': 'add_link',
            'title': t('Add a channel', 'Añade un canal'),
            'body': t('Copy a Chaturbate channel URL from your browser (for example '
                      'chaturbate.com/somemodel), paste it into the box at the top of the '
                      'Panel and press Add. The channel appears as a card with its status: '
                      'LIVE, OFFLINE, UNKNOWN or RECORDING.',
                      'Copia la URL de un canal de Chaturbate desde tu navegador (por '
                      'ejemplo chaturbate.com/unmodelo), pégala en la caja de arriba del '
                      'Panel y pulsa Añadir. El canal aparece como tarjeta con su estado: '
                      'EN VIVO, OFFLINE, DESCONOCIDO o GRABANDO.'),
            'tip': ffmpeg_tip,
        },
        {
            'icon': 'fiber_manual_record',
            'title': t('Record automatically', 'Graba automáticamente'),
            'body': t('Every card has an Auto-record switch. With it on, the app checks the '
                      'channel every cycle and starts recording the moment it goes live. '
                      'The top switch, Automatic monitoring, is the master; Check now polls '
                      'everyone immediately, and the record button forces a capture right now.',
                      'Cada tarjeta tiene un interruptor Auto-grabar. Con él activado, la app '
                      'comprueba el canal en cada ciclo y empieza a grabar en cuanto se pone '
                      'en vivo. El interruptor de arriba, Vigilancia automática, es el '
                      'maestro; Comprobar ahora chequea todos al momento, y el botón de '
                      'grabar fuerza una captura ya.'),
            'tip': t('Stopping a recording by hand pauses its auto-record for 10 minutes.',
                     'Parar una grabación a mano pausa su auto-grabación 10 minutos.'),
        },
        {
            'icon': 'visibility',
            'title': t('Watch it happen', 'Míralo en vivo'),
            'body': t('While a channel records, its card shows a live preview frame that '
                      'refreshes every few seconds, plus a running counter of time and size. '
                      'Channels that are live but not recording show a still of the room. '
                      'The history button in the header opens the activity feed: what '
                      'started, what got saved, what failed and why.',
                      'Mientras un canal graba, su tarjeta muestra un fotograma en vivo que '
                      'se refresca cada pocos segundos, más el contador de tiempo y tamaño. '
                      'Los canales en vivo que no se graban muestran una imagen de la sala. '
                      'El botón de historial de la cabecera abre la actividad: qué empezó, '
                      'qué se guardó, qué falló y por qué.'),
            'tip': '',
        },
        {
            'icon': 'video_library',
            'title': t('Your library', 'Tu biblioteca'),
            'body': t('Recordings are grouped into one collapsible card per profile. Click '
                      'any video to play it in the built-in player: ±10 s skips, playback '
                      'speed, and it remembers your volume and where you left off. Select '
                      'lets you pick several recordings for one bulk delete (always to the '
                      'recycle bin).',
                      'Las grabaciones se agrupan en una tarjeta plegable por perfil. Clic '
                      'en cualquier vídeo para reproducirlo en el reproductor integrado: '
                      'saltos de ±10 s, velocidad, y recuerda tu volumen y por dónde ibas. '
                      'Seleccionar te deja marcar varias grabaciones para borrarlas en lote '
                      '(siempre a la papelera).'),
            'tip': t('A video marked RAW is an interrupted capture — click it to convert it '
                     'to a clean MP4.',
                     'Un vídeo marcado SIN PROCESAR es una captura interrumpida — clic para '
                     'convertirlo a un MP4 limpio.'),
        },
        {
            'icon': 'settings',
            'title': t('Make it yours', 'Hazla tuya'),
            'body': t('In Settings: the recordings folder, quality, how often channels get '
                      'checked, the file name template, the interface language, and Start '
                      'with Windows so the app watches your channels from sign-in. LAN '
                      'access lets you open the panel from your phone.',
                      'En Ajustes: la carpeta de grabaciones, la calidad, cada cuánto se '
                      'comprueban los canales, la plantilla de nombres, el idioma de la '
                      'interfaz, e Inicio con Windows para que la app vigile tus canales '
                      'desde que enciendes el PC. El acceso LAN te deja abrir el panel '
                      'desde el móvil.'),
            'tip': '',
        },
        {
            'icon': 'power_settings_new',
            'title': t('Closing the window', 'Cerrar la ventana'),
            'body': t('Minimizing minimizes normally. The window X asks whether to hide the '
                      'app to the tray (it keeps recording in the background) or quit for '
                      'real — quitting finalizes captures in flight first. If anything '
                      'breaks, the card menu → View log shows exactly what happened.',
                      'Minimizar minimiza normal. La X pregunta si esconder la app a la '
                      'bandeja (sigue grabando de fondo) o cerrarla del todo — al cerrar, '
                      'las grabaciones en curso se finalizan primero. Si algo falla, el '
                      'menú de la tarjeta → Ver registro muestra exactamente qué pasó.'),
            'tip': '',
        },
    ]


def show() -> None:
    steps = _steps()
    names = [s['title'] for s in steps]
    with ui.dialog() as dialog, \
            ui.card().classes('w-[600px] max-w-full p-0 gap-0 rounded-xl overflow-hidden'):
        with ui.row().classes('w-full items-center gap-2.5 px-4 pt-3.5 pb-2 flex-nowrap'):
            ui.icon('school', size='sm').classes('text-rose-600')
            ui.label(t('How Recam works', 'Cómo funciona Recam')) \
                .classes('text-[15px] font-semibold grow')
            counter = ui.label(f'1 / {len(steps)}').classes('font-mono text-[11px] text-gray-500')
            ui.button(icon='close', on_click=dialog.close).props('flat round dense color=grey-5')
        with ui.stepper(value=names[0]) \
                .props('vertical flat done-color=positive active-color=white '
                       'inactive-color=grey-7 header-nav') \
                .classes('w-full px-2 pb-2') as stepper:
            for i, step in enumerate(steps):
                with ui.step(step['title']).props(f'icon={step["icon"]}'):
                    ui.label(step['body']).classes('text-[13px] text-gray-300 leading-relaxed')
                    if step['tip']:
                        with ui.row().classes('w-full items-center gap-2 rounded-md bg-white/[.03] '
                                              'border border-white/5 px-2.5 py-2 flex-nowrap mt-1.5'):
                            ui.icon('info', size='xs').classes('text-gray-400 flex-none')
                            ui.label(step['tip']).classes('text-xs text-gray-500')
                    with ui.stepper_navigation().classes('gap-2 pt-1'):
                        if i < len(steps) - 1:
                            ui.button(t('Next', 'Siguiente'), on_click=stepper.next) \
                                .props('unelevated dense no-caps color=white text-color=dark')
                        else:
                            ui.button(t('Done', 'Listo'), on_click=dialog.close) \
                                .props('unelevated dense no-caps color=white text-color=dark')
                        if i > 0:
                            ui.button(t('Back', 'Atrás'), on_click=stepper.previous) \
                                .props('flat dense no-caps color=grey-4')
        stepper.on_value_change(lambda e: counter.set_text(
            f'{names.index(e.value) + 1 if e.value in names else 1} / {len(steps)}'))
        # one quiet line instead of a plea inside the last step
        with ui.row().classes('w-full items-center gap-2 px-4 py-2.5 border-t border-white/5 '
                              'flex-nowrap'):
            ui.label(t('Something unclear? Tell us — it shapes the next build.',
                       '¿Algo no queda claro? Cuéntanoslo: da forma a la siguiente versión.')) \
                .classes('text-xs text-gray-600 grow')
            for name, icon, tint in (('Discord', 'forum', 'text-indigo-300'),
                                     ('Patreon', 'favorite', 'text-rose-400')):
                ui.button(name, icon=icon,
                          on_click=lambda n=name: webbrowser.open(config_mod.LINKS[n])) \
                    .props('flat dense no-caps no-wrap size=sm color=grey-5') \
                    .classes(f'[&_.q-icon]:{tint}')
    dialog.open()
