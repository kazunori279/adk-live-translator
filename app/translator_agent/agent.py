"""Real-time translator agent definition."""

import csv
import os
from pathlib import Path

from google.adk.agents import Agent

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
