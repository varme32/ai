"""Resolve per-agent voice-runtime knobs from workflow_configurations.

Defaults match the previous hardcoded production values so existing agents
do not change behavior until the user overrides them in agent settings.
"""

from __future__ import annotations

from typing import Any

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from api.schemas.workflow_configurations import (
    DEFAULT_FLUX_EAGER_EOT_THRESHOLD,
    DEFAULT_FLUX_EOT_THRESHOLD,
    DEFAULT_FLUX_EOT_TIMEOUT_MS,
    DEFAULT_REALTIME_PREFIX_PADDING_MS,
    DEFAULT_REALTIME_SILENCE_DURATION_MS,
    DEFAULT_SEMANTIC_EOT_COMPLETE_TIMEOUT_SECS,
    DEFAULT_SEMANTIC_EOT_INCOMPLETE_TIMEOUT_SECS,
    DEFAULT_SPEECH_TIMEOUT_SECS,
    DEFAULT_STT_ENDPOINTING_MS,
    DEFAULT_VAD_CONFIDENCE,
    DEFAULT_VAD_MIN_VOLUME,
    DEFAULT_VAD_START_SECS,
    DEFAULT_VAD_STOP_SECS,
    DEFAULT_BARGE_IN_IGNORE_PHRASES,
    DEFAULT_TOOL_FILLER_DELAY_MS,
    DEFAULT_TOOL_FILLER_PHRASES,
)


def _section(run_configs: dict | None, key: str) -> dict[str, Any]:
    if not run_configs:
        return {}
    value = run_configs.get(key) or {}
    return value if isinstance(value, dict) else {}


def _float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def resolve_vad_params(run_configs: dict | None) -> VADParams:
    vad = _section(run_configs, "vad_configuration")
    return VADParams(
        confidence=_float(
            vad.get("confidence", DEFAULT_VAD_CONFIDENCE),
            DEFAULT_VAD_CONFIDENCE,
            minimum=0.1,
            maximum=1.0,
        ),
        min_volume=_float(
            vad.get("min_volume", DEFAULT_VAD_MIN_VOLUME),
            DEFAULT_VAD_MIN_VOLUME,
            minimum=0.0,
            maximum=1.0,
        ),
        start_secs=_float(
            vad.get("start_secs", DEFAULT_VAD_START_SECS),
            DEFAULT_VAD_START_SECS,
            minimum=0.05,
            maximum=2.0,
        ),
        stop_secs=_float(
            vad.get("stop_secs", DEFAULT_VAD_STOP_SECS),
            DEFAULT_VAD_STOP_SECS,
            minimum=0.05,
            maximum=2.0,
        ),
    )


def vad_params_match(left: VADParams, right: VADParams) -> bool:
    return (
        left.confidence == right.confidence
        and left.min_volume == right.min_volume
        and left.start_secs == right.start_secs
        and left.stop_secs == right.stop_secs
    )


def should_reuse_warmed_vad(
    warmed: SileroVADAnalyzer | None, params: VADParams
) -> bool:
    if warmed is None:
        return False
    return vad_params_match(warmed._params, params)


def resolve_speech_timeout_secs(run_configs: dict | None) -> float:
    if not run_configs:
        return DEFAULT_SPEECH_TIMEOUT_SECS
    return _float(
        run_configs.get("speech_timeout_secs", DEFAULT_SPEECH_TIMEOUT_SECS),
        DEFAULT_SPEECH_TIMEOUT_SECS,
        minimum=0.05,
        maximum=2.0,
    )


