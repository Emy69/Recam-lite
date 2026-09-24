"""Tiny runtime translation: every user-facing string carries both languages.

t('English text', 'Texto en español') returns the variant matching the
configured language. English is the default. The log file stays English
regardless of the setting, since it is diagnostics rather than UI.
"""
from __future__ import annotations

current = 'en'


def set_language(lang: str | None) -> None:
    global current
    current = 'es' if str(lang or '').lower().startswith('es') else 'en'


def t(en: str, es: str) -> str:
    return es if current == 'es' else en
