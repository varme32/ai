from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.murf_tts import (
    _normalize_murf_model,
    _normalize_murf_voice,
    _pcm_without_wav_header,
    _resolve_murf_locale,
)
from api.services.pipecat.service_factory import create_tts_service


def test_normalize_murf_model_accepts_legacy_identifiers():
    assert _normalize_murf_model("FALCON") == "falcon-2"
    assert _normalize_murf_model("falcon-2") == "falcon-2"
    assert _normalize_murf_model("Falcon-2") == "falcon-2"
    assert _normalize_murf_model("GEN2") == "gen2"
    assert _normalize_murf_model(None) == "falcon-2"


def test_normalize_murf_voice_accepts_api_voice_ids():
    assert _normalize_murf_voice("en-US-alicia") == "en-US-alicia"
    assert _normalize_murf_voice("hi-IN-aarav") == "hi-IN-aarav"
    assert _normalize_murf_voice("te-IN-ananya") == "te-IN-ananya"
    assert _normalize_murf_voice(None) == "Gordon"


def test_pcm_without_wav_header_strips_riff():
    payload = b"RIFF" + b"\x00" * 40 + b"pcm-bytes"
    assert _pcm_without_wav_header(payload) == b"pcm-bytes"
    assert _pcm_without_wav_header(b"raw-pcm") == b"raw-pcm"


def test_resolve_murf_locale_maps_short_codes():
    assert _resolve_murf_locale("te") == "te-IN"
    assert _resolve_murf_locale("en") == "en-US"
    assert _resolve_murf_locale("hi-IN") == "hi-IN"
    assert _resolve_murf_locale(None, "hi-IN-aarav") == "hi-IN"
    assert _resolve_murf_locale(None, "en-US-natalie") == "en-US"


def test_create_murf_tts_matches_telephony_wire_rate():
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
    assert kwargs["sample_rate"] == 16000
    assert kwargs["settings"].murf_sample_rate == 16000
    assert "text_aggregation_mode" not in kwargs


def test_create_murf_gen2_also_matches_wire_rate():
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
    assert kwargs["sample_rate"] == 16000
    assert kwargs["settings"].murf_sample_rate == 16000


def test_murf_session_config_omits_style_when_unset():
    from api.services.pipecat.murf_tts import MurfTTSService, MurfTTSSettings

    svc = MurfTTSService(
        api_key="test-key",
        settings=MurfTTSSettings(
            model="falcon-2",
            voice="hi-IN-kabir",
            language="hi",
        ),
    )
    cfg = svc._build_session_config()
    assert cfg["voice_config"]["voice_id"] == "hi-IN-kabir"
    assert cfg["voice_config"]["voiceId"] == "hi-IN-kabir"
    assert cfg["voice_config"]["locale"] == "hi-IN"
    assert "style" not in cfg["voice_config"]


def test_murf_session_config_includes_style_when_specified():
    from api.services.pipecat.murf_tts import MurfTTSService, MurfTTSSettings

    svc = MurfTTSService(
        api_key="test-key",
        settings=MurfTTSSettings(
            model="falcon-2",
            voice="en-US-marcus",
            style="Conversational",
        ),
    )
    cfg = svc._build_session_config()
    assert cfg["voice_config"]["voice_id"] == "en-US-marcus"
    assert cfg["voice_config"]["style"] == "Conversational"


def test_murf_build_text_msg_attaches_voice_config():
    from api.services.pipecat.murf_tts import MurfTTSService, MurfTTSSettings

    svc = MurfTTSService(
        api_key="test-key",
        settings=MurfTTSSettings(
            model="falcon-2",
            voice="te-IN-ananya",
            language="te",
        ),
    )
    msg = svc._build_text_msg("హలో", context_id="ctx-123", end=False)
    assert msg["text"] == "హలో"
    assert msg["context_id"] == "ctx-123"
    assert msg["end"] is False
    assert msg["voice_config"]["voice_id"] == "te-IN-ananya"
    assert msg["voice_config"]["locale"] == "te-IN"