def resolve_semantic_eot_timeouts(run_configs: dict | None) -> tuple[float, float]:
    eot = _section(run_configs, "semantic_eot_configuration")
    complete = _float(
        eot.get("complete_timeout_secs", DEFAULT_SEMANTIC_EOT_COMPLETE_TIMEOUT_SECS),
        DEFAULT_SEMANTIC_EOT_COMPLETE_TIMEOUT_SECS,
        minimum=0.1,
        maximum=2.0,
    )
    incomplete = _float(
        eot.get(
            "incomplete_timeout_secs", DEFAULT_SEMANTIC_EOT_INCOMPLETE_TIMEOUT_SECS
        ),
        DEFAULT_SEMANTIC_EOT_INCOMPLETE_TIMEOUT_SECS,
        minimum=0.3,
        maximum=3.0,
    )
    if incomplete < complete:
        incomplete = complete
    return complete, incomplete


def resolve_stt_turn_settings(run_configs: dict | None) -> dict[str, float | int]:
    stt = _section(run_configs, "stt_turn_configuration")
    return {
        "endpointing_ms": _int(
            stt.get("endpointing_ms", DEFAULT_STT_ENDPOINTING_MS),
            DEFAULT_STT_ENDPOINTING_MS,
            minimum=50,
            maximum=2000,
        ),
        "flux_eot_timeout_ms": _int(
            stt.get("flux_eot_timeout_ms", DEFAULT_FLUX_EOT_TIMEOUT_MS),
            DEFAULT_FLUX_EOT_TIMEOUT_MS,
            minimum=100,
            maximum=3000,
        ),
        "flux_eot_threshold": _float(
            stt.get("flux_eot_threshold", DEFAULT_FLUX_EOT_THRESHOLD),
            DEFAULT_FLUX_EOT_THRESHOLD,
            minimum=0.1,
            maximum=1.0,
        ),
        "flux_eager_eot_threshold": _float(
            stt.get("flux_eager_eot_threshold", DEFAULT_FLUX_EAGER_EOT_THRESHOLD),
            DEFAULT_FLUX_EAGER_EOT_THRESHOLD,
            minimum=0.1,
            maximum=1.0,
        ),
    }


def resolve_realtime_vad_ms(run_configs: dict | None) -> tuple[int, int]:
    vad = _section(run_configs, "vad_configuration")
    return (
        _int(
            vad.get("realtime_prefix_padding_ms", DEFAULT_REALTIME_PREFIX_PADDING_MS),
            DEFAULT_REALTIME_PREFIX_PADDING_MS,
            minimum=0,
            maximum=1000,
        ),
        _int(
            vad.get(
                "realtime_silence_duration_ms", DEFAULT_REALTIME_SILENCE_DURATION_MS
            ),
            DEFAULT_REALTIME_SILENCE_DURATION_MS,
            minimum=50,
            maximum=2000,
        ),
    )


def resolve_barge_in_ignore_phrases(run_configs: dict | None) -> list[str] | None:
    """Return ignore phrases when the filter is enabled, otherwise None."""
    barge_in = _section(run_configs, "barge_in_filter")
    if not barge_in.get("enabled"):
        return None
    phrases = barge_in.get("ignore_phrases")
    if not isinstance(phrases, list) or not phrases:
        return list(DEFAULT_BARGE_IN_IGNORE_PHRASES)
    cleaned: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        normalized = " ".join(str(phrase).strip().lower().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned or list(DEFAULT_BARGE_IN_IGNORE_PHRASES)


def resolve_tool_filler_config(run_configs: dict | None) -> dict[str, Any] | None:
    filler = _section(run_configs, "tool_filler_configuration")
    if not filler.get("enabled"):
        return None
    phrases = filler.get("phrases")
    if not isinstance(phrases, list) or not phrases:
        phrases = list(DEFAULT_TOOL_FILLER_PHRASES)
    cleaned = [" ".join(str(p).split()).strip() for p in phrases]
    cleaned = [p for p in cleaned if p]
    return {
        "enabled": True,
        "delay_ms": _int(
            filler.get("delay_ms", DEFAULT_TOOL_FILLER_DELAY_MS),
            DEFAULT_TOOL_FILLER_DELAY_MS,
            minimum=100,
            maximum=5000,
        ),
        "phrases": cleaned or list(DEFAULT_TOOL_FILLER_PHRASES),
    }
