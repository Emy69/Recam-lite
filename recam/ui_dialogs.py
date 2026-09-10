"""The app-level dialogs: the test-build welcome and the question behind the X."""
from __future__ import annotations

import webbrowser

from nicegui import ui

from . import __version__
from . import config as config_mod
from .i18n import t


def _link(name: str, icon: str, tint: str) -> None:
    # the system browser, never ui.link: inside the native window that would
    # navigate the app away
    ui.button(name, icon=icon, on_click=lambda: webbrowser.open(config_mod.LINKS[name])) \
        .props('outline dense no-caps no-wrap size=sm color=grey-8').classes(f'[&_.q-icon]:{tint}')


def beta_notice(cfg: config_mod.Config, on_tutorial) -> ui.dialog:
    """The welcome shown on every start until the tester opts out."""
    with ui.dialog() as dialog, ui.card().classes('w-[460px] max-w-full gap-3 rounded-xl p-5'):
        with ui.row().classes('items-center gap-3.5 flex-nowrap'):
            with ui.element('div').classes('w-[52px] h-[52px] rounded-full bg-white/5 border-2 '
                                           'border-white/10 flex items-center justify-center '
                                           'flex-none'):
                ui.icon('radio_button_checked', size='md').classes('text-rose-600')
            with ui.column().classes('gap-0.5 min-w-0'):
                ui.label(t('Welcome to the Recam test build',
                           'Bienvenido a la versión de prueba de Recam')) \
                    .classes('text-[15px] font-semibold')
                ui.label(t('v{} · records Chaturbate only for now',
                           'v{} · por ahora solo graba Chaturbate').format(__version__)) \
                    .classes('text-xs text-gray-500')
        ui.label(t('This is an early version and some things may still break. Anything you '
                   'report back — bugs, confusing bits, ideas — is what moves development '
                   'forward. Thank you for testing.',
                   'Es una versión temprana y puede que algo falle todavía. Todo lo que '
                   'cuentes — fallos, partes confusas, ideas — es lo que hace avanzar el '
                   'desarrollo. Gracias por probarla.')) \
            .classes('text-[13px] text-gray-300 leading-relaxed')
        with ui.row().classes('w-full items-center gap-2 rounded-lg bg-white/[.03] border '
                              'border-white/5 p-2.5 flex-nowrap'):
            ui.label(t('Say hi or report something:', 'Saluda o cuenta algo:')) \
                .classes('text-xs text-gray-500 grow')
            _link('Discord', 'forum', 'text-indigo-300')
            _link('Patreon', 'favorite', 'text-rose-400')
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            dont_show = ui.checkbox(t("Don't show this again", 'No volver a mostrar esto')) \
                .props('dense size=sm').classes('text-xs text-gray-400 grow')

            def dismiss() -> None:
                if dont_show.value:
                    cfg.show_beta_notice = False
                    config_mod.save(cfg)
                dialog.close()

            def tutorial() -> None:
                dismiss()
                on_tutorial()

            ui.button(t('Tutorial', 'Tutorial'), icon='school', on_click=tutorial) \
                .props('outline no-caps no-wrap color=grey-5')
            ui.button('OK', on_click=dismiss).props('unelevated no-caps color=white text-color=dark')
    return dialog


def close_question(tray_active: bool, on_hide, on_quit) -> dict:
    """The dialog behind the window's X. Returns the pieces app.py keeps so the
    warning about running captures can be filled in before opening it."""
    with ui.dialog().props('persistent') as dialog, \
            ui.card().classes('w-[400px] max-w-full gap-2.5 rounded-xl p-5'):
        ui.label(t('Close Recam?', '¿Cerrar Recam?')).classes('text-[15px] font-semibold')
        with ui.row().classes('w-full items-start gap-2 rounded-md bg-rose-600/10 border '
                              'border-rose-600/30 px-2.5 py-2 flex-nowrap') as warn:
            ui.icon('fiber_manual_record', size='xs').classes('text-rose-400 flex-none mt-0.5')
            warn_text = ui.label('').classes('text-xs text-rose-200')
        warn.set_visibility(False)
        if tray_active:
            ui.label(t('Hide keeps Recam in the tray, recording in the background. Bring it '
                       'back from the tray icon.',
                       'Esconder deja Recam en la bandeja, grabando de fondo. Se recupera '
                       'desde el icono de la bandeja.')) \
                .classes('text-xs text-gray-500 leading-relaxed')
        with ui.row().classes('w-full justify-end gap-2 mt-1.5'):
            ui.button(t('Cancel', 'Cancelar'), on_click=dialog.close) \
                .props('flat no-caps color=grey-4')
            if tray_active:
                # the safe default, so it is the one that looks primary
                ui.button(t('Hide to tray', 'Esconder en la bandeja'), icon='visibility_off',
                          on_click=lambda: (dialog.close(), on_hide())) \
                    .props('unelevated no-caps no-wrap color=white text-color=dark')
            ui.button(t('Quit for real', 'Cerrar del todo'), icon='power_settings_new',
                      on_click=lambda: (dialog.close(), on_quit())) \
                .props('outline no-caps no-wrap color=primary')
    return {'dialog': dialog, 'warn': warn, 'warn_text': warn_text}
