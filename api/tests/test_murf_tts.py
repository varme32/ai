from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import TTS_OUTPUT_SAMPLE_RATE
from api.services.pipecat.murf_tts import (
    _normalize_murf_model,
    _pcm_without_wav_header,
    _resolve_murf_locale,
)
from api.services.pipecat.service_factory import create_tts_service
from pipecat.services.tts_service import TextAggregationMode


def test_normalize_murf_model_accepts_legacy_identifiers():
    assert _normalize_murf_model("FALCON") == "falcon-2"
    assert _normalize_murf_model("falcon-2") == "falcon-2"
    assert _normalize_murf_model("Falcon-2") == "falcon-2"
    assert _normalize_murf_model("GEN2") == "gen2"
    assert _normalize_murf_model(None) == "falcon-2"


def test_pcm_without_wav_header_strips_riff():
    payload = b"RIFF" + b"\x00" * 40 + b"pcm-bytes"
    assert _pcm_without_wav_header(payload) == b"pcm-bytes"
    assert _pcm_without_wav_header(b"raw-pcm") == b"raw-pcm"


def test_resolve_murf_locale_maps_short_codes():
    assert _resolve_murf_locale("te") == "te-IN"
    assert _resolve_murf_locale("en") == "en-US"
    assert _resolve_murf_locale("hi-IN") == "hi-IN"


def test_create_murf_tts_ignores_telephony_8khz_and_streams_tokens():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.MURF.value,
            api_key="test-key",
            model="falcon-2",
            voice="Gordon",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        pipeline_sample_rate=8000,
        transport_out_sample_rate=8000,
        transport_in_sample_rate=8000,
    )

    with patch("api.services.pipecat.service_factory.MurfTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["sample_rate"] == TTS_OUTPUT_SAMPLE_RATE
    assert kwargs["settings"].murf_sample_rate == TTS_OUTPUT_SAMPLE_RATE
    assert kwargs["text_aggregation_mode"] == TextAggregationMode.TOKEN


def test_create_murf_gen2_stays_sentence_aggregated():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.MURF.value,
            api_key="test-key",
            model="GEN2",
            voice="Anisha",
            language="te",
        )
    )
    audio_config = SimpleNamespace(pipeline_sample_rate=8000)

    with patch("api.services.pipecat.service_factory.MurfTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert "text_aggregation_mode" not in kwargs
    assert kwargs["sample_rate"] == TTS_OUTPUT_SAMPLE_RATE
