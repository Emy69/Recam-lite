"""Bridge for pywebview's 'closing' event, which NiceGUI does not forward.

NiceGUI runs the native window in a separate spawned process, and the spawn
target is pickled by reference. install() points that target at open_window
below, so the child process imports THIS module and runs it: it wraps
webview.create_window to attach a 'closing' handler that vetoes the native
close and notifies the parent instead, then hands control back to NiceGUI's
own window bootstrap. The parent listens with app.native.on('closing', ...)
and decides in-page what happens: ask, hide to the tray, or quit.

The original bootstrap is captured at import time, before install() can patch
it. That matters because spawn re-imports the app's main module in the child,
which runs install() there too — resolving the original lazily would then find
our own wrapper and recurse forever.
"""
from __future__ import annotations

import contextlib
from typing import Any

from nicegui.native import native_mode as _native_mode

_original_open_window = _native_mode._open_window


def install() -> None:
    """Reroute the native window bootstrap. Must be called before ui.run."""
    _native_mode._open_window = open_window


def allow_close() -> None:
    """Disarm the veto; call right before app.shutdown().

    Shutting down destroys the window with Close(), which fires the same
    FormClosing our veto intercepts — without this, quitting would cancel
    its own window teardown. The message rides the regular method queue, so
    it reaches the window process before the destroy that follows it.
    """
    with contextlib.suppress(Exception):
        from nicegui.native import native
        if native.method_queue is not None:
            native.method_queue.put(('rb_allow_close', (), {}))


def open_window(*args: Any, **kwargs: Any) -> None:
    """Drop-in replacement for nicegui's _open_window; runs IN THE WINDOW PROCESS."""
    import webview

    # duck-typed on purpose: on Windows the pipe is a PipeConnection, which is NOT
    # an instance of multiprocessing.connection.Connection
    event_sender = next((a for a in args
                         if hasattr(a, 'send') and hasattr(a, 'recv')
                         and hasattr(a, 'fileno')), None)
    real_create_window = webview.create_window

    def create_window(*a: Any, **k: Any):
        window = real_create_window(*a, **k)
        window._rb_close_allowed = False
        # reachable from the app through the window method queue
        window.rb_allow_close = lambda: setattr(window, '_rb_close_allowed', True)

        def on_closing() -> bool:
            if getattr(window, '_rb_close_allowed', False):
                return True   # the app is shutting down on purpose
            if event_sender is None:
                return True
            try:
                event_sender.send({'type': 'closing', 'args': {}})
            except OSError:
                return True   # the app is gone; let the window close for real
            return False      # veto: the app shows its own close dialog

        try:
            window.events.closing += on_closing
        except Exception:
            pass   # this pywebview has no closing event: keep default behavior
        return window

    try:
        webview.create_window = create_window
        _original_open_window(*args, **kwargs)
    finally:
        webview.create_window = real_create_window
