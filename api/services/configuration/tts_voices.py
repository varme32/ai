"""Fetch live TTS voice catalogs from provider APIs.

Selecting Cartesia / Sarvam / Murf / Smallest loads the full catalog
immediately. The user's API key is optional for listing and is used later
for actual TTS. Indian regional endpoints are preferred where published.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.services.configuration.masking import contains_masked_key
from api.services.configuration.options.sarvam import (
    SARVAM_LANGUAGES,
    SARVAM_V2_VOICE_CATALOG,
    SARVAM_V3_VOICE_CATALOG,
)
from api.services.configuration.options.smallest import SMALLEST_LANGUAGE_NAME_TO_ISO

CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices"
CARTESIA_TTS_BYTES_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-08-14"
MURF_VOICES_URLS = (
    "https://in.api.murf.ai/v1/speech/voices",
    "https://api.murf.ai/v1/speech/voices",
)
MURF_GENERATE_URLS = (
    "https://in.api.murf.ai/v1/speech/generate",
    "https://api.murf.ai/v1/speech/generate",
)
SMALLEST_VOICES_URL = "https://api.india.smallest.ai/waves/v1/{model}/get_voices"
SMALLEST_SPEECH_URL = (
    "https://api.india.smallest.ai/waves/v1/{model}/get_speech"
)
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# Always expose these in the picker so Telugu (and other Indian languages)
# appear even when a provider tags voices as Hindi/English only.
INDIAN_LANGUAGE_FACETS = (
    "as-IN",
    "bn-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
)
_VOICE_ID_LOCALE = re.compile(r"^([a-z]{2,3}[_-][a-z]{2})", re.IGNORECASE)
_PREVIEW_HOST_SUFFIXES = (
    "cartesia.ai",
    "murf.ai",
    "smallest.ai",
    "sarvam.ai",
    "amazonaws.com",
    "cloudfront.net",
    "googleusercontent.com",
    "azureedge.net",
)

_LANGUAGE_ALIASES = {
    "te": "te-IN",
    "telugu": "te-IN",
    "hi": "hi-IN",
    "hindi": "hi-IN",
    "ta": "ta-IN",
    "tamil": "ta-IN",
    "kn": "kn-IN",
    "kannada": "kn-IN",
    "ml": "ml-IN",
    "malayalam": "ml-IN",
    "mr": "mr-IN",
    "marathi": "mr-IN",
    "gu": "gu-IN",
    "gujarati": "gu-IN",
    "bn": "bn-IN",
    "bengali": "bn-IN",
    "pa": "pa-IN",
    "punjabi": "pa-IN",
    "or": "od-IN",
    "od": "od-IN",
    "odia": "od-IN",
    "as": "as-IN",
    "assamese": "as-IN",
}

_PREVIEW_TEXT = {
    "te-IN": "నమస్కారం, ఇది నా వాయిస్ సాంపిల్.",
    "hi-IN": "नमस्ते, यह मेरी आवाज़ का सैंपल है।",
    "ta-IN": "வணக்கம், இது என் குரல் மாதிரி.",
    "kn-IN": "ನಮಸ್ಕಾರ, ಇದು ನನ್ನ ಧ್ವನಿ ಮಾದರಿ.",
    "ml-IN": "നമസ്കാരം, ഇത് എന്റെ ശബ്ദ സാമ്പിൾ ആണ്.",
    "mr-IN": "नमस्कार, हा माझ्या आवाजाचा नमुना आहे.",
    "gu-IN": "નમસ્તે, આ મારા અવાજનો નમૂનો છે.",
    "bn-IN": "নমস্কার, এটি আমার ভয়েস স্যাম্পল।",
    "pa-IN": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਇਹ ਮੇਰੀ ਆਵਾਜ਼ ਦਾ ਨਮੂਨਾ ਹੈ।",
    "od-IN": "ନମସ୍କାର, ଏହା ମୋ ସ୍ୱରର ନମୁନା।",
    "en-IN": "Hello, this is a sample of my voice.",
}

LIVE_VOICE_PROVIDERS = frozenset({"cartesia", "sarvam", "murf", "smallest"})
_PLATFORM_API_KEY_ENV = {
    "cartesia": "CARTESIA_API_KEY",
    "murf": "MURF_API_KEY",
    "sarvam": "SARVAM_API_KEY",
    "smallest": "SMALLEST_API_KEY",
}


def _usable_api_key(api_key_override: str | None) -> str | None:
    if not api_key_override or not str(api_key_override).strip():
        return None
    key = str(api_key_override).strip()
    if contains_masked_key(key):
        return None
    return key


async def resolve_tts_api_key(
    *,
    organization_id: int | None,
    provider: str,
    api_key_override: str | None = None,
) -> str | None:
    usable = _usable_api_key(api_key_override)
    if usable:
        return usable

    from api.services.configuration.ai_model_configuration import (
        get_resolved_ai_model_configuration,
    )

    try:
        resolved = await get_resolved_ai_model_configuration(
            organization_id=organization_id
        )
        tts_config = resolved.effective.tts if resolved.effective else None
    except Exception:
        tts_config = None
    if tts_config and getattr(tts_config, "provider", None) == provider:
        raw_key = getattr(tts_config, "api_key", None)
        if isinstance(raw_key, list):
            if raw_key:
                return raw_key[0]
        elif raw_key:
            return raw_key

    env_name = _PLATFORM_API_KEY_ENV.get(provider)
    if env_name:
        return os.environ.get(env_name) or None
    return None


def canonicalize_language(code: str | None) -> str | None:
    if not code:
        return None
    raw = str(code).strip().replace("_", "-")
    if not raw:
        return None
    key = raw.lower()
    if key in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[key]
    if "-" in key:
        lang, region = key.split("-", 1)
        if lang in _LANGUAGE_ALIASES:
            return _LANGUAGE_ALIASES[lang]
        return f"{lang}-{region.upper()}"
    return key


def languages_match(left: str | None, right: str | None) -> bool:
    a = canonicalize_language(left)
    b = canonicalize_language(right)
    if not a or not b:
        return False
    return a == b or a.startswith(f"{b}-") or b.startswith(f"{a}-") or a.split("-")[0] == b.split("-")[0]


def _matches_filters(
    *,
    name: str,
    voice_id: str,
    gender: str | None,
    accent: str | None,
    language: str | None,
    q: str | None,
    filter_gender: str | None,
    filter_accent: str | None,
    filter_language: str | None,
    extra_languages: list[str] | None = None,
) -> bool:
    if q:
        needle = q.lower()
        hay = f"{name} {voice_id} {gender or ''} {accent or ''} {language or ''}".lower()
        if needle not in hay:
            return False
    if filter_gender and gender and gender.lower() != filter_gender.lower():
        return False
    if filter_accent and accent and accent.lower() != filter_accent.lower():
        return False
    if filter_language:
        candidates = [language, *(extra_languages or [])]
        if any(candidate and languages_match(candidate, filter_language) for candidate in candidates):
            return True
        # Voices that can speak every listed Indian language still match.
        if extra_languages and set(canonicalize_language(item) for item in extra_languages) >= set(
            canonicalize_language(item) for item in SARVAM_LANGUAGES
        ):
            return True
        if any(candidates):
            return False
    return True


def _normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in {"feminine", "female", "f"}:
        return "female"
    if lowered in {"masculine", "male", "m"}:
        return "male"
    if lowered in {"neutral", "gender_neutral"}:
        return "neutral"
    return lowered


def _accent_from_country_or_locale(country: str | None, locale: str | None) -> str | None:
    if country:
        return country.strip().lower()
    if locale and "-" in locale:
        return locale.split("-", 1)[1].lower()
    return None


def _is_indian_accent(accent: str | None) -> bool:
    if not accent:
        return False
    return accent.strip().lower() in {"in", "india", "indian"}


def _locale_from_voice_id(voice_id: str | None) -> str | None:
    if not voice_id:
        return None
    match = _VOICE_ID_LOCALE.match(voice_id.strip())
    if not match:
        return None
    return canonicalize_language(match.group(1))


def _facets(
    voices: list[dict[str, Any]], extra_languages: list[str] | None = None
) -> dict[str, list[str]]:
    genders: set[str] = set()
    accents: set[str] = set()
    languages: set[str] = set(INDIAN_LANGUAGE_FACETS)
    for voice in voices:
        if voice.get("gender"):
            genders.add(str(voice["gender"]).lower())
        if voice.get("accent"):
            accents.add(str(voice["accent"]).lower())
        for item in [voice.get("language"), *(voice.get("languages") or [])]:
            canonical = canonicalize_language(item)
            if canonical:
                languages.add(canonical)
    for item in extra_languages or []:
        canonical = canonicalize_language(item)
        if canonical:
            languages.add(canonical)
    return {
        "genders": sorted(genders),
        "accents": sorted(accents),
        "languages": sorted(languages),
    }


def _allowed_preview_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _PREVIEW_HOST_SUFFIXES)


async def _download_allowed_preview(url: str | None) -> tuple[bytes, str] | None:
    if not url or not _allowed_preview_url(url):
        return None
    return await _download_audio(url)


def _preview_text(language: str | None) -> str:
    canonical = canonicalize_language(language) or "en-IN"
    return _PREVIEW_TEXT.get(canonical) or _PREVIEW_TEXT["en-IN"]


async def _download_audio(url: str) -> tuple[bytes, str] | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                content_type = (resp.headers.get("Content-Type") or "audio/mpeg").split(";")[0]
                if data:
                    return data, content_type
    except aiohttp.ClientError:
        logger.debug(f"Failed to download preview audio from {url}", exc_info=True)
    return None


async def list_provider_voices(
    *,
    provider: str,
    organization_id: int | None,
    model: str | None = None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
    api_key_override: str | None = None,
) -> dict[str, Any]:
    if provider == "cartesia":
        return await list_cartesia_voices(
            organization_id=organization_id,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
            api_key_override=api_key_override,
        )
    if provider == "sarvam":
        return list_sarvam_voices(
            model=model, language=language, q=q, gender=gender, accent=accent
        )
    if provider == "murf":
        return await list_murf_voices(
            organization_id=organization_id,
            model=model,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
            api_key_override=api_key_override,
        )
    if provider == "smallest":
        return await list_smallest_voices(
            model=model, language=language, q=q, gender=gender, accent=accent
        )
    raise HTTPException(status_code=400, detail=f"Unsupported live voice provider {provider}")


async def list_cartesia_voices(
    *,
    organization_id: int | None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
    api_key_override: str | None = None,
) -> dict[str, Any]:
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="cartesia",
        api_key_override=api_key_override,
    )
    if not api_key:
        return await _list_cartesia_voices_via_mps(
            organization_id=organization_id,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-API-Key": api_key,
        "Cartesia-Version": CARTESIA_VERSION,
        "Accept": "application/json",
    }
    raw_voices: list[dict[str, Any]] = []
    starting_after: str | None = None
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(20):
                params: dict[str, Any] = {
                    "limit": 100,
                    "expand[]": "preview_file_url",
                }
                if starting_after:
                    params["starting_after"] = starting_after
                async with session.get(
                    CARTESIA_VOICES_URL,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Cartesia voices API error {resp.status}: {body[:200]}")
                        raise HTTPException(
                            status_code=502,
                            detail=f"Cartesia API returned {resp.status}: {body[:200]}",
                        )
                    payload = await resp.json()
                page = (
                    payload.get("data")
                    if isinstance(payload, dict)
                    else payload
                ) or []
                if not isinstance(page, list) or not page:
                    break
                raw_voices.extend(page)
                if len(page) < 100:
                    break
                starting_after = page[-1].get("id")
                if not starting_after:
                    break
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Cartesia API: {exc}"
        ) from exc

    all_voices: list[dict[str, Any]] = []
    for item in raw_voices:
        voice_id = str(item.get("id") or item.get("voice_id") or "")
        if not voice_id:
            continue
        name = str(item.get("name") or voice_id)
        tagline = (item.get("tagline") or "").strip()
        display_name = f"{name} - {tagline}" if tagline else name
        v_gender = _normalize_gender(item.get("gender"))
        v_language = canonicalize_language(item.get("language"))
        extra_languages: list[str] = []
        for accent_item in item.get("accents") or []:
            if isinstance(accent_item, dict):
                extra_languages.append(
                    canonicalize_language(
                        accent_item.get("locale") or accent_item.get("language")
                    )
                )
            elif isinstance(accent_item, str):
                extra_languages.append(canonicalize_language(accent_item))
        for lang in item.get("languages") or []:
            extra_languages.append(canonicalize_language(lang))
        extra_languages = [lang for lang in extra_languages if lang]
        v_accent = _accent_from_country_or_locale(
            item.get("country"), v_language
        )
        # Cartesia Indian voices can speak Telugu and other Indian languages
        # even when the catalog tags them as Hindi/English.
        if _is_indian_accent(v_accent):
            extra_languages = list(
                dict.fromkeys([*extra_languages, *INDIAN_LANGUAGE_FACETS])
            )
        all_voices.append(
            {
                "voice_id": voice_id,
                "name": display_name,
                "description": item.get("description") or tagline or None,
                "accent": v_accent,
                "gender": v_gender,
                "language": v_language,
                "languages": extra_languages or None,
                "preview_url": item.get("preview_file_url")
                or item.get("preview_url"),
            }
        )
    voices = [
        voice
        for voice in all_voices
        if _matches_filters(
            name=voice["name"],
            voice_id=voice["voice_id"],
            gender=voice.get("gender"),
            accent=voice.get("accent"),
            language=voice.get("language"),
            extra_languages=voice.get("languages") or [],
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        )
    ]
    return {"provider": "cartesia", "voices": voices, "facets": _facets(all_voices)}


async def _list_cartesia_voices_via_mps(
    *,
    organization_id: int | None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
) -> dict[str, Any]:
    """List Cartesia voices without the user's BYOK key (platform catalog)."""
    from api.services.mps_service_key_client import mps_service_key_client

    try:
        result = await mps_service_key_client.get_voices(
            provider="cartesia",
            language=language,
            q=q,
            gender=gender,
            accent=accent,
            organization_id=organization_id,
        )
    except Exception as exc:
        logger.warning(f"Cartesia MPS voice catalog failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Failed to load Cartesia voices. Try again in a moment.",
        ) from exc

    raw_voices = result.get("voices", []) if isinstance(result, dict) else []
    all_voices: list[dict[str, Any]] = []
    for item in raw_voices:
        voice_id = str(item.get("voice_id") or item.get("id") or "")
        if not voice_id:
            continue
        name = str(item.get("name") or voice_id)
        tagline = (item.get("tagline") or item.get("description") or "").strip()
        display_name = f"{name} - {tagline}" if tagline and tagline not in name else name
        v_language = canonicalize_language(item.get("language"))
        v_accent = item.get("accent")
        extra_languages = list(item.get("languages") or [])
        extra_languages = [canonicalize_language(lang) for lang in extra_languages]
        extra_languages = [lang for lang in extra_languages if lang]
        if _is_indian_accent(v_accent):
            extra_languages = list(
                dict.fromkeys([*extra_languages, *INDIAN_LANGUAGE_FACETS])
            )
        all_voices.append(
            {
                "voice_id": voice_id,
                "name": display_name,
                "description": item.get("description") or tagline or None,
                "accent": v_accent,
                "gender": _normalize_gender(item.get("gender")),
                "language": v_language,
                "languages": extra_languages or None,
                "preview_url": item.get("preview_url") or item.get("preview_file_url"),
            }
        )
    voices = [
        voice
        for voice in all_voices
        if _matches_filters(
            name=voice["name"],
            voice_id=voice["voice_id"],
            gender=voice.get("gender"),
            accent=voice.get("accent"),
            language=voice.get("language"),
            extra_languages=voice.get("languages") or [],
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        )
    ]
    return {
        "provider": "cartesia",
        "voices": voices,
        "facets": _facets(all_voices, extra_languages=list((result.get("facets") or {}).get("languages") or [])),
    }


