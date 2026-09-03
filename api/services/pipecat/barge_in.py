"""Transcription-gated barge-in that ignores short backchannels."""

from __future__ import annotations

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)

from api.services.pipecat.semantic_eot import normalize_utterance


class IgnorePhraseUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    """Min-words start strategy that also drops listed backchannels.

    Ignore phrases are only applied while the bot is speaking. When the bot
    is silent, a one-word answer such as "okay" still starts the user turn.
    """

    def __init__(
        self,
        *,
        min_words: int,
        ignore_phrases: list[str],
        use_interim: bool = True,
        **kwargs,
    ):
        super().__init__(min_words=min_words, use_interim=use_interim, **kwargs)
        self._ignore = {
            normalize_utterance(phrase) for phrase in ignore_phrases if phrase.strip()
        }
        self._ignore.discard("")

    async def _handle_transcription(
        self, frame: TranscriptionFrame | InterimTranscriptionFrame
    ) -> ProcessFrameResult:
        if self._bot_speaking:
            normalized = normalize_utterance(frame.text)
            if normalized and normalized in self._ignore:
                await self.trigger_reset_aggregation()
                return ProcessFrameResult.CONTINUE
        return await super()._handle_transcription(frame)
