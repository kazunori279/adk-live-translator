"""Translator package — system instruction + glossary helpers."""

from .agent import (
    LANGUAGES,
    MODEL,
    POPULAR_LANGUAGES,
    build_system_instruction,
    load_default_glossary,
)

__all__ = [
    "LANGUAGES",
    "MODEL",
    "POPULAR_LANGUAGES",
    "build_system_instruction",
    "load_default_glossary",
]
