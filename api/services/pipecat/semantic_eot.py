"""Heuristic semantic end-of-turn for cascaded voice pipelines.

Classifies the current partial transcript as complete or incomplete and
picks a short vs long speech timeout. This is intentionally a transcript
heuristic, not an extra LLM round-trip — LLM-gated EOT would add latency
on the critical path.
"""

from __future__ import annotations

import re

from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)

_TERMINAL_PUNCTUATION = (".", "?", "!", "।", "؟")
_TRAILING_INCOMPLETE = frozenset(
    {
        "and",
        "or",
        "but",
        "so",
        "because",
        "if",
        "when",
        "while",
        "although",
        "the",
        "a",
        "an",
        "to",
        "for",
        "with",
        "of",
        "my",
        "your",
        "our",
        "i",
        "im",
        "i'm",
        "its",
        "it's",
        "that",
        "this",
        "just",
        "like",
        "um",
        "uh",
        "well",
        "actually",
        "wanna",
        "gonna",
    }
)
_INCOMPLETE_BIGRAMS = frozenset(
    {
        "i want",
        "i need",
        "i was",
        "i am",
        "im going",
        "going to",
        "want to",
        "need to",
        "trying to",
        "have to",
        "able to",
        "because i",
        "and i",
    }
)
_COMPLETE_SHORT = frozenset(
    {
        "yes",
        "no",
        "yeah",
        "yep",
        "yup",
        "nah",
        "nope",
        "ok",
        "okay",
        "sure",
        "thanks",
        "thank you",
        "bye",
        "hello",
        "hi",
        "correct",
        "wrong",
        "done",
        "wait",
        "stop",
        "haan",
        "han",
        "nahi",
        "ji",
    }
)
_NORMALIZE_RE = re.compile(r"[^\w\s']+", re.UNICODE)


def normalize_utterance(text: str) -> str:
    collapsed = " ".join(text.strip().lower().split())
    return _NORMALIZE_RE.sub("", collapsed).strip()


def is_complete_utterance(text: str) -> bool:
    """Return True when the transcript looks like a finished turn.

    Empty text is incomplete so we wait for STT before snapping the turn
    closed on VAD stop alone.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith(("...", "…", ",", "-", "—", ";")):
        return False
    if stripped.endswith(_TERMINAL_PUNCTUATION):
        return True

    normalized = normalize_utterance(stripped)
    if not normalized:
        return False
    if normalized in _COMPLETE_SHORT:
        return True

    words = normalized.split()
    last = words[-1]
    if last in _TRAILING_INCOMPLETE:
        return False
    if len(words) >= 2 and " ".join(words[-2:]) in _INCOMPLETE_BIGRAMS:
        return False
    return True


class SemanticEotUserTurnStopStrategy(SpeechTimeoutUserTurnStopStrategy):
    """Speech-timeout stop strategy with complete vs incomplete waits."""

    def __init__(
        self,
        *,
        complete_timeout_secs: float = 0.3,
        incomplete_timeout_secs: float = 1.2,
        wait_for_transcript: bool = True,
        **kwargs,
    ):
        super().__init__(
            user_speech_timeout=complete_timeout_secs,
            wait_for_transcript=wait_for_transcript,
            **kwargs,
        )
        self._complete_timeout_secs = complete_timeout_secs
        self._incomplete_timeout_secs = max(
            incomplete_timeout_secs, complete_timeout_secs
        )

    def _timeout_for_current_text(self) -> float:
        if is_complete_utterance(self._text):
            return self._complete_timeout_secs
        return self._incomplete_timeout_secs

    async def _restart_user_speech_timer(self):
        if self._user_speech_timeout_task:
            await self.task_manager.cancel_task(self._user_speech_timeout_task)
            self._user_speech_timeout_task = None
        self._user_speech_wait_done = False
        timeout = self._timeout_for_current_text()
        self._user_speech_timeout_task = self.task_manager.create_task(
            self._user_speech_timeout_handler(timeout),
            f"{self}::_user_speech_timeout_handler",
        )
