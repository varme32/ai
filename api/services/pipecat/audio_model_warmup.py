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


def create_silero_vad_analyzer() -> SileroVADAnalyzer:
    """Construct a Silero VAD analyzer. Safe to call from a worker thread."""
    return SileroVADAnalyzer(params=VADParams(stop_secs=0.2))


async def create_silero_vad_analyzer_async() -> SileroVADAnalyzer:
    """Load Silero VAD off the event loop so call setup stays responsive."""
    return await asyncio.to_thread(create_silero_vad_analyzer)


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