def list_sarvam_voices(
    *,
    model: str | None = None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
) -> dict[str, Any]:
    catalog = (
        SARVAM_V2_VOICE_CATALOG
        if (model or "").lower() == "bulbul:v2"
        else SARVAM_V3_VOICE_CATALOG
    )
    all_voices: list[dict[str, Any]] = []
    for voice_id, display_name, v_gender, style in catalog:
        name = f"{display_name} - {style}"
        all_voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": style,
                "accent": "in",
                "gender": v_gender,
                "language": canonicalize_language(language) or "hi-IN",
                "languages": list(SARVAM_LANGUAGES),
                "preview_url": None,
            }
        )
    voices = [
        voice
        for voice in all_voices
        if _matches_filters(
            name=voice["name"],
            voice_id=voice["voice_id"],
            gender=voice.get("gender"),
            accent=voice.get("accent"),
            language="hi-IN",
            extra_languages=list(SARVAM_LANGUAGES),
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        )
    ]
    return {
        "provider": "sarvam",
        "voices": voices,
        "facets": _facets(all_voices, extra_languages=list(SARVAM_LANGUAGES)),
    }


async def list_murf_voices(
    *,
    organization_id: int | None,
    model: str | None = None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
    api_key_override: str | None = None,
) -> dict[str, Any]:
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="murf",
        api_key_override=api_key_override,
    )

    params: dict[str, str] = {}
    if model:
        lowered = model.lower()
        if "falcon" in lowered:
            params["model"] = "FALCON"
        elif "gen2" in lowered or "gen-2" in lowered:
            params["model"] = "GEN2"

    headers = {"Accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    data: Any = None
    last_error = ""
    try:
        async with aiohttp.ClientSession() as session:
            for url in MURF_VOICES_URLS:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        break
                    last_error = await resp.text()
                    logger.warning(
                        f"Murf voices {url} returned {resp.status}: {last_error[:200]}"
                    )
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Murf API: {exc}"
        ) from exc

    if data is None:
        raise HTTPException(
            status_code=502,
            detail=f"Murf API returned an error: {last_error[:200]}",
        )

    raw_voices = data if isinstance(data, list) else data.get("voices", [])
    all_voices: list[dict[str, Any]] = []
    for item in raw_voices:
        voice_id = str(
            item.get("voiceId") or item.get("voice_id") or item.get("id") or ""
        )
        if not voice_id:
            continue
        name = str(item.get("displayName") or item.get("name") or voice_id)
        description = item.get("description")
        if description:
            name = f"{name} - {description}" if description not in name else name
        v_gender = _normalize_gender(item.get("gender"))
        v_locale = canonicalize_language(
            item.get("locale") or item.get("language") or item.get("displayLanguage")
        ) or _locale_from_voice_id(voice_id)
        supported = item.get("supportedLocales") or {}
        extra_languages = [
            canonicalize_language(key) for key in (supported.keys() if isinstance(supported, dict) else [])
        ]
        extra_languages = [lang for lang in extra_languages if lang]
        v_accent = item.get("accent") or _accent_from_country_or_locale(None, v_locale)
        if isinstance(v_accent, str) and "india" in v_accent.lower():
            v_accent = "in"
        all_voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": description,
                "accent": v_accent,
                "gender": v_gender,
                "language": v_locale,
                "languages": extra_languages or None,
                "preview_url": item.get("sampleAudioUrl") or item.get("preview_url"),
            }
        )
    voices = [
        voice
        for voice in all_voices
        if _matches_filters(
            name=voice["name"],
            voice_id=voice["voice_id"],
            gender=voice.get("gender"),
            accent=voice.get("accent"),
            language=voice.get("language"),
            extra_languages=voice.get("languages") or [],
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        )
    ]
    return {"provider": "murf", "voices": voices, "facets": _facets(all_voices)}


