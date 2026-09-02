"""Recam without a GUI: recording daemon plus channel management.

    python -m recam.cli dashboard           # live interactive panel (the nice one)
    python -m recam.cli run                 # headless daemon, Ctrl+C stops it cleanly
    python -m recam.cli add <url>           # add a channel, auto-record on
    python -m recam.cli remove <channel>    # drop a channel
    python -m recam.cli auto <channel> on   # toggle auto-record (on|off)
    python -m recam.cli list                # list the channels
    python -m recam.cli status              # who is live right now?
    python -m recam.cli now                 # what is being recorded right now?
    python -m recam.cli stop <channel>      # stop a capture in flight (or 'all')
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time

from . import config as config_mod
from . import i18n, logbook, platforms, status as status_mod, tools
from .i18n import t
from .library import Library
from .models import state_label, status_label
from .monitor import Monitor

STALE_AFTER = 30   # seconds; past this the status file is nobody's live state


def _build() -> tuple[config_mod.Config, Monitor]:
    cfg = config_mod.load()
    library = Library(cfg)
    return cfg, Monitor(cfg, config_mod.load_streamers(), library)


def _match(streamers, query: str):
    q = query.strip().lower()
    return [s for s in streamers if q in (s.username.lower(), s.url.lower(), s.key)]


def cmd_list(_args: argparse.Namespace) -> int:
    streamers = config_mod.load_streamers()
    if not streamers:
        print(t('No channels. Add one with:  python -m recam.cli add <url>',
                'No hay canales. Añade con:  python -m recam.cli add <url>'))
        return 0
    print(t('{} channels:', '{} canales:').format(len(streamers)))
    for s in streamers:
        auto = 'auto' if s.auto_record else 'manual'
        print(f'  [{s.platform:10}] {s.username:22} {auto:6}  {s.url}')
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    _cfg, monitor = _build()
    try:
        s = monitor.add_streamer(args.url)
    except ValueError as exc:
        print(t('Error: {}', 'Error: {}').format(exc), file=sys.stderr)
        return 1
    print(t('Added: {} ({})', 'Añadido: {} ({})').format(s.username, s.platform))
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    streamers = config_mod.load_streamers()
    found = _match(streamers, args.channel)
    if not found:
        print(t('No channel matches “{}”', 'No encontré ningún canal que coincida con «{}»')
              .format(args.channel), file=sys.stderr)
        return 1
    on = args.state == 'on'
    for s in found:
        s.auto_record = on
    config_mod.save_streamers(streamers)
    for s in found:
        print(f'{s.username}: ' + (t('auto-record ON', 'auto-grabar ACTIVADO') if on
                                   else t('auto-record off', 'auto-grabar desactivado')))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    streamers = config_mod.load_streamers()
    removed = _match(streamers, args.channel)
    if not removed:
        print(t('No channel matches “{}”', 'No encontré ningún canal que coincida con «{}»')
              .format(args.channel), file=sys.stderr)
        return 1
    config_mod.save_streamers([s for s in streamers if s not in removed])
    for s in removed:
        print(t('Removed: {} ({})', 'Eliminado: {} ({})').format(s.username, s.platform))
    return 0


async def _check_all() -> int:
    import httpx
    _cfg, monitor = _build()
    if not monitor.streamers:
        print(t('No channels.', 'No hay canales.'))
        return 0
    async with httpx.AsyncClient(headers=platforms.REQUEST_HEADERS, timeout=15,
                                 follow_redirects=True) as client:
        async def one(s) -> None:
            st = await platforms.check_online(client, s.platform, s.username)
            print(f'  {status_label(st)[0]:12} [{s.platform}] {s.username}')
        await asyncio.gather(*(one(s) for s in monitor.streamers))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    return asyncio.run(_check_all())


def cmd_now(_args: argparse.Namespace) -> int:
    data = status_mod.read()
    if not data:
        print(t('No state file. Is the daemon (recam-cli run) or the app running?',
                'No hay estado. ¿Está corriendo el daemon (recam-cli run) o la app?'))
        return 1
    age = time.time() - data.get('ts', 0)
    if age > STALE_AFTER:
        print(t('⚠ The state is {}s old — the daemon/app looks stopped.',
                '⚠ El estado tiene {}s de antigüedad — el daemon/app parece parado.')
              .format(int(age)))
    recs = data.get('recording', [])
    watching = 'ON' if data.get('enabled', True) else 'OFF'
    if not recs:
        print(t('Nothing recording now. ({} channels · monitoring {})',
                'Nada grabando ahora. ({} canales · vigilancia {})')
              .format(data.get('channels', 0), watching))
        return 0
    print(t('Recording {} · {} channels · monitoring {}:',
            'Grabando {} · {} canales · vigilancia {}:')
          .format(len(recs), data.get('channels', 0), watching))
    for r in recs:
        print(f'  ● {r["user"]:22} [{r["platform"]:10}] '
              f'{tools.human_duration(r["elapsed"]):>8}  '
              f'{tools.human_size(r["size"]):>9}  {state_label(r["state"])}')
    return 0


def cmd_offset(args: argparse.Namespace) -> int:
    cfg = config_mod.load()
    if args.ms is None:
        print(t('Current audio nudge: {} ms', 'Ajuste de audio actual: {} ms')
              .format(cfg.audio_offset_ms))
        print(t('  It should normally stay at 0: recordings come out in sync on',
                '  Normalmente debe quedarse en 0: las grabaciones salen sincronizadas'))
        print(t('  their own. It applies when converting to MP4, without re-encoding.',
                '  por sí solas. Se aplica al convertir a MP4, sin recodificar.'))
        print(t('  Usage:  offset <ms>   ·   audio ahead → positive (delays it) ; '
                'behind → negative',
                '  Uso:  offset <ms>   ·   adelantado → positivo (lo retrasa) ; '
                'atrasado → negativo'))
        return 0
    cfg.audio_offset_ms = max(-2000, min(2000, int(args.ms)))
    config_mod.save(cfg)
    if cfg.audio_offset_ms > 0:
        effect = t('delays the audio', 'retrasa el audio')
    elif cfg.audio_offset_ms < 0:
        effect = t('pulls the audio forward', 'adelanta el audio')
    else:
        effect = t('no nudge', 'sin ajuste')
    print(t('Audio nudge = {} ms ({}). Applies when captures finishing from now on '
            'are converted to MP4.',
            'Ajuste de audio = {} ms ({}). Se aplica al convertir a MP4 las '
            'grabaciones que terminen a partir de ahora.')
          .format(cfg.audio_offset_ms, effect))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    channel = args.channel.strip()
    everything = channel.lower() in ('all', '*')
    data = status_mod.read()
    if not data or time.time() - data.get('ts', 0) > STALE_AFTER:
        print(t('⚠ The daemon does not look running (recam-cli run). '
                'The order stays queued until it starts.',
                '⚠ El daemon no parece estar corriendo (recam-cli run). '
                'La orden quedará pendiente hasta que arranque.'))
    elif not everything:
        recording = {r['user'].lower() for r in data.get('recording', [])}
        if channel.lower() not in recording:
            print(t('Note: “{}” does not appear to be recording; sending the order anyway.',
                    'Aviso: «{}» no aparece grabando ahora mismo; envío la orden igual.')
                  .format(channel))
    status_mod.send_command('stop', channel=channel)
    target = t('ALL captures', 'TODAS las grabaciones') if everything else f'“{channel}”'
    print(t('Order sent: stop {}. It applies within seconds (finalized to .mp4). '
            'With auto-record ON it resumes after the cooldown; to keep it stopped '
            'use:  auto <channel> off',
            'Orden enviada: parar {}. Se aplica en unos segundos (se finaliza a .mp4). '
            'Con auto-grabar ON volverá tras el enfriamiento; para que no reanude '
            'usa:  auto <canal> off').format(target))
    return 0


def _install_signal_handlers(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    def handler(*_a) -> None:
        loop.call_soon_threadsafe(stop.set)
        with contextlib.suppress(Exception):
            signal.signal(signal.SIGINT, signal.SIG_DFL)   # a second Ctrl+C really quits
    for sig in ('SIGINT', 'SIGTERM', 'SIGBREAK'):          # SIGBREAK = Ctrl+Break
        num = getattr(signal, sig, None)
        if num is not None:
            with contextlib.suppress(Exception):
                signal.signal(num, handler)


def _live_line(monitor: Monitor) -> str:
    parts = [f'{r.streamer.username} {tools.human_duration(r.elapsed)}/'
             f'{tools.human_size(r.size)}' for r in monitor.recordings.values()]
    return t('▶ recording ({}): ', '▶ grabando ({}): ').format(len(parts)) + ' · '.join(parts)


async def _apply_commands(monitor: Monitor) -> None:
    for cmd in status_mod.drain_commands():
        if cmd.get('action') != 'stop':
            continue
        ch = (cmd.get('channel') or '').strip().lower()
        recording = [s for s in monitor.streamers if s.key in monitor.recordings]
        targets = (recording if ch in ('all', '*', '')
                   else [s for s in recording
                         if ch in (s.username.lower(), s.url.lower(), s.key)])
        for s in targets:
            print(t('⏹ stopping {} (external order)…', '⏹ parando {} (orden externa)…')
                  .format(s.username))
            logbook.event(f'ORDER: stop {s.username}')
            await monitor.stop_recording(s)


async def _status_loop(monitor: Monitor, stop: asyncio.Event) -> None:
    """Publish status.json, pick up orders from other terminals, print progress."""
    last_set = None
    ticks = 0
    while not stop.is_set():
        await _apply_commands(monitor)
        status_mod.write(monitor)
        cur = tuple(sorted(monitor.recordings))
        if cur != last_set:
            if cur:
                print(_live_line(monitor))
            elif last_set:
                print(t('· no captures in flight any more',
                        '· ya no hay grabaciones en curso'))
            last_set = cur
        elif cur and ticks % 15 == 0:      # otherwise a progress line every ~30s
            print(_live_line(monitor))
        ticks += 1
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=2)


async def _run() -> None:
    cfg, monitor = _build()
    logbook.enable_console()
    print(t('Recam (CLI) · {} channels · destination: {}',
            'Recam (CLI) · {} canales · destino: {}')
          .format(len(monitor.streamers), cfg.recordings_dir))
    print(t('Checking every {}s · max {} at once · Ctrl+C to stop '
            '(finalizes captures in flight).',
            'Comprobando cada {}s · máx {} a la vez · Ctrl+C para parar '
            '(finaliza las grabaciones en curso).')
          .format(cfg.poll_seconds, cfg.max_concurrent))
    print(t('(test build v0.0.1, Chaturbate only — any feedback helps development)',
            '(versión de prueba v0.0.1, solo Chaturbate — cualquier comentario '
            'ayuda al desarrollo)'))
    logbook.event('CLI: started')
    status_mod.drain_commands()   # anything queued before we started is stale
    await monitor.start()
    await monitor.library.scan()

    stop = asyncio.Event()
    _install_signal_handlers(stop, asyncio.get_running_loop())
    status_task = asyncio.create_task(_status_loop(monitor, stop))
    try:
        await stop.wait()
    finally:
        status_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await status_task
        print(t('\nStopping… finalizing captures in flight (do not force-close)…',
                '\nParando… finalizando grabaciones en curso (no cierres a la fuerza)…'))
        await monitor.shutdown()
        status_mod.write(monitor)
        logbook.event('CLI: stopped')
        print(t('Done.', 'Listo.'))


def cmd_run(_args: argparse.Namespace) -> int:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass   # backstop for when the signal handler could not be installed
    return 0


def cmd_dashboard(_args: argparse.Namespace) -> int:
    from . import tui   # deferred: rich is only needed for the dashboard
    try:
        asyncio.run(tui.run_dashboard())
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    # force UTF-8 out: printing '▶' or '●' blows up on cp1252 when the output is
    # redirected to a file or a service. errors='replace' means it can never crash.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    i18n.set_language(getattr(config_mod.load(), 'language', 'en'))

    parser = argparse.ArgumentParser(
        prog='recam',
        description=t('Recam headless (no GUI): record and manage channels',
                      'Recam headless (sin GUI): grabar y gestionar canales'))
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('run', help=t('watch and record in a loop (daemon, no interface)',
                                 'vigila y graba en bucle (daemon, sin interfaz)'))
    sub.add_parser('dashboard', help=t('live interactive panel (keyboard controls)',
                                       'panel interactivo en vivo (controlar con teclas)'))
    p_add = sub.add_parser('add', help=t('add a channel by URL', 'añade un canal por URL'))
    p_add.add_argument('url')
    p_rm = sub.add_parser('remove', help=t('remove a channel (by user or URL)',
                                           'quita un canal (por usuario o URL)'))
    p_rm.add_argument('channel')
    p_auto = sub.add_parser('auto', help=t('toggle auto-record for a channel',
                                           'activa/desactiva auto-grabar un canal'))
    p_auto.add_argument('channel')
    p_auto.add_argument('state', choices=['on', 'off'])
    p_stop = sub.add_parser('stop', help=t("stop a capture in flight (or 'all')",
                                           "para una grabación en curso (o 'all')"))
    p_stop.add_argument('channel', help=t("user/URL, or 'all' for everything",
                                          "usuario/URL, o 'all' para todas"))
    p_off = sub.add_parser('offset', help=t('view/set the audio nudge in ms',
                                            'ver/ajustar el desfase de audio en ms'))
    p_off.add_argument('ms', nargs='?', type=int,
                       help=t('ms; ahead→positive, behind→negative',
                              'ms; adelantado→positivo, atrasado→negativo'))
    sub.add_parser('list', help=t('list the configured channels',
                                  'lista los canales configurados'))
    sub.add_parser('status', help=t('check who is live right now',
                                    'comprueba quién está en vivo ahora'))
    sub.add_parser('now', help=t('show what is being recorded right now',
                                 'muestra lo que se está grabando ahora mismo'))

    args = parser.parse_args(argv)
    return {
        'run': cmd_run,
        'dashboard': cmd_dashboard,
        'add': cmd_add,
        'remove': cmd_remove,
        'auto': cmd_auto,
        'list': cmd_list,
        'status': cmd_status,
        'now': cmd_now,
        'stop': cmd_stop,
        'offset': cmd_offset,
    }[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
