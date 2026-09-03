"""Cartesia TTS with per-utterance language detection for Indic scripts."""

import json

from loguru import logger
from pipecat.services.cartesia.tts import CartesiaTTSService

from api.services.pipecat.tts_language import language_from_script


class DograhCartesiaTTSService(CartesiaTTSService):
    """Cartesia TTS that sets ``language`` per-utterance based on script detection.

    For Indic scripts (Telugu, Hindi, Tamil, etc.) the detected language code is
    sent to Cartesia so it uses the correct phoneme tables.

    For all other text (English, numbers, punctuation) we **explicitly** send
    ``"en"`` so that Cartesia never falls back to whatever the session-level
    language setting is (e.g. ``"te"``).  Without this override, English words
    were processed through Telugu phoneme tables, which caused:

    * All selected voices to sound identical (phoneme constraints override voice
      characteristics when the language is wrong).
    * Speech that sounds rushed or at 2x speed (Telugu phoneme timing applied
      to English word lengths produces clipped, fast output).
    """

    def _build_msg(
        self,
        text: str = "",
        continue_transcript: bool = True,
        add_timestamps: bool = True,
        context_id: str = "",
    ):
        raw = super()._build_msg(
            text=text,
            continue_transcript=continue_transcript,
            add_timestamps=add_timestamps,
            context_id=context_id,
        )
        try:
            msg = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw

        detected = language_from_script(text)
        # Always set an explicit language so Cartesia never silently inherits
        # the session-level language (e.g. "te") for English text.  When Indic
        # script is detected use the detected code; otherwise fall back to "en".
        language = detected or "en"
        msg["language"] = language
        logger.debug(
            f"Cartesia TTS language={language} (detected={detected}) "
            f"sample_rate={self._output_sample_rate} chars={len(text)}"
        )
        return json.dumps(msg)