async def list_smallest_voices(
    *,
    model: str | None = None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
) -> dict[str, Any]:
    resolved_model = (model or "lightning_v3.1").replace("-", "_")
    model_slug = resolved_model.replace("_", "-")
    url = SMALLEST_VOICES_URL.format(model=model_slug)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        f"Smallest AI voices API error {resp.status}: {body[:200]}"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"Smallest AI API returned {resp.status}: {body[:200]}",
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Smallest AI API: {exc}"
        ) from exc

    raw_voices = data.get("voices", []) if isinstance(data, dict) else data
    all_voices: list[dict[str, Any]] = []
    for item in raw_voices:
        tags = item.get("tags") or {}
        voice_id = str(
            item.get("voiceId") or item.get("voice_id") or item.get("id") or ""
        )
        if not voice_id:
            continue
        display = str(item.get("displayName") or item.get("name") or voice_id)
        style = tags.get("style") or tags.get("mood") or item.get("description") or ""
        name = f"{display} - {style}" if style and style not in display else display
        v_gender = _normalize_gender(tags.get("gender") or item.get("gender"))
        v_accent = tags.get("accent") or item.get("accent")
        if isinstance(v_accent, str) and "india" in v_accent.lower():
            v_accent = "in"
        raw_langs = tags.get("language") or tags.get("languages") or []
        if isinstance(raw_langs, str):
            raw_langs = [raw_langs]
        iso_langs = [
            canonicalize_language(
                SMALLEST_LANGUAGE_NAME_TO_ISO.get(str(lang).lower(), str(lang).lower())
            )
            for lang in raw_langs
        ]
        iso_langs = [item for item in iso_langs if item]
        raw_recommended = tags.get("recommendedLanguages") or []
        recommended_iso = [
            canonicalize_language(
                SMALLEST_LANGUAGE_NAME_TO_ISO.get(str(lang).lower(), str(lang).lower())
            )
            for lang in raw_recommended
        ]
        recommended_iso = [item for item in recommended_iso if item]
        extra_languages = list(dict.fromkeys([*recommended_iso, *iso_langs]))
        primary_lang = (
            recommended_iso[0]
            if recommended_iso
            else (iso_langs[0] if iso_langs else None)
        )
        all_voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": style or None,
                "accent": v_accent,
                "gender": v_gender,
                "language": primary_lang,
                "languages": extra_languages or None,
                "preview_url": item.get("previewUrl")
                or item.get("preview_url")
                or tags.get("previewUrl"),
            }
        )
    voices = [
        voice
        for voice in all_voices
        if _matches_filters(
            name=voice["name"],
            voice_id=voice["voice_id"],
            gender=voice.get("gender"),
            accent=voice.get("accent"),
            language=voice.get("language"),
            extra_languages=voice.get("languages") or [],
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        )
    ]
    return {"provider": "smallest", "voices": voices, "facets": _facets(all_voices)}


