"""Smallest TTS with per-utterance language detection for Indic scripts."""

from loguru import logger
from pipecat.services.smallest.tts import SmallestTTSService

from api.services.pipecat.tts_language import language_from_script


class DograhSmallestTTSService(SmallestTTSService):
    """Smallest TTS that sets ``language`` per-utterance based on script detection.

    For Indic scripts (Telugu, Hindi, Tamil, etc.) the detected language code is
    used so Smallest applies the correct phoneme tables.

    For all other text (English, numbers, punctuation) we **explicitly** send
    ``"en"`` so that Smallest never inherits the session-level language setting
    (e.g. ``"te"``).  Without this override, English words were processed through
    Telugu phoneme tables, which caused voices to sound identical and speech to
    sound rushed or at 2x speed.
    """

    def _build_msg(self, text: str) -> dict:
        msg = super()._build_msg(text=text)

        detected = language_from_script(text)
        # Always set an explicit language so Smallest never silently inherits
        # the session-level language (e.g. "te") for English text.
        language = detected or "en"
        msg["language"] = language
        logger.debug(
            f"Smallest TTS language={language} (detected={detected}) chars={len(text)}"
        )
        return msg
