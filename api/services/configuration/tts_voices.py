"""Fetch live TTS voice catalogs from provider APIs.

Selecting Cartesia / Sarvam / Murf / Smallest loads the full catalog
immediately. The user's API key is optional for listing and is used later
for actual TTS. Indian regional endpoints are preferred where published.
"""

from __future__ import annotations

import os
from typing import Any

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.services.configuration.masking import contains_masked_key
from api.services.configuration.options.sarvam import (
    SARVAM_V2_VOICE_CATALOG,
    SARVAM_V3_VOICE_CATALOG,
)
from api.services.configuration.options.smallest import SMALLEST_LANGUAGE_NAME_TO_ISO

CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices"
CARTESIA_VERSION = "2026-08-14"
MURF_VOICES_URLS = (
    "https://in.api.murf.ai/v1/speech/voices",
    "https://api.murf.ai/v1/speech/voices",
)
SMALLEST_VOICES_URL = "https://api.india.smallest.ai/waves/v1/{model}/get_voices"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

LIVE_VOICE_PROVIDERS = frozenset({"cartesia", "sarvam", "murf", "smallest"})
_PLATFORM_API_KEY_ENV = {
    "cartesia": "CARTESIA_API_KEY",
    "murf": "MURF_API_KEY",
    "sarvam": "SARVAM_API_KEY",
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
    if filter_language and language:
        lang = language.lower()
        wanted = filter_language.lower()
        if not (lang == wanted or lang.startswith(wanted) or wanted.startswith(lang)):
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


def _facets(voices: list[dict[str, Any]]) -> dict[str, list[str]]:
    genders: set[str] = set()
    accents: set[str] = set()
    languages: set[str] = set()
    for voice in voices:
        if voice.get("gender"):
            genders.add(str(voice["gender"]).lower())
        if voice.get("accent"):
            accents.add(str(voice["accent"]).lower())
        if voice.get("language"):
            languages.add(str(voice["language"]).lower())
    return {
        "genders": sorted(genders),
        "accents": sorted(accents),
        "languages": sorted(languages),
    }


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
                params: dict[str, Any] = {"limit": 100}
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

    voices: list[dict[str, Any]] = []
    for item in raw_voices:
        voice_id = str(item.get("id") or item.get("voice_id") or "")
        if not voice_id:
            continue
        name = str(item.get("name") or voice_id)
        tagline = (item.get("tagline") or "").strip()
        display_name = f"{name} - {tagline}" if tagline else name
        v_gender = _normalize_gender(item.get("gender"))
        v_language = item.get("language")
        v_accent = _accent_from_country_or_locale(
            item.get("country"), v_language if isinstance(v_language, str) else None
        )
        if not _matches_filters(
            name=display_name,
            voice_id=voice_id,
            gender=v_gender,
            accent=v_accent,
            language=str(v_language) if v_language else None,
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        ):
            continue
        voices.append(
            {
                "voice_id": voice_id,
                "name": display_name,
                "description": item.get("description") or tagline or None,
                "accent": v_accent,
                "gender": v_gender,
                "language": v_language,
                "preview_url": item.get("preview_file_url")
                or item.get("preview_url"),
            }
        )
    return {"provider": "cartesia", "voices": voices, "facets": _facets(voices)}


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
    voices: list[dict[str, Any]] = []
    for item in raw_voices:
        voice_id = str(item.get("voice_id") or item.get("id") or "")
        if not voice_id:
            continue
        name = str(item.get("name") or voice_id)
        tagline = (item.get("tagline") or item.get("description") or "").strip()
        display_name = f"{name} - {tagline}" if tagline and tagline not in name else name
        voices.append(
            {
                "voice_id": voice_id,
                "name": display_name,
                "description": item.get("description") or tagline or None,
                "accent": item.get("accent"),
                "gender": _normalize_gender(item.get("gender")),
                "language": item.get("language"),
                "preview_url": item.get("preview_url") or item.get("preview_file_url"),
            }
        )
    return {
        "provider": "cartesia",
        "voices": voices,
        "facets": result.get("facets") or _facets(voices),
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
    voices: list[dict[str, Any]] = []
    for voice_id, display_name, v_gender, style in catalog:
        name = f"{display_name} - {style}"
        if not _matches_filters(
            name=name,
            voice_id=voice_id,
            gender=v_gender,
            accent="in",
            language=language or "hi-IN",
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=None,
        ):
            continue
        voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": style,
                "accent": "in",
                "gender": v_gender,
                "language": "hi-IN",
                "preview_url": None,
            }
        )
    return {"provider": "sarvam", "voices": voices, "facets": _facets(voices)}


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
    voices: list[dict[str, Any]] = []
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
        v_locale = item.get("locale") or item.get("language") or item.get("displayLanguage")
        v_accent = item.get("accent") or _accent_from_country_or_locale(None, v_locale)
        if isinstance(v_accent, str) and "india" in v_accent.lower():
            v_accent = "in"
        if not _matches_filters(
            name=name,
            voice_id=voice_id,
            gender=v_gender,
            accent=v_accent,
            language=str(v_locale) if v_locale else None,
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=language,
        ):
            continue
        voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": description,
                "accent": v_accent,
                "gender": v_gender,
                "language": v_locale,
                "preview_url": item.get("sampleAudioUrl") or item.get("preview_url"),
            }
        )
    return {"provider": "murf", "voices": voices, "facets": _facets(voices)}


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
    voices: list[dict[str, Any]] = []
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
            SMALLEST_LANGUAGE_NAME_TO_ISO.get(str(lang).lower(), str(lang).lower())
            for lang in raw_langs
        ]
        raw_recommended = tags.get("recommendedLanguages") or []
        recommended_iso = [
            SMALLEST_LANGUAGE_NAME_TO_ISO.get(str(lang).lower(), str(lang).lower())
            for lang in raw_recommended
        ]
        primary_lang = (
            recommended_iso[0]
            if recommended_iso
            else (iso_langs[0] if iso_langs else None)
        )
        # Do not drop voices just because they also tag other Indic languages.
        # Only apply a language filter when the caller explicitly asked for one.
        if language:
            wanted = language.lower()
            supported = set(recommended_iso or iso_langs)
            if supported and wanted not in supported and not any(
                item.startswith(wanted) or wanted.startswith(item) for item in supported
            ):
                continue
        if not _matches_filters(
            name=name,
            voice_id=voice_id,
            gender=v_gender,
            accent=v_accent,
            language=primary_lang,
            q=q,
            filter_gender=gender,
            filter_accent=accent,
            filter_language=None,
        ):
            continue
        voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "description": style or None,
                "accent": v_accent,
                "gender": v_gender,
                "language": primary_lang,
                "preview_url": item.get("previewUrl")
                or item.get("preview_url")
                or tags.get("previewUrl"),
            }
        )
    return {"provider": "smallest", "voices": voices, "facets": _facets(voices)}


