from loguru import logger
from pipecat.frames.frames import TranscriptionFrame
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.utils.time import time_now_iso8601


class DograhSarvamSTTService(SarvamSTTService):
    """SarvamSTTService with a language-confidence gate for telephony calls.

    On 8 kHz PSTN audio, Sarvam occasionally emits transcripts that are
    clearly noise-induced (e.g. "in the sense" at 18.5% language
    confidence while the caller is speaking Telugu). Without a filter,
    these reach the turn-start strategy and trigger spurious LLM calls or
    interrupt the bot mid-sentence.

    Any transcript whose language_probability is below
    MIN_LANGUAGE_PROBABILITY (0.4) is silently discarded; all others
    pass through exactly as the base class would deliver them.
    """

    # Lowered from 0.4 to 0.30: on 8 kHz PSTN audio, Sarvam often returns
    # legitimate Telugu utterances with 35–45% confidence because the model
    # is ambiguous between Telugu and Kannada (closely related scripts/phonology).
    # The previous 0.4 threshold was silently dropping real user speech.
    MIN_LANGUAGE_PROBABILITY = 0.30

    async def _handle_message(self, message):
        """Override to apply language-confidence gate before emitting frames."""
        # Delegate all non-data messages (VAD events etc.) to the base class.
        if getattr(message, "type", None) != "data":
            return await super()._handle_message(message)

        try:
            transcript = message.data.transcript
            language_code = message.data.language_code

            if language_code:
                language = self._map_language_code_to_enum(language_code)
            else:
                language_string = self._get_language_string()
                if language_string:
                    language = self._map_language_code_to_enum(language_string)
                else:
                    from pipecat.transcriptions.language import Language
                    language = Language.HI_IN

            await self._call_event_handler("on_utterance_end")

            if transcript and transcript.strip():
                language_probability = (
                    getattr(message.data, "language_probability", 1.0) or 1.0
                )

                if language_probability < self.MIN_LANGUAGE_PROBABILITY:
                    logger.warning(
                        f"DograhSarvamSTTService: discarding low-confidence transcript "
                        f"(language_probability={language_probability:.3f} "
                        f"< {self.MIN_LANGUAGE_PROBABILITY}): "
                        f"'{transcript}' [{language_code}]"
                    )
                else:
                    await self._handle_transcription(transcript, True, language)
                    await self.push_frame(
                        TranscriptionFrame(
                            transcript,
                            self._user_id,
                            time_now_iso8601(),
                            language,
                            result=(
                                message.dict()
                                if hasattr(message, "dict")
                                else str(message)
                            ),
                        )
                    )

            await self.stop_processing_metrics()

        except Exception as e:
            await self.push_error(error_msg=f"Failed to handle message: {e}", exception=e)
            await self.stop_all_metrics()