async def synthesize_voice_preview(
    *,
    provider: str,
    voice_id: str,
    organization_id: int | None,
    model: str | None = None,
    language: str | None = None,
    api_key_override: str | None = None,
    preview_url: str | None = None,
) -> tuple[bytes, str]:
    """Return (audio_bytes, content_type) for an on-demand sample clip."""
    catalog_clip = await _download_allowed_preview(preview_url)
    if catalog_clip:
        return catalog_clip
    if provider == "sarvam":
        return await _preview_sarvam(
            voice_id=voice_id,
            organization_id=organization_id,
            model=model,
            language=language,
            api_key_override=api_key_override,
        )
    if provider == "cartesia":
        return await _preview_cartesia(
            voice_id=voice_id,
            organization_id=organization_id,
            model=model,
            language=language,
            api_key_override=api_key_override,
        )
    if provider == "murf":
        return await _preview_murf(
            voice_id=voice_id,
            organization_id=organization_id,
            model=model,
            language=language,
            api_key_override=api_key_override,
        )
    if provider == "smallest":
        return await _preview_smallest(
            voice_id=voice_id,
            organization_id=organization_id,
            model=model,
            language=language,
            api_key_override=api_key_override,
        )
    raise HTTPException(status_code=400, detail=f"Preview is not supported for {provider}")


