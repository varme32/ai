from types import SimpleNamespace

from api.services.pipecat.tts_language import language_from_script, resolve_tts_language


def test_language_from_script_detects_telugu():
    text = "హలో నమస్తే అండి property enquiry"
    assert language_from_script(text) == "te"


def test_language_from_script_detects_hindi():
    assert language_from_script("नमस्ते, आप कैसे हैं?") == "hi"


def test_language_from_script_english_returns_none():
    assert language_from_script("Hello, how are you?") is None


def test_resolve_tts_language_inherits_sarvam_telugu_from_stt():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(language="en"),
        stt=SimpleNamespace(language="te-IN"),
    )
    assert resolve_tts_language(user_config) == "te"


def test_resolve_tts_language_keeps_explicit_tts_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(language="hi"),
        stt=SimpleNamespace(language="te-IN"),
    )
    assert resolve_tts_language(user_config) == "hi"
