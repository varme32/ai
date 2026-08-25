"""Cartesia TTS with per-utterance language detection for Indic scripts."""

import json

from loguru import logger
from pipecat.services.cartesia.tts import CartesiaTTSService

from api.services.pipecat.tts_language import language_from_script


class DograhCartesiaTTSService(CartesiaTTSService):
    """Cartesia TTS that sets ``language`` from Telugu/Hindi/etc. in the text.

    Cartesia speaks Indic script as English phonemes when language is ``en``,
    which is unintelligible on the call.
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
        if detected:
            msg["language"] = detected
            logger.debug(
                f"Cartesia TTS language={detected} sample_rate={self._output_sample_rate} "
                f"chars={len(text)}"
            )
        return json.dumps(msg)