async def _preview_sarvam(
    *,
    voice_id: str,
    organization_id: int | None,
    model: str | None,
    language: str | None,
    api_key_override: str | None,
) -> tuple[bytes, str]:
    import base64

    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="sarvam",
        api_key_override=api_key_override,
    )
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Add a Sarvam API key to play a voice sample.",
        )
    target_language = canonicalize_language(language) or "hi-IN"
    payload = {
        "text": _preview_text(target_language),
        "target_language_code": target_language if "-" in target_language else "hi-IN",
        "speaker": (voice_id or "").strip().lower(),
        "model": model or "bulbul:v3",
    }
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SARVAM_TTS_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise HTTPException(
                        status_code=502,
                        detail=f"Could not play this sample ({resp.status}).",
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Sarvam API: {exc}"
        ) from exc
    audios = data.get("audios") if isinstance(data, dict) else None
    if not audios:
        raise HTTPException(status_code=502, detail="Sarvam preview returned no audio")
    return base64.b64decode(audios[0]), "audio/wav"


async def _preview_cartesia(
    *,
    voice_id: str,
    organization_id: int | None,
    model: str | None,
    language: str | None,
    api_key_override: str | None,
) -> tuple[bytes, str]:
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="cartesia",
        api_key_override=api_key_override,
    )
    headers = {
        "Cartesia-Version": CARTESIA_VERSION,
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    # Prefer the hosted preview clip so playback works without CORS issues.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{CARTESIA_VOICES_URL}/{voice_id}",
                headers=headers,
                params={"expand[]": "preview_file_url"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    preview_url = payload.get("preview_file_url") or payload.get(
                        "preview_url"
                    )
                    if preview_url:
                        downloaded = await _download_audio(preview_url)
                        if downloaded:
                            return downloaded
    except aiohttp.ClientError:
        logger.debug("Cartesia preview clip lookup failed", exc_info=True)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Add a Cartesia API key to play a voice sample.",
        )
    lang = canonicalize_language(language) or "en"
    lang = lang.split("-")[0]
    body = {
        "model_id": model or "sonic-3",
        "transcript": _preview_text(language),
        "voice": {"mode": "id", "id": voice_id},
        "language": lang,
        "output_format": {
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": 24000,
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CARTESIA_TTS_BYTES_URL,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        status_code=502,
                        detail="Could not play this Cartesia sample.",
                    )
                return await resp.read(), "audio/wav"
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Cartesia API: {exc}"
        ) from exc


async def _preview_murf(
    *,
    voice_id: str,
    organization_id: int | None,
    model: str | None,
    language: str | None,
    api_key_override: str | None,
) -> tuple[bytes, str]:
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="murf",
        api_key_override=api_key_override,
    )
    headers = {"Accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    payload = {
        "text": _preview_text(language),
        "voiceId": voice_id,
        "format": "MP3",
        "model": "falcon-2" if not model or "falcon" in model.lower() else "gen2",
    }
    locale = canonicalize_language(language)
    if locale:
        payload["locale"] = locale
    last_error = ""
    try:
        async with aiohttp.ClientSession() as session:
            for url in MURF_GENERATE_URLS:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        last_error = await resp.text()
                        continue
                    content_type = (resp.headers.get("Content-Type") or "").split(";")[0]
                    if content_type.startswith("audio/"):
                        return await resp.read(), content_type
                    data = await resp.json()
                    audio_url = data.get("audioFile") or data.get("audio_file")
                    if audio_url:
                        downloaded = await _download_audio(audio_url)
                        if downloaded:
                            return downloaded
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Murf API: {exc}"
        ) from exc
    raise HTTPException(
        status_code=502,
        detail=f"Could not play this Murf sample. {last_error[:160]}".strip(),
    )


