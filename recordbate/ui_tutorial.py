"""The in-app tutorial: a short step-by-step tour, opened from the header (?)
or from the test-build welcome. Built fresh on every call so it always renders
in the caller's client and language."""
from __future__ import annotations

from nicegui import ui

from . import tools
from .i18n import t


def _steps() -> list[tuple[str, str, str]]:
    return [
        (
            'add_link',
            t('Add a channel', 'Añade un canal'),
            t('Copy a Chaturbate channel URL from your browser (for example '
              'chaturbate.com/somemodel), paste it into the box at the top of the '
              'Panel and press Add. The channel appears as a tile with its status: '
              'LIVE, OFFLINE, UNKNOWN or RECORDING.',
              'Copia la URL de un canal de Chaturbate desde tu navegador (por '
              'ejemplo chaturbate.com/unmodelo), pégala en la caja de arriba del '
              'Panel y pulsa Añadir. El canal aparece como tarjeta con su estado: '
              'EN VIVO, OFFLINE, DESCONOCIDO o GRABANDO.'),
        ),
        (
            'fiber_manual_record',
            t('Record automatically', 'Graba automáticamente'),
            t('Every tile has an auto switch. With it on, the app checks the '
              'channel every cycle and starts recording the moment it goes live — '
              'no clicks needed. The top switch (Automatic monitoring) is the '
              'master; Check now polls everyone immediately, and the ⏺ button '
              'forces a recording right now. Stopping one pauses its auto-record '
              'for 10 minutes.',
              'Cada tarjeta tiene un interruptor auto. Con él activado, la app '
              'comprueba el canal en cada ciclo y empieza a grabar en cuanto se '
              'pone en vivo — sin tocar nada. El interruptor de arriba (Vigilancia '
              'automática) es el maestro; Comprobar ahora chequea todos al '
              'momento, y el botón ⏺ fuerza una grabación ya. Al parar una, su '
              'auto-grabación se pausa 10 minutos.'),
        ),
        (
            'visibility',
            t('Watch it happen', 'Míralo en vivo'),
            t('While a channel records, its tile shows a live preview frame that '
              'refreshes every few seconds, plus a running counter of time and '
              'size. The Recent activity feed at the bottom keeps a short history: '
              'what started, what got saved, what failed and why.',
              'Mientras un canal graba, su tarjeta muestra un fotograma en vivo '
              'que se refresca cada pocos segundos, más el contador de tiempo y '
              'tamaño. El feed de Actividad reciente de abajo guarda el historial '
              'corto: qué empezó, qué se guardó, qué falló y por qué.'),
        ),
        (
            'video_library',
            t('Your library', 'Tu biblioteca'),
            t('Recordings are grouped into one collapsible section per profile. '
              'Click any tile to play it in the built-in player: ±10 s skips, '
              'playback speed, and it remembers your volume and where you left '
              'off. The ☑ button selects several recordings for one bulk delete '
              '(always to the recycle bin). A tile marked RAW is an interrupted '
              'capture — click it to convert it to a clean MP4.',
              'Las grabaciones se agrupan en una sección plegable por perfil. '
              'Clic en cualquier tarjeta para reproducirla en el reproductor '
              'integrado: saltos de ±10 s, velocidad, y recuerda tu volumen y por '
              'dónde ibas. El botón ☑ selecciona varias grabaciones para borrarlas '
              'en lote (siempre a la papelera). Una tarjeta marcada SIN PROCESAR '
              'es una captura interrumpida — clic para convertirla a un MP4 '
              'limpio.'),
        ),
        (
            'settings',
            t('Make it yours', 'Hazla tuya'),
            t('In Settings: the recordings folder, quality, how often channels '
              'get checked, the file name template, the interface language, and '
              'Start with Windows so the app watches your channels from sign-in. '
              'LAN access lets you open the panel from your phone.',
              'En Ajustes: la carpeta de grabaciones, la calidad, cada cuánto se '
              'comprueban los canales, la plantilla de nombres, el idioma de la '
              'interfaz, e Inicio con Windows para que la app vigile tus canales '
              'desde que enciendes el PC. El acceso LAN te deja abrir el panel '
              'desde el móvil.'),
        ),
        (
            'favorite',
            t('Closing, and your feedback', 'Cerrar, y tu feedback'),
            t('Minimizing minimizes normally. The window X asks whether to hide '
              'the app to the tray (it keeps recording in the background) or quit '
              'for real — quitting finalizes captures in flight first. If '
              'anything breaks, the tile menu → View log shows exactly what '
              'happened. This is a test build: every bit of feedback you share '
              'genuinely helps development. Thank you!',
              'Minimizar minimiza normal. La X pregunta si esconder la app a la '
              'bandeja (sigue grabando de fondo) o cerrarla del todo — al cerrar, '
              'las grabaciones en curso se finalizan primero. Si algo falla, el '
              'menú de la tarjeta → Ver registro muestra exactamente qué pasó. '
              'Esto es una versión de prueba: cada comentario que dejes ayuda de '
              'verdad al desarrollo. ¡Gracias!'),
        ),
    ]


def show() -> None:
    steps = _steps()
    with ui.dialog() as dialog, ui.card().classes('w-[600px] max-w-full p-0 gap-0'):
        with ui.row().classes('w-full items-center gap-2 px-4 pt-3'):
            ui.icon('school').classes('text-xl text-rose-600')
            ui.label(t('How RecordBate works', 'Cómo funciona RecordBate')) \
                .classes('text-base font-medium')
            ui.space()
            ui.button(icon='close', on_click=dialog.close).props('flat round dense')
        with ui.stepper().props('vertical flat done-color=positive') \
                .classes('w-full') as stepper:
            missing_ffmpeg = not tools.ffmpeg_path()
            for i, (icon, title, body) in enumerate(steps):
                with ui.step(title).props(f'icon={icon}'):
                    ui.label(body).classes('text-sm text-gray-300')
                    if i == 0 and missing_ffmpeg:
                        ui.label(t('⚠ ffmpeg was not found on this PC — recordings '
                                   'need it. Install it with:  winget install '
                                   'Gyan.FFmpeg  and restart the app.',
                                   '⚠ No se encontró ffmpeg en este PC — las '
                                   'grabaciones lo necesitan. Instálalo con:  '
                                   'winget install Gyan.FFmpeg  y reinicia la '
                                   'app.')).classes('text-xs text-amber-500')
                    with ui.stepper_navigation():
                        if i < len(steps) - 1:
                            ui.button(t('Next', 'Siguiente'),
                                      on_click=stepper.next).props('unelevated dense')
                        else:
                            ui.button(t('Done', 'Listo'),
                                      on_click=dialog.close).props('unelevated dense')
                        if i > 0:
                            ui.button(t('Back', 'Atrás'),
                                      on_click=stepper.previous).props('flat dense')
    dialog.open()
