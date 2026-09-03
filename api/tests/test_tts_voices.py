from unittest.mock import AsyncMock, patch

import pytest
from api.services.configuration.options.sarvam import SARVAM_V3_VOICE_CATALOG
from api.services.configuration.tts_voices import (
    list_cartesia_voices,
    list_murf_voices,
    list_sarvam_voices,
    list_smallest_voices,
    resolve_tts_api_key,
)


class _FakeResponse:
    def __init__(self, status, payload, text="", body=b"", headers=None):
        self.status = status
        self._payload = payload
        self._text = text or ""
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        if not self._responses:
            return _FakeResponse(500, {}, "no more responses")
        return self._responses.pop(0)


def test_sarvam_v3_catalog_returns_every_documented_voice():
    result = list_sarvam_voices(model="bulbul:v3")
    ids = [voice["voice_id"] for voice in result["voices"]]
    assert len(ids) == len(SARVAM_V3_VOICE_CATALOG)
    assert "shubh" in ids
    assert "rupali" in ids
    assert "amelia" in ids
    assert all(voice["accent"] == "in" for voice in result["voices"])
    assert all(" - " in voice["name"] for voice in result["voices"])
    assert "te-IN" in result["facets"]["languages"]
    telugu = list_sarvam_voices(model="bulbul:v3", language="te-IN")
    assert len(telugu["voices"]) == len(result["voices"])


def test_sarvam_v2_catalog_is_separate_from_v3():
    v2 = {voice["voice_id"] for voice in list_sarvam_voices(model="bulbul:v2")["voices"]}
    v3 = {voice["voice_id"] for voice in list_sarvam_voices(model="bulbul:v3")["voices"]}
    assert v2 == {"anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh"}
    assert "shubh" not in v2
    assert "anushka" not in v3


@pytest.mark.asyncio
async def test_cartesia_paginates_until_the_catalog_is_exhausted():
    page1 = {
        "data": [
            {
                "id": "voice-1",
                "name": "Aadhya",
                "tagline": "Soother",
                "gender": "feminine",
                "language": "hi",
                "country": "IN",
                "preview_file_url": "https://cdn.example/aadhya.wav",
            }
        ]
        + [{"id": f"pad-{i}", "name": f"Pad {i}", "language": "en", "country": "US"} for i in range(99)]
    }
    page2 = {
        "data": [
            {
                "id": "voice-2",
                "name": "Vikram",
                "tagline": "Conversational",
                "gender": "masculine",
                "language": "te",
                "country": "IN",
            }
        ]
    }
    session = _FakeSession(
        [_FakeResponse(200, page1), _FakeResponse(200, page2)]
    )
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value="sk_test"),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        result = await list_cartesia_voices(organization_id=1)

    names = [voice["name"] for voice in result["voices"]]
    assert "Aadhya - Soother" in names
    assert "Vikram - Conversational" in names
    assert len(result["voices"]) == 101
    aadhya = next(voice for voice in result["voices"] if voice["voice_id"] == "voice-1")
    assert aadhya["gender"] == "female"
    assert aadhya["accent"] == "in"
    assert aadhya["preview_url"] == "https://cdn.example/aadhya.wav"


@pytest.mark.asyncio
async def test_murf_prefers_the_india_voices_endpoint():
    payload = [
        {
            "voiceId": "Anisha",
            "displayName": "Anisha",
            "description": "Conversational",
            "gender": "Female",
            "locale": "hi-IN",
            "accent": "Indian",
            "sampleAudioUrl": "https://cdn.example/anisha.mp3",
        }
    ]
    session = _FakeSession([_FakeResponse(200, payload)])
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value="murf-key"),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        result = await list_murf_voices(organization_id=1, model="falcon-2")

    assert session.urls[0].startswith("https://in.api.murf.ai/")
    assert result["voices"][0]["voice_id"] == "Anisha"
    assert result["voices"][0]["name"] == "Anisha - Conversational"
    assert result["voices"][0]["accent"] == "in"
    assert result["voices"][0]["preview_url"] == "https://cdn.example/anisha.mp3"


@pytest.mark.asyncio
async def test_smallest_returns_the_full_model_catalog_without_language_prefilter():
    payload = {
        "voices": [
            {
                "voiceId": "sridhar",
                "displayName": "Sridhar",
                "tags": {
                    "gender": "male",
                    "accent": "indian",
                    "language": ["telugu", "hindi", "english"],
                    "recommendedLanguages": ["telugu"],
                    "style": "Narrator",
                },
            },
            {
                "voiceId": "emily",
                "displayName": "Emily",
                "tags": {
                    "gender": "female",
                    "accent": "american",
                    "language": ["english"],
                    "recommendedLanguages": ["english"],
                    "style": "Conversational",
                },
            },
        ]
    }
    session = _FakeSession(
        [_FakeResponse(200, payload), _FakeResponse(200, payload)]
    )
    with patch("aiohttp.ClientSession", return_value=session):
        result = await list_smallest_voices(model="lightning_v3.1")
        telugu = await list_smallest_voices(model="lightning_v3.1", language="te")

    assert "api.india.smallest.ai" in session.urls[0]
    ids = {voice["voice_id"] for voice in result["voices"]}
    assert ids == {"sridhar", "emily"}
    sridhar = next(voice for voice in result["voices"] if voice["voice_id"] == "sridhar")
    assert sridhar["name"] == "Sridhar - Narrator"
    assert sridhar["language"] == "te-IN"
    assert sridhar["accent"] == "in"
    assert "te-IN" in result["facets"]["languages"]
    assert {voice["voice_id"] for voice in telugu["voices"]} == {"sridhar"}


