"""Real-time translator agent definition."""

import csv
import os
from pathlib import Path

from google.adk.agents import Agent

LANGUAGES = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ko": "Korean",
    "hi": "Hindi",
    "ar": "Arabic",
}

# Load translation dictionary from CSV
_dict_path = Path(__file__).parent.parent / "dict.csv"
_glossary_lines = []
if _dict_path.exists():
    with open(_dict_path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                _glossary_lines.append(f"- {row[0]} → {row[1]}")

_glossary_section = ""
if _glossary_lines:
    _glossary_section = (
        "\n\nUse the following glossary for specific terms. "
        "When you hear these words, always use the paired translation:\n"
        + "\n".join(_glossary_lines)
    )

MODEL = os.getenv("DEMO_AGENT_MODEL", "gemini-3.1-flash-live-preview")


def create_agent(source_lang: str = "en", target_lang: str = "ja") -> Agent:
    """Create a translator agent for the given language pair."""
    source_name = LANGUAGES.get(source_lang, source_lang)
    target_name = LANGUAGES.get(target_lang, target_lang)
    return Agent(
        name="live_translator",
        model=MODEL,
        instruction=(
            f"You are a real-time translator from {source_name} to {target_name}. "
            f"Listen to the incoming audio and immediately output the translated "
            f"version in {target_name}, maintaining the speaker's original tone "
            f"and urgency."
            + _glossary_section
        ),
    )


# Default agent for backward compatibility
agent = create_agent()
