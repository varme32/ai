from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    ServiceProviders,
    ServiceType,
    SmallestAISTTConfiguration,
    SmallestAITTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


def test_smallest_tts_configuration_defaults_and_registry():
    config = SmallestAITTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SMALLEST
    assert config.model == "lightning_v3.1"
    assert config.voice == "sophia"
    assert config.language == "en"
    assert config.speed == 1.0
    assert (
        REGISTRY[ServiceType.TTS][ServiceProviders.SMALLEST]
        is SmallestAITTSConfiguration
    )


def test_smallest_stt_configuration_defaults_and_registry():
    config = SmallestAISTTConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SMALLEST
    assert config.model == "pulse"
    assert config.language == "en"
    assert (
        REGISTRY[ServiceType.STT][ServiceProviders.SMALLEST]
        is SmallestAISTTConfiguration
    )


def test_validator_accepts_smallest_services():
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            SmallestAITTSConfiguration(api_key="test-key"),
            "tts",
        )
        == []
    )
    assert (
        validator._validate_service(
            SmallestAISTTConfiguration(api_key="test-key"),
            "stt",
        )
        == []
    )


def test_create_smallest_tts_service_normalizes_hyphenated_model_values():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SMALLEST.value,
            api_key="test-key",
            model="lightning-v3.1",
            voice="sophia",
            language="en",
            speed=1.0,
        )
    )
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.SmallestTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].model == "lightning_v3.1"
    from pipecat.services.tts_service import TextAggregationMode

    assert kwargs["text_aggregation_mode"] == TextAggregationMode.TOKEN


async def test_smallest_stt_marks_is_final_transcripts_as_finalized():
    """Final transcripts must set finalized=True so turn-end does not wait STT P99."""
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.services.smallest.stt import SmallestSTTService

    service = SmallestSTTService(api_key="test-key")
    captured = []

    async def capture(frame, direction=None):
        captured.append(frame)

    service.push_frame = capture
    service._user_id = "user"
    service.stop_processing_metrics = AsyncMock()
    service._handle_transcription = AsyncMock()

    await service._process_response(
        {"is_final": True, "transcript": "hello there", "language": "en"}
    )

    finals = [frame for frame in captured if isinstance(frame, TranscriptionFrame)]
    assert len(finals) == 1
    assert finals[0].finalized is True
    assert finals[0].text == "hello there"