async def _preview_smallest(
    *,
    voice_id: str,
    organization_id: int | None,
    model: str | None,
    language: str | None,
    api_key_override: str | None,
) -> tuple[bytes, str]:
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="smallest",
        api_key_override=api_key_override,
    )
    resolved_model = (model or "lightning_v3.1").replace("-", "_")
    model_slug = resolved_model.replace("_", "-")
    lang = canonicalize_language(language) or "en"
    lang = lang.split("-")[0]
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "text": _preview_text(language),
        "voice_id": voice_id,
        "language": lang,
    }
    url = SMALLEST_SPEECH_URL.format(model=model_slug)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        status_code=502,
                        detail="Could not play this Smallest sample.",
                    )
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0]
                if content_type.startswith("audio/"):
                    return await resp.read(), content_type
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Smallest AI API: {exc}"
        ) from exc
    import base64

    audio_b64 = None
    if isinstance(data, dict):
        audio_b64 = data.get("audio") or data.get("data")
        audio_url = data.get("url") or data.get("previewUrl")
        if audio_url:
            downloaded = await _download_audio(audio_url)
            if downloaded:
                return downloaded
    if not audio_b64:
        raise HTTPException(status_code=502, detail="Smallest preview returned no audio")
    return base64.b64decode(audio_b64), "audio/wav"
