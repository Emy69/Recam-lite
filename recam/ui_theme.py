"""Shared look and feel, applied by every page (the app and previews alike)."""
from __future__ import annotations

from nicegui import ui

_CSS = '''
body { background: #0b0f14; }
.q-header {
    background: #0e141b !important;
    border-bottom: 1px solid rgba(255, 255, 255, .07);
}
.q-card {
    background: #131a22;
    border-radius: 14px;
    box-shadow: none;
}
.q-card--bordered { border: 1px solid rgba(255, 255, 255, .06); }
.q-tab { text-transform: none; letter-spacing: 0; }
.q-field--outlined .q-field__control:before { border-color: rgba(255, 255, 255, .14); }
.rb-row { transition: background .12s; }
.rb-row:hover { background: rgba(255, 255, 255, .05); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, .14); border-radius: 5px; }
::-webkit-scrollbar-track { background: transparent; }
'''


def apply() -> None:
    ui.colors(primary='#e11d48', positive='#22c55e', negative='#ef4444',
              warning='#f59e0b', info='#38bdf8', dark='#131a22', dark_page='#0b0f14')
    ui.dark_mode(True)
    ui.add_css(_CSS)
