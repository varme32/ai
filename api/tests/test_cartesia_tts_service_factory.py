from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import (
    CARTESIA_TTS_MODELS,
    CartesiaTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import CARTESIA_SAMPLE_RATE, create_tts_service


def test_cartesia_tts_configuration_defaults_to_sonic_3_5():
    config = CartesiaTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.CARTESIA
    assert config.model == "sonic-3.6"
    assert CARTESIA_TTS_MODELS == ["sonic-3.6", "sonic-3.5", "sonic-3"]


def test_create_cartesia_tts_service_passes_selected_model():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DograhCartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == CARTESIA_SAMPLE_RATE  # always 24 kHz
    assert kwargs["settings"].model == "sonic-3.6"
    assert kwargs["settings"].voice == "test-voice-id"


def test_create_cartesia_tts_uses_24khz_even_on_webrtc_16khz_transport():
    """Cartesia always generates at 24 kHz so the voice matches the website preview."""
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.6",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=16000,  # WebRTC transport
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DograhCartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    # Must be 24 kHz regardless of 16 kHz transport — base_output resamples
    assert kwargs["sample_rate"] == CARTESIA_SAMPLE_RATE


def test_cartesia_tts_configuration_default_language_is_english():
    config = CartesiaTTSConfiguration(api_key="test-key")

    assert config.language == "en"


def test_create_cartesia_tts_service_passes_language_to_settings():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
            language="tr",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DograhCartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert str(kwargs["settings"].language) == "tr"


def test_create_cartesia_tts_uses_16khz_on_telephony():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
            language="en",
        ),
        stt=SimpleNamespace(language="te-IN"),
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=8000,
        transport_in_sample_rate=8000,
        pipeline_sample_rate=8000,
    )

    with patch(
        "api.services.pipecat.service_factory.DograhCartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["sample_rate"] == CARTESIA_SAMPLE_RATE  # always 24 kHz, never transport rate
    assert str(kwargs["settings"].language) == "te"
