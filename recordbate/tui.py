"""Live dashboard in the terminal: see everything and drive it with the keyboard.

Runs the engine in this process, exactly like the GUI does. Do not leave it up
next to `recordbate-cli run` or both would record the same channels.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from . import config as config_mod
from . import status as status_mod
from . import tools
from .library import Library
from .models import Status
from .monitor import Monitor

_STATE_LABELS = {
    Status.RECORDING: ('● GRABANDO', 'bold red'),
    Status.ONLINE: ('EN VIVO', 'bold green'),
    Status.OFFLINE: ('offline', 'grey50'),
    Status.UNKNOWN: ('—', 'grey50'),
}
_PLAT_COLOR = {'twitch': 'magenta', 'kick': 'green',
               'stripchat': 'red', 'chaturbate': 'yellow'}


class _KeyReader:
    """Non-blocking key reads: msvcrt on Windows, cbreak + select elsewhere."""

    def __init__(self) -> None:
        self.posix = False
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            self.posix = True
            import termios
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)

    def start(self) -> None:
        if self.posix:
            import tty
            tty.setcbreak(self._fd)

    def stop(self) -> None:
        if self.posix:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    def poll(self) -> str | None:
        if not self.posix:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):   # prefix of an extended key; the code follows
                return {'H': 'UP', 'P': 'DOWN'}.get(msvcrt.getwch(), '')
            return ch
        import select
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            if select.select([sys.stdin], [], [], 0.001)[0]:
                return {'[A': 'UP', '[B': 'DOWN'}.get(sys.stdin.read(2), 'ESC')
            return 'ESC'
        return ch


def _render(monitor: Monitor, cfg: config_mod.Config, selected: int) -> Group:
    streamers = monitor.streamers
    live = sum(1 for s in streamers if s.status in (Status.ONLINE, Status.RECORDING))
    header = Text.assemble(
        ('  RecordBate  ', 'bold white on dark_red'),
        (f'  {len(streamers)} canales · {live} en vivo · {len(monitor.recordings)} grabando'
         f' · vigilancia ', 'white'),
        ('ON' if monitor.enabled else 'OFF',
         'bold green' if monitor.enabled else 'bold red'),
        (f'   ·   {cfg.recordings_dir}', 'grey50'),
    )
    table = Table(expand=True, show_edge=False, pad_edge=False)
    table.add_column('#', width=3, justify='right')
    table.add_column('Plataforma', width=11)
    table.add_column('Canal', ratio=2, no_wrap=True)
    table.add_column('Estado', width=11)
    table.add_column('Auto', width=6, justify='center')
    table.add_column('En curso / info', ratio=3, no_wrap=True)

    if not streamers:
        table.add_row('', '', Text('(sin canales — pulsa + para añadir uno)', style='grey58'),
                      '', '', '')
    for i, s in enumerate(streamers):
        rec = monitor.recordings.get(s.key)
        state_txt, state_style = _STATE_LABELS.get(s.status, ('—', 'grey50'))
        auto = (Text('● ON', style='bold green') if s.auto_record
                else Text('○ off', style='grey42'))
        if rec:
            info = Text(f'{tools.human_duration(rec.elapsed)} · '
                        f'{tools.human_size(rec.size)} · {rec.state}', style='red')
        elif s.last_error:
            info = Text('⚠ ' + s.last_error[:60], style='yellow')
        elif s.last_result:
            info = Text(s.last_result[:60], style='grey58')
        else:
            cd = s.cooldown_until - time.time()
            info = Text(f'pausa {int(cd / 60)} min' if cd > 90 else '', style='grey42')
        num = Text(('▶' if i == selected else ' ') + str(i + 1),
                   style='bold cyan' if i == selected else 'grey58')
        table.add_row(num, Text(s.platform, style=_PLAT_COLOR.get(s.platform, 'white')),
                      s.username, Text(state_txt, style=state_style), auto, info,
                      style='on grey15' if i == selected else '')

    footer = Text('  [↑↓/1-9] seleccionar   [+] añadir   [espacio] auto on/off   [A] todos   '
                  '[r] grabar ya   [s] parar sel.   [x] parar todo   [supr] quitar   '
                  '[v] vigilancia   [q] salir  ', style='dim')
    return Group(header, Text(''), table, Text(''), footer)


async def _handle_key(key: str, monitor: Monitor, selected: list[int]) -> str | None:
    """Act on one keypress. Returns 'quit', 'add', or None."""
    n = len(monitor.streamers)
    if key in ('q', 'Q', 'ESC', '\x03'):
        return 'quit'
    if key in ('+', 'n', 'N'):
        return 'add'
    if n == 0:
        return None
    if key == 'UP':
        selected[0] = (selected[0] - 1) % n
    elif key == 'DOWN':
        selected[0] = (selected[0] + 1) % n
    elif key.isdigit() and key != '0':
        if int(key) - 1 < n:
            selected[0] = int(key) - 1
    else:
        selected[0] = min(selected[0], n - 1)
        s = monitor.streamers[selected[0]]
        if key == ' ':
            s.auto_record = not s.auto_record
            monitor.persist()
        elif key in ('a', 'A'):
            target = not all(x.auto_record for x in monitor.streamers)
            for x in monitor.streamers:
                x.auto_record = target
            monitor.persist()
        elif key in ('r', 'R'):
            s.cooldown_until = 0
            await monitor.start_recording(s)
        elif key in ('s', 'S'):
            await monitor.stop_recording(s)
        elif key in ('x', 'X'):
            for x in list(monitor.streamers):
                if x.key in monitor.recordings:
                    await monitor.stop_recording(x)
        elif key in ('\x7f', '\x08'):    # Supr / Backspace
            await monitor.remove_streamer(s)
            selected[0] = max(0, min(selected[0], len(monitor.streamers) - 1))
        elif key in ('v', 'V'):
            monitor.enabled = not monitor.enabled
    return None


async def _add_flow(live: Live, reader: _KeyReader, monitor: Monitor,
                    console: Console) -> None:
    """Drop out of the live view, ask for a URL, add it, come back."""
    live.stop()
    reader.stop()
    try:
        console.print()
        url = input('  Pega la URL del canal (Enter vacío = cancelar): ').strip()
        if url:
            try:
                s = monitor.add_streamer(url)
                console.print(f'  [green]✓ Añadido: {s.username} ({s.platform})[/]')
            except ValueError as exc:
                console.print(f'  [red]✗ {exc}[/]')
            await asyncio.sleep(1.3)
    finally:
        reader.start()
        live.start()


async def run_dashboard() -> None:
    cfg = config_mod.load()
    data = status_mod.read()
    if data and time.time() - data.get('ts', 0) < 15:
        print('⚠ Parece que ya hay un daemon o la GUI corriendo '
              '(data/status.json está fresco).')
        print('  Ciérralo antes de abrir el panel para no grabar por duplicado.')
        print('  ¿Continuar de todas formas? [s/N] ', end='', flush=True)
        if (input().strip().lower() or 'n') not in ('s', 'si', 'sí', 'y'):
            return

    monitor = Monitor(cfg, config_mod.load_streamers(), Library(cfg))
    await monitor.start()
    await monitor.library.scan()

    selected = [0]
    reader = _KeyReader()
    console = Console()
    live = Live(_render(monitor, cfg, 0), console=console, screen=True, auto_refresh=False)
    stop = False
    reader.start()
    live.start()
    try:
        while not stop:
            live.update(_render(monitor, cfg, selected[0]), refresh=True)
            status_mod.write(monitor)
            # drain every pending key each turn, otherwise held arrows lag behind
            while (key := reader.poll()) is not None:
                action = await _handle_key(key, monitor, selected)
                if action == 'quit':
                    stop = True
                    break
                if action == 'add':
                    await _add_flow(live, reader, monitor, console)
            await asyncio.sleep(0.1)
    finally:
        with contextlib.suppress(Exception):
            live.stop()
        reader.stop()
        print('Cerrando… finalizando grabaciones en curso (no cierres a la fuerza)…')
        await monitor.shutdown()
        status_mod.write(monitor)
        print('Listo.')
