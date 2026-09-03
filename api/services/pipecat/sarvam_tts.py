"""Sarvam HTTP TTS with per-utterance language detection for Indic scripts."""

from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.services.sarvam.tts import SarvamHttpTTSService

from api.services.pipecat.tts_language import language_from_script

# Map the short script-detection codes → Sarvam BCP-47 codes
_SARVAM_LANG_MAP: dict[str, str] = {
    "te": "te-IN",
    "hi": "hi-IN",
    "ta": "ta-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "mr": "mr-IN",
}
_SARVAM_EN = "en-IN"


class DograhSarvamHttpTTSService(SarvamHttpTTSService):
    """Sarvam HTTP TTS with per-utterance language detection.

    For Indic scripts (Telugu, Hindi, Tamil, etc.) the detected Sarvam language
    code is injected into the ``target_language_code`` field of every request.

    For all other text (English, numbers, punctuation) we **explicitly** send
    ``"en-IN"`` so that Sarvam never inherits the session-level language setting
    (e.g. ``"te-IN"``).  Without this override, English words were processed
    through Telugu phoneme tables, which caused voices to sound identical and
    speech to sound rushed or at 2x speed.
    """

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        detected = language_from_script(text)
        language = _SARVAM_LANG_MAP.get(detected, _SARVAM_EN) if detected else _SARVAM_EN

        logger.debug(
            f"Sarvam HTTP TTS language={language} (detected={detected}) chars={len(text)}"
        )

        # Temporarily override the language setting for this utterance
        original_language = self._settings.language
        self._settings.language = language
        try:
            async for frame in super().run_tts(text, context_id):
                yield frame
        finally:
            self._settings.language = original_language
