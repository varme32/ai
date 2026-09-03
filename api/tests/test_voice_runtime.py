import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)

from api.schemas.workflow_configurations import DEFAULT_SPEECH_TIMEOUT_SECS
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.barge_in import IgnorePhraseUserTurnStartStrategy
from api.services.pipecat.run_pipeline import (
    _create_non_realtime_user_turn_start_strategies,
    _create_non_realtime_user_turn_stop_strategies,
    _create_realtime_user_turn_config,
    _resolve_user_turn_stop_timeout,
)
from api.services.pipecat.semantic_eot import (
    SemanticEotUserTurnStopStrategy,
    is_complete_utterance,
)
from api.services.pipecat.service_factory import create_stt_service
from api.services.pipecat.voice_runtime import (
    resolve_barge_in_ignore_phrases,
    resolve_speech_timeout_secs,
    resolve_stt_turn_settings,
    resolve_tool_filler_config,
    resolve_vad_params,
    should_reuse_warmed_vad,
)
from api.services.workflow.tool_fillers import tool_filler


def test_is_complete_utterance_detects_finished_and_trailing_speech():
    assert is_complete_utterance("is that okay?") is True
    assert is_complete_utterance("Yes") is True
    assert is_complete_utterance("I want a large pizza.") is True
    assert is_complete_utterance("because I want") is False
    assert is_complete_utterance("I want to") is False
    assert is_complete_utterance("I want to order the") is False
    assert is_complete_utterance("I was thinking...") is False
    assert is_complete_utterance("") is False


def test_resolve_vad_params_uses_production_defaults():
    params = resolve_vad_params({})
    assert params.confidence == 0.7
    assert params.min_volume == 0.6
    assert params.start_secs == 0.15
    assert params.stop_secs == 0.25


def test_resolve_vad_params_reads_workflow_overrides():
    params = resolve_vad_params(
        {"vad_configuration": {"confidence": 0.9, "stop_secs": 0.4}}
    )
    assert params.confidence == 0.9
    assert params.stop_secs == 0.4
    assert params.start_secs == 0.15


def test_should_reuse_warmed_vad_only_when_params_match():
    warmed = SimpleNamespace(_params=resolve_vad_params({}))
    assert should_reuse_warmed_vad(warmed, resolve_vad_params({})) is True
    assert (
        should_reuse_warmed_vad(
            warmed, VADParams(confidence=0.9, min_volume=0.6, start_secs=0.15, stop_secs=0.25)
        )
        is False
    )


def test_null_user_turn_stop_timeout_keeps_platform_default():
    assert (
        _resolve_user_turn_stop_timeout(
            {"user_turn_stop_timeout": None}, uses_external_turns=True
        )
        == 30.0
    )


def test_speech_timeout_override_is_clamped():
    assert resolve_speech_timeout_secs({"speech_timeout_secs": 0.45}) == 0.45
    assert resolve_speech_timeout_secs({"speech_timeout_secs": 9}) == 2.0


def test_barge_in_ignore_phrases_only_when_enabled():
    assert resolve_barge_in_ignore_phrases({}) is None
    phrases = resolve_barge_in_ignore_phrases(
        {"barge_in_filter": {"enabled": True, "ignore_phrases": [" Yeah ", "OKAY"]}}
    )
    assert phrases == ["yeah", "okay"]


def test_tool_filler_config_disabled_by_default():
    assert resolve_tool_filler_config({}) is None
    config = resolve_tool_filler_config(
        {"tool_filler_configuration": {"enabled": True, "delay_ms": 800}}
    )
    assert config["enabled"] is True
    assert config["delay_ms"] == 800
    assert "Let me check that." in config["phrases"]


def test_default_turn_stop_uses_configured_speech_timeout():
    strategies = _create_non_realtime_user_turn_stop_strategies(
        {"speech_timeout_secs": 0.35},
        uses_external_turns=False,
    )
    assert len(strategies) == 1
    assert isinstance(strategies[0], SpeechTimeoutUserTurnStopStrategy)
    assert strategies[0]._user_speech_timeout == 0.35


def test_semantic_eot_stop_strategy_uses_complete_and_incomplete_timeouts():
    strategies = _create_non_realtime_user_turn_stop_strategies(
        {
            "turn_stop_strategy": "semantic_eot",
            "semantic_eot_configuration": {
                "complete_timeout_secs": 0.25,
                "incomplete_timeout_secs": 1.1,
            },
        },
        uses_external_turns=False,
    )
    assert len(strategies) == 1
    assert isinstance(strategies[0], SemanticEotUserTurnStopStrategy)
    assert strategies[0]._complete_timeout_secs == 0.25
    assert strategies[0]._incomplete_timeout_secs == 1.1


