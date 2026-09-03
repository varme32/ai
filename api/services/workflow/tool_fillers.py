"""Speak a short filler when a tool call is still running after a delay."""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


@asynccontextmanager
async def tool_filler(
    engine: "PipecatEngine", *, skip: bool = False
) -> AsyncIterator[None]:
    """Yield while a delayed filler task is armed, then cancel it.

    No-ops when fillers are disabled, skipped (the tool already spoke), or
    the engine has no pipeline task yet.
    """
    config = getattr(engine, "_tool_filler_configuration", None)
    pipeline_task = getattr(engine, "task", None)
    if (
        skip
        or not isinstance(config, dict)
        or not config.get("enabled")
        or pipeline_task is None
        or not callable(getattr(pipeline_task, "queue_frame", None))
    ):
        yield
        return

    try:
        delay_ms = int(config.get("delay_ms", 600))
    except (TypeError, ValueError):
        yield
        return
    phrases = [p for p in (config.get("phrases") or []) if isinstance(p, str) and p]
    if delay_ms <= 0 or not phrases:
        yield
        return

    async def _speak_later() -> None:
        try:
            await asyncio.sleep(delay_ms / 1000)
        except asyncio.CancelledError:
            return
        phrase = random.choice(phrases)
        try:
            engine._queued_speech_mute_state = "waiting"
            await pipeline_task.queue_frame(
                TTSSpeakFrame(
                    phrase,
                    append_to_context=False,
                    persist_to_logs=True,
                )
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Failed to queue tool-call filler", exc_info=True)

    filler_task = asyncio.create_task(_speak_later())
    try:
        yield
    finally:
        if not filler_task.done():
            filler_task.cancel()
            try:
                await filler_task
            except asyncio.CancelledError:
                pass