async def synthesize_voice_preview(
    *,
    provider: str,
    voice_id: str,
    organization_id: int | None,
    model: str | None = None,
    language: str | None = None,
    api_key_override: str | None = None,
) -> tuple[bytes, str]:
    """Return (audio_bytes, content_type) for an on-demand sample clip."""
    if provider != "sarvam":
        raise HTTPException(
            status_code=400,
            detail="On-demand preview is only required for Sarvam. Other providers return preview_url.",
        )
    api_key = await resolve_tts_api_key(
        organization_id=organization_id,
        provider="sarvam",
        api_key_override=api_key_override,
    )
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No Sarvam API key configured. Save your Sarvam API key in Voice settings first.",
        )
    speaker = (voice_id or "").strip().lower()
    tts_model = model or "bulbul:v3"
    target_language = language or "hi-IN"
    payload = {
        "text": "Namaste, this is a sample of my voice.",
        "target_language_code": target_language,
        "speaker": speaker,
        "model": tts_model,
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
                        detail=f"Sarvam preview failed ({resp.status}): {body[:200]}",
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Sarvam API: {exc}"
        ) from exc

    import base64

    audios = data.get("audios") if isinstance(data, dict) else None
    if not audios:
        raise HTTPException(status_code=502, detail="Sarvam preview returned no audio")
    return base64.b64decode(audios[0]), "audio/wav"