def test_barge_in_filter_replaces_raw_vad_on_default_start_strategy():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"barge_in_filter": {"enabled": True, "ignore_phrases": ["yeah"]}},
        uses_external_turns=False,
    )
    assert len(strategies) == 1
    assert isinstance(strategies[0], IgnorePhraseUserTurnStartStrategy)
    assert "yeah" in strategies[0]._ignore


def test_min_words_keeps_ignore_filter_when_enabled():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {
            "turn_start_strategy": "min_words",
            "turn_start_min_words": 4,
            "barge_in_filter": {"enabled": True},
        },
        uses_external_turns=False,
    )
    assert len(strategies) == 1
    assert isinstance(strategies[0], IgnorePhraseUserTurnStartStrategy)
    assert strategies[0]._min_words == 4


def test_min_words_without_filter_stays_unchanged():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words", "turn_start_min_words": 4},
        uses_external_turns=False,
    )
    assert len(strategies) == 1
    assert type(strategies[0]) is MinWordsUserTurnStartStrategy


def test_realtime_local_vad_uses_configured_speech_timeout():
    strategies, analyzer = _create_realtime_user_turn_config(
        ServiceProviders.GOOGLE_REALTIME.value,
        run_configs={"speech_timeout_secs": 0.4},
    )
    assert strategies.stop[0]._user_speech_timeout == 0.4
    assert analyzer._params.stop_secs == 0.25


def test_realtime_custom_vad_does_not_reuse_mismatched_analyzer():
    existing = SimpleNamespace(
        _params=VADParams(confidence=0.7, min_volume=0.6, start_secs=0.15, stop_secs=0.25)
    )
    _strategies, analyzer = _create_realtime_user_turn_config(
        ServiceProviders.ULTRAVOX_REALTIME.value,
        vad_analyzer=existing,
        run_configs={"vad_configuration": {"stop_secs": 0.5}},
    )
    assert analyzer is not existing
    assert analyzer._params.stop_secs == 0.5


async def test_ignore_phrase_does_not_interrupt_while_bot_is_speaking():
    strategy = IgnorePhraseUserTurnStartStrategy(
        min_words=1, ignore_phrases=["yeah", "okay"]
    )
    started = False

    @strategy.event_handler("on_user_turn_started")
    async def _on_start(_strategy, _params):
        nonlocal started
        started = True

    await strategy.process_frame(BotStartedSpeakingFrame())
    result = await strategy.process_frame(
        TranscriptionFrame(text="yeah", user_id="user", timestamp="")
    )
    assert result == ProcessFrameResult.CONTINUE
    assert started is False

    await strategy.process_frame(BotStoppedSpeakingFrame())
    result = await strategy.process_frame(
        TranscriptionFrame(text="okay", user_id="user", timestamp="")
    )
    assert result == ProcessFrameResult.STOP
    assert started is True


async def test_tool_filler_speaks_after_delay_and_cancels_when_tool_returns():
    queued = []
    engine = SimpleNamespace(
        _tool_filler_configuration={
            "enabled": True,
            "delay_ms": 20,
            "phrases": ["Let me check that."],
        },
        _queued_speech_mute_state="idle",
        task=SimpleNamespace(queue_frame=AsyncMock(side_effect=queued.append)),
    )

    async with tool_filler(engine):
        await asyncio.sleep(0.08)

    assert queued
    assert isinstance(queued[0], TTSSpeakFrame)
    assert queued[0].text == "Let me check that."

    queued.clear()
    async with tool_filler(engine):
        await asyncio.sleep(0)

    await asyncio.sleep(0.05)
    assert queued == []


def test_deepgram_endpointing_reads_workflow_config():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="nova-3",
            language="en",
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )
    with patch(
        "api.services.pipecat.service_factory.DeepgramSTTService"
    ) as mock_service:
        create_stt_service(
            user_config,
            audio_config,
            run_configs={"stt_turn_configuration": {"endpointing_ms": 450}},
        )
    assert mock_service.call_args.kwargs["settings"].endpointing == 450


def test_flux_eot_timeout_reads_workflow_config():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-en",
            language="en",
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )
    from api.services.configuration.options import DEEPGRAM_FLUX_MODELS

    if "flux-general-en" not in DEEPGRAM_FLUX_MODELS:
        pytest.skip("flux-general-en is not a registered Flux model")

    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(
            user_config,
            audio_config,
            run_configs={"stt_turn_configuration": {"flux_eot_timeout_ms": 1200}},
        )
    assert mock_service.call_args.kwargs["settings"].eot_timeout_ms == 1200


def test_resolve_stt_turn_settings_defaults():
    settings = resolve_stt_turn_settings({})
    assert settings["endpointing_ms"] == 300
    assert settings["flux_eot_timeout_ms"] == 800
    assert DEFAULT_SPEECH_TIMEOUT_SECS == 0.2