@pytest.mark.asyncio
async def test_masked_api_key_override_is_ignored():
    with patch(
        "api.services.configuration.ai_model_configuration.get_resolved_ai_model_configuration",
        new=AsyncMock(
            return_value=type(
                "R",
                (),
                {
                    "effective": type(
                        "E",
                        (),
                        {
                            "tts": type(
                                "T",
                                (),
                                {"provider": "cartesia", "api_key": "real-key"},
                            )()
                        },
                    )()
                },
            )()
        ),
    ):
        key = await resolve_tts_api_key(
            organization_id=1,
            provider="cartesia",
            api_key_override="************abcd",
        )
    assert key == "real-key"


@pytest.mark.asyncio
async def test_cartesia_lists_voices_without_the_user_api_key():
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.services.mps_service_key_client.mps_service_key_client.get_voices",
            new=AsyncMock(
                return_value={
                    "voices": [
                        {
                            "voice_id": "voice-1",
                            "name": "Aadhya",
                            "tagline": "Soother",
                            "gender": "female",
                            "accent": "in",
                            "language": "hi",
                            "preview_url": "https://cdn.example/aadhya.wav",
                        }
                    ]
                }
            ),
        ),
    ):
        result = await list_cartesia_voices(organization_id=1)
    assert result["voices"][0]["name"] == "Aadhya - Soother"
    assert result["voices"][0]["voice_id"] == "voice-1"


@pytest.mark.asyncio
async def test_murf_lists_voices_without_an_api_key():
    payload = [
        {
            "voiceId": "Anisha",
            "displayName": "Anisha",
            "description": "Conversational",
            "gender": "Female",
            "locale": "hi-IN",
        }
    ]
    session = _FakeSession([_FakeResponse(200, payload)])
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value=None),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        result = await list_murf_voices(organization_id=1)

    assert result["voices"][0]["voice_id"] == "Anisha"


@pytest.mark.asyncio
async def test_cartesia_telugu_filter_includes_indian_voices():
    page = {
        "data": [
            {
                "id": "voice-1",
                "name": "Aadhya",
                "tagline": "Soother",
                "gender": "feminine",
                "language": "hi",
                "country": "IN",
                "preview_file_url": "https://cdn.cartesia.ai/aadhya.wav",
            },
            {
                "id": "voice-2",
                "name": "Emily",
                "language": "en",
                "country": "US",
            },
        ]
    }
    session = _FakeSession([_FakeResponse(200, page)])
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value="sk_test"),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        full = await list_cartesia_voices(organization_id=1)

    assert "te-IN" in full["facets"]["languages"]

    session = _FakeSession([_FakeResponse(200, page)])
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value="sk_test"),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        telugu = await list_cartesia_voices(organization_id=1, language="te-IN")

    assert {voice["voice_id"] for voice in telugu["voices"]} == {"voice-1"}
    assert "te-IN" in telugu["facets"]["languages"]


@pytest.mark.asyncio
async def test_murf_reads_telugu_locale_from_voice_id():
    payload = [
        {
            "voiceId": "te-IN-ananya",
            "displayName": "Ananya",
            "gender": "Female",
            "sampleAudioUrl": "https://cdn.murf.ai/ananya.mp3",
        }
    ]
    session = _FakeSession([_FakeResponse(200, payload)])
    with (
        patch(
            "api.services.configuration.tts_voices.resolve_tts_api_key",
            new=AsyncMock(return_value=None),
        ),
        patch("aiohttp.ClientSession", return_value=session),
    ):
        result = await list_murf_voices(organization_id=1)

    ananya = result["voices"][0]
    assert ananya["voice_id"] == "te-IN-ananya"
    assert ananya["language"] == "te-IN"
    assert "te-IN" in result["facets"]["languages"]


@pytest.mark.asyncio
async def test_preview_plays_the_catalog_sample_clip():
    from api.services.configuration.tts_voices import synthesize_voice_preview

    audio = b"RIFF....wav"
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {},
                body=audio,
                headers={"Content-Type": "audio/wav"},
            )
        ]
    )
    with patch("aiohttp.ClientSession", return_value=session):
        data, content_type = await synthesize_voice_preview(
            provider="murf",
            voice_id="Anisha",
            organization_id=1,
            preview_url="https://cdn.murf.ai/anisha.mp3",
        )
    assert data == audio
    assert content_type == "audio/wav"
