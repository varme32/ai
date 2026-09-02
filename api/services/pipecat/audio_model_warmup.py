"""Process-level warmup for local audio models used on every call.

Silero VAD loads an ONNX session on first construct. Doing that lazily on
the first answered call blocks the event loop and delays the greeting.
Warm the model once at API startup so later constructors only pay file-cache
cost.

The warmed instance is cached in ``_warmed_vad`` and returned by
``get_warmed_vad()``. Call-setup code reuses this instance instead of
creating a fresh one, eliminating the 300–800 ms ONNX cold load per call.
"""

import asyncio

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

# Module-level singleton — set once during startup warmup
_warmed_vad: SileroVADAnalyzer | None = None

# ---------------------------------------------------------------------------
# Hardened VAD params for enterprise deployments (rooms with multiple speakers).
#
# Problem: Silero VAD defaults (confidence=0.7, min_volume=0.6, start_secs=0.2)
# are tuned for near-field microphones. In a room with multiple speakers,
# background conversations easily exceed these thresholds and fire a
# UserStartedSpeakingFrame, which interrupts the AI mid-sentence.
#
# Fix: Raise all four thresholds so only the primary speaker (who is close
# to and directly addressing the microphone) can trigger a turn change:
#   confidence=0.80  — needs 80% model confidence (vs 70% default)
#   min_volume=0.75  — requires louder, close-field audio (vs 0.6 default)
#   start_secs=0.4   — needs 400 ms of sustained speech (vs 200 ms default)
#   stop_secs=0.5    — waits 500 ms of silence before closing a turn (vs 200 ms)
# ---------------------------------------------------------------------------
ENTERPRISE_VAD_PARAMS = VADParams(
    confidence=0.80,
    min_volume=0.75,
    start_secs=0.4,
    stop_secs=0.5,
)


def create_silero_vad_analyzer(
    params: VADParams | None = None,
) -> SileroVADAnalyzer:
    """Construct a Silero VAD analyzer. Safe to call from a worker thread."""
    return SileroVADAnalyzer(params=params or ENTERPRISE_VAD_PARAMS)


async def create_silero_vad_analyzer_async(
    params: VADParams | None = None,
) -> SileroVADAnalyzer:
    """Load Silero VAD off the event loop so call setup stays responsive."""
    return await asyncio.to_thread(create_silero_vad_analyzer, params)


def get_warmed_vad() -> SileroVADAnalyzer | None:
    """Return the pre-warmed VAD instance, or None if warmup hasn't finished."""
    return _warmed_vad


async def warm_audio_models() -> None:
    """Load local ONNX models into the process so the first call is not cold."""
    global _warmed_vad
    try:
        _warmed_vad = await create_silero_vad_analyzer_async()
        logger.info("Warmed Silero VAD model (singleton cached for reuse)")
    except Exception:
        logger.warning("Failed to warm Silero VAD model", exc_info=True)

