"""Translator system instruction + glossary helpers."""

import csv
import os
from pathlib import Path

POPULAR_LANGUAGES = ["en", "ja", "zh", "es", "fr", "de", "pt", "ko", "hi", "ar"]

LANGUAGES = {
    "af": "Afrikaans",
    "ak": "Akan",
    "sq": "Albanian (Shqip)",
    "am": "Amharic (አማርኛ)",
    "ar": "Arabic (العربية)",
    "hy": "Armenian (Հայերեն)",
    "as": "Assamese (অসমীয়া)",
    "az": "Azerbaijani (Azərbaycan)",
    "eu": "Basque (Euskara)",
    "be": "Belarusian (Беларуская)",
    "bn": "Bengali (বাংলা)",
    "bs": "Bosnian (Bosanski)",
    "bg": "Bulgarian (Български)",
    "my": "Burmese (မြန်မာ)",
    "ca": "Catalan (Català)",
    "ceb": "Cebuano",
    "zh": "Chinese (中文)",
    "hr": "Croatian (Hrvatski)",
    "cs": "Czech (Čeština)",
    "da": "Danish (Dansk)",
    "nl": "Dutch (Nederlands)",
    "en": "English",
    "et": "Estonian (Eesti)",
    "fo": "Faroese (Føroyskt)",
    "fil": "Filipino",
    "fi": "Finnish (Suomi)",
    "fr": "French (Français)",
    "gl": "Galician (Galego)",
    "ka": "Georgian (ქართული)",
    "de": "German (Deutsch)",
    "el": "Greek (Ελληνικά)",
    "gu": "Gujarati (ગુજરાતી)",
    "ha": "Hausa",
    "iw": "Hebrew (עברית)",
    "hi": "Hindi (हिन्दी)",
    "hu": "Hungarian (Magyar)",
    "is": "Icelandic (Íslenska)",
    "id": "Indonesian (Bahasa Indonesia)",
    "ga": "Irish (Gaeilge)",
    "it": "Italian (Italiano)",
    "ja": "Japanese (日本語)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "kk": "Kazakh (Қазақ)",
    "km": "Khmer (ខ្មែរ)",
    "rw": "Kinyarwanda",
    "ko": "Korean (한국어)",
    "ku": "Kurdish (Kurdî)",
    "ky": "Kyrgyz (Кыргызча)",
    "lo": "Lao (ລາວ)",
    "lv": "Latvian (Latviešu)",
    "lt": "Lithuanian (Lietuvių)",
    "mk": "Macedonian (Македонски)",
    "ms": "Malay (Bahasa Melayu)",
    "ml": "Malayalam (മലയാളം)",
    "mt": "Maltese (Malti)",
    "mi": "Maori (Māori)",
    "mr": "Marathi (मराठी)",
    "mn": "Mongolian (Монгол)",
    "ne": "Nepali (नेपाली)",
    "no": "Norwegian (Norsk)",
    "or": "Odia (ଓଡ଼ିଆ)",
    "om": "Oromo",
    "ps": "Pashto (پښتو)",
    "fa": "Persian (فارسی)",
    "pl": "Polish (Polski)",
    "pt": "Portuguese (Português)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)",
    "qu": "Quechua",
    "ro": "Romanian (Română)",
    "rm": "Romansh (Rumantsch)",
    "ru": "Russian (Русский)",
    "sr": "Serbian (Српски)",
    "sd": "Sindhi (سنڌي)",
    "si": "Sinhala (සිංහල)",
    "sk": "Slovak (Slovenčina)",
    "sl": "Slovenian (Slovenščina)",
    "so": "Somali",
    "st": "Southern Sotho (Sesotho)",
    "es": "Spanish (Español)",
    "sw": "Swahili (Kiswahili)",
    "sv": "Swedish (Svenska)",
    "tg": "Tajik (Тоҷикӣ)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "th": "Thai (ไทย)",
    "tn": "Tswana (Setswana)",
    "tr": "Turkish (Türkçe)",
    "tk": "Turkmen (Türkmen)",
    "uk": "Ukrainian (Українська)",
    "ur": "Urdu (اردو)",
    "uz": "Uzbek (Oʻzbek)",
    "vi": "Vietnamese (Tiếng Việt)",
    "cy": "Welsh (Cymraeg)",
    "fy": "Western Frisian (Frysk)",
    "wo": "Wolof",
    "yo": "Yoruba (Yorùbá)",
    "zu": "Zulu (isiZulu)",
}

DICT_PATH = Path(__file__).parent.parent / "dict.csv"

# Glossary entry: (source, target_spoken, transcription_display).
# - source: the term in the source language the model should listen for
# - target_spoken: how the model should pronounce it (drives the audio + the
#   model's own output transcription)
# - transcription_display: how the frontend should render it in the on-screen
#   transcript. Defaults to target_spoken when absent. Server-side this column
#   is purely round-tripped — only source + target_spoken affect the model.
GlossaryEntry = tuple[str, str, str]


def load_default_glossary() -> list[GlossaryEntry]:
    """Read the seed glossary from dict.csv (used when a client sends none)."""
    if not DICT_PATH.exists():
        return []
    entries: list[GlossaryEntry] = []
    with open(DICT_PATH, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            src, tgt = row[0].strip(), row[1].strip()
            if not src or not tgt:
                continue
            disp = row[2].strip() if len(row) >= 3 and row[2].strip() else tgt
            entries.append((src, tgt, disp))
    return entries


def _glossary_section(entries: list[GlossaryEntry]) -> str:
    if not entries:
        return ""
    lines = "\n".join(f"- {src} → {tgt}" for src, tgt, _ in entries)
    return (
        "\n\nUse the following glossary for specific terms. Match the source "
        "term case-insensitively (e.g. \"kubernetes\", \"Kubernetes\", and "
        "\"KUBERNETES\" all match a \"Kubernetes\" entry). When you hear any "
        "of these terms, always use the paired translation:\n"
        + lines
    )


MODEL = os.getenv("DEMO_AGENT_MODEL", "gemini-3.1-flash-live-preview")


def build_system_instruction(
    source_lang: str = "en",
    target_lang: str = "ja",
    glossary_entries: list[GlossaryEntry] | None = None,
) -> str:
    """Build the translator system instruction for the given language pair and glossary."""
    source_name = LANGUAGES.get(source_lang, source_lang)
    target_name = LANGUAGES.get(target_lang, target_lang)
    entries = (
        glossary_entries if glossary_entries is not None else load_default_glossary()
    )
    return (
        f"You are a real-time translator from {source_name} to {target_name}. "
        f"Listen to the incoming audio and immediately output the translated "
        f"version in {target_name}, maintaining the speaker's original tone "
        f"and urgency. "
        f"Translate only the current utterance. Do not repeat, reference, or "
        f"prepend translations from previous turns. Each spoken segment should "
        f"produce exactly one translation of that segment and nothing else."
        + _glossary_section(entries)
    )
