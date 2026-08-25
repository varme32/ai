"""Infer TTS language from config, STT language, or script in the transcript."""

from __future__ import annotations

from typing import Any

# Unicode blocks → Cartesia / ISO 639-1 codes.
_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0C00, 0x0C7F, "te"),  # Telugu
    (0x0900, 0x097F, "hi"),  # Devanagari (Hindi, Marathi, …)
    (0x0B80, 0x0BFF, "ta"),  # Tamil
    (0x0C80, 0x0CFF, "kn"),  # Kannada
    (0x0D00, 0x0D7F, "ml"),  # Malayalam
    (0x0980, 0x09FF, "bn"),  # Bengali
    (0x0A80, 0x0AFF, "gu"),  # Gujarati
    (0x0A00, 0x0A7F, "pa"),  # Gurmukhi / Punjabi
)


def language_from_script(text: str) -> str | None:
    """Return the dominant Indic script language in *text*, if any."""
    counts: dict[str, int] = {}
    for ch in text:
        code = ord(ch)
        for start, end, lang in _SCRIPT_RANGES:
            if start <= code <= end:
                counts[lang] = counts.get(lang, 0) + 1
                break
    if not counts:
        return None
    return max(counts, key=counts.get)


def _base_lang(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().split("-")[0].lower()


def resolve_tts_language(user_config: Any, default: str = "en") -> str:
    """Pick a TTS language code.

    If TTS is left at English but STT is an Indian language (Sarvam te-IN,
    hi-IN, …), inherit that so Telugu greetings are not spoken as English.
    """
    tts_lang = _base_lang(getattr(getattr(user_config, "tts", None), "language", None))
    if tts_lang and tts_lang not in ("en", "auto"):
        return tts_lang

    stt_lang = _base_lang(getattr(getattr(user_config, "stt", None), "language", None))
    if stt_lang and stt_lang not in ("en", "auto", "multi", "unknown"):
        return stt_lang

    return tts_lang or default
