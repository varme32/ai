from datetime import datetime, timedelta
from typing import List, Literal, Optional, TypedDict, Union

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, ValidationError

from api.db import db_client
from api.db.models import (
    UserModel,
)
from api.schemas.onboarding_state import OnboardingState, OnboardingStateUpdate
from api.schemas.workflow_configurations import (
    WorkflowConfigurationDefaults,
    get_default_workflow_configurations,
)
from api.services.auth.depends import get_user
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
    get_resolved_ai_model_configuration,
    update_organization_ai_model_configuration_last_validated_at,
    upsert_organization_ai_model_configuration_v2,
)
from api.services.configuration.check_validity import (
    APIKeyStatusResponse,
    UserConfigurationValidator,
)
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.masking import check_for_masked_keys, mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.mps_service_key_client import mps_service_key_client
from api.services.organization_preferences import (
    get_organization_preferences,
    upsert_organization_preferences,
)
from api.services.user_onboarding import (
    get_onboarding_state,
    update_onboarding_state,
)

router = APIRouter(prefix="/user")


class AuthUserResponse(TypedDict):
    id: int
    is_superuser: bool


class DefaultConfigurationsResponse(BaseModel):
    llm: dict[str, dict]
    tts: dict[str, dict]
    stt: dict[str, dict]
    embeddings: dict[str, dict]
    realtime: dict[str, dict]
    default_providers: dict[str, str]
    workflow_configurations: WorkflowConfigurationDefaults


@router.get("/configurations/defaults")
async def get_default_configurations() -> DefaultConfigurationsResponse:
    configurations = {
        "llm": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.LLM].items()
        },
        "tts": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.TTS].items()
        },
        "stt": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.STT].items()
        },
        "embeddings": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.EMBEDDINGS].items()
        },
        "realtime": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.REALTIME].items()
        },
        "default_providers": DEFAULT_SERVICE_PROVIDERS,
        "workflow_configurations": get_default_workflow_configurations(),
    }
    return DefaultConfigurationsResponse(**configurations)


@router.get("/auth/user")
async def get_auth_user(
    user: UserModel = Depends(get_user),
) -> AuthUserResponse:
    return {
        "id": user.id,
        "is_superuser": user.is_superuser,
    }


class UserConfigurationRequestResponseSchema(BaseModel):
    llm: dict[str, Union[str, float, list[str], None]] | None = None
    tts: dict[str, Union[str, float, list[str], None]] | None = None
    stt: dict[str, Union[str, float, list[str], None]] | None = None
    embeddings: dict[str, Union[str, float, list[str], None]] | None = None
    realtime: dict[str, Union[str, float, list[str], None]] | None = None
    is_realtime: bool | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    organization_pricing: dict[str, Union[float, str, bool]] | None = None


def _is_validation_cache_stale(
    last_validated_at: datetime | None,
    validity_ttl_seconds: int,
) -> bool:
    if last_validated_at is None:
        return True

    has_timezone = (
        last_validated_at.tzinfo is not None
        and last_validated_at.utcoffset() is not None
    )
    if has_timezone:
        now = datetime.now(last_validated_at.tzinfo)
    else:
        now = datetime.now()
    return last_validated_at < now - timedelta(seconds=validity_ttl_seconds)


@router.get("/configurations/user")
async def get_user_configurations(
    user: UserModel = Depends(get_user),
) -> UserConfigurationRequestResponseSchema:
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=user.selected_organization_id,
    )
    masked_config = mask_user_config(resolved_config.effective)
    if user.selected_organization_id:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if preferences.test_phone_number is not None:
            masked_config["test_phone_number"] = preferences.test_phone_number
        if preferences.timezone is not None:
            masked_config["timezone"] = preferences.timezone

    # Add organization pricing info if available
    if user.selected_organization_id:
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if org and org.price_per_second_usd is not None:
            masked_config["organization_pricing"] = {
                "price_per_second_usd": org.price_per_second_usd,
                "currency": "USD",
                "billing_enabled": True,
            }

    return masked_config


@router.put("/configurations/user")
async def update_user_configurations(
    request: UserConfigurationRequestResponseSchema,
    user: UserModel = Depends(get_user),
) -> UserConfigurationRequestResponseSchema:
    existing_config = (
        await get_resolved_ai_model_configuration(
            organization_id=user.selected_organization_id,
        )
    ).effective

    incoming_dict = request.model_dump(exclude_none=True)

    # Remove organization_pricing from incoming dict as it's read-only
    incoming_dict.pop("organization_pricing", None)
    preferences_update = {
        key: incoming_dict.pop(key)
        for key in ("test_phone_number", "timezone")
        if key in incoming_dict
    }

    if incoming_dict:
        if not user.selected_organization_id:
            raise HTTPException(status_code=400, detail="No organization selected")

        # Merge via helper
        try:
            user_configurations = merge_user_configurations(
                existing_config, incoming_dict
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        try:
            check_for_masked_keys(user_configurations)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            validator = UserConfigurationValidator()
            await validator.validate(
                user_configurations,
                organization_id=user.selected_organization_id,
                created_by=user.provider_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=e.args[0])

        try:
            organization_configuration = convert_legacy_ai_model_configuration_to_v2(
                user_configurations
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        await upsert_organization_ai_model_configuration_v2(
            user.selected_organization_id,
            organization_configuration,
        )
    else:
        user_configurations = existing_config

    if user.selected_organization_id and preferences_update:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if "test_phone_number" in preferences_update:
            preferences.test_phone_number = preferences_update["test_phone_number"]
        if "timezone" in preferences_update:
            preferences.timezone = preferences_update["timezone"]
        await upsert_organization_preferences(
            user.selected_organization_id,
            preferences,
        )

    # Return masked version of updated config
    masked_config = mask_user_config(user_configurations)
    if user.selected_organization_id:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if preferences.test_phone_number is not None:
            masked_config["test_phone_number"] = preferences.test_phone_number
        if preferences.timezone is not None:
            masked_config["timezone"] = preferences.timezone

    # Add organization pricing info if available
    if user.selected_organization_id:
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if org and org.price_per_second_usd is not None:
            masked_config["organization_pricing"] = {
                "price_per_second_usd": org.price_per_second_usd,
                "currency": "USD",
                "billing_enabled": True,
            }

    return masked_config


@router.get("/onboarding-state")
async def get_user_onboarding_state(
    user: UserModel = Depends(get_user),
) -> OnboardingState:
    return await get_onboarding_state(user.id)


@router.put("/onboarding-state")
async def update_user_onboarding_state(
    request: OnboardingStateUpdate,
    user: UserModel = Depends(get_user),
) -> OnboardingState:
    return await update_onboarding_state(user.id, request)


@router.get("/configurations/user/validate")
async def validate_user_configurations(
    validity_ttl_seconds: int = Query(default=60, ge=0, le=86400),
    user: UserModel = Depends(get_user),
) -> APIKeyStatusResponse:
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=user.selected_organization_id,
    )
    configurations = resolved_config.effective

    if _is_validation_cache_stale(
        configurations.last_validated_at,
        validity_ttl_seconds,
    ):
        validator = UserConfigurationValidator()
        try:
            status = await validator.validate(
                configurations,
                organization_id=user.selected_organization_id,
                created_by=user.provider_id,
            )
            if (
                resolved_config.source == "organization_v2"
                and user.selected_organization_id is not None
            ):
                await update_organization_ai_model_configuration_last_validated_at(
                    user.selected_organization_id
                )
            return status
        except ValueError as e:
            raise HTTPException(status_code=422, detail=e.args[0])
    else:
        return {"status": []}


# API Key Management Endpoints
class APIKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None


class CreateAPIKeyRequest(BaseModel):
    name: str


class CreateAPIKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    api_key: str  # Only returned when creating a new key
    created_at: datetime


@router.get("/api-keys")
async def get_api_keys(
    include_archived: bool = Query(default=False),
    user: UserModel = Depends(get_user),
) -> List[APIKeyResponse]:
    """Get all API keys for the user's selected organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=include_archived
    )

    return [
        APIKeyResponse(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            is_active=key.is_active,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            archived_at=key.archived_at,
        )
        for key in api_keys
    ]


@router.post("/api-keys")
async def create_api_key(
    request: CreateAPIKeyRequest,
    user: UserModel = Depends(get_user),
) -> CreateAPIKeyResponse:
    """Create a new API key for the user's selected organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    api_key, raw_key = await db_client.create_api_key(
        organization_id=user.selected_organization_id,
        name=request.name,
        created_by=user.id,
    )

    return CreateAPIKeyResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        api_key=raw_key,
        created_at=api_key.created_at,
    )


@router.delete("/api-keys/{api_key_id}")
async def archive_api_key(
    api_key_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """Archive an API key (soft delete)."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Verify the API key belongs to the user's organization
    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=True
    )
    if not any(key.id == api_key_id for key in api_keys):
        raise HTTPException(status_code=404, detail="API key not found")

    success = await db_client.archive_api_key(api_key_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to archive API key")

    return {"success": True, "message": "API key archived successfully"}


@router.put("/api-keys/{api_key_id}/reactivate")
async def reactivate_api_key(
    api_key_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """Reactivate an archived API key."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Verify the API key belongs to the user's organization
    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=True
    )
    if not any(key.id == api_key_id for key in api_keys):
        raise HTTPException(status_code=404, detail="API key not found")

    success = await db_client.reactivate_api_key(api_key_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reactivate API key")

    return {"success": True, "message": "API key reactivated successfully"}


# Voice Configuration Endpoints
TTSProvider = Literal["elevenlabs", "deepgram", "sarvam", "cartesia", "dograh", "rime", "murf", "smallest"]


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: Optional[str] = None
    accent: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    preview_url: Optional[str] = None


class VoiceFacets(BaseModel):
    """Distinct selector values across a provider's full voice catalog."""

    genders: List[str] = []
    accents: List[str] = []
    languages: List[str] = []


class VoicesResponse(BaseModel):
    provider: str
    voices: List[VoiceInfo]
    facets: Optional[VoiceFacets] = None


@router.get("/configurations/voices/{provider}")
async def get_voices(
    provider: TTSProvider,
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    api_key: Optional[str] = None,
    user: UserModel = Depends(get_user),
) -> VoicesResponse:
    """Get available voices for a TTS provider."""

    # Murf uses the user's own API key stored in their config — no MPS proxy needed
    if provider == "murf":
        return await _get_murf_voices(
            organization_id=user.selected_organization_id,
            model=model,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
            api_key_override=api_key,
        )

    # Smallest AI voices are fetched from the public get_voices API (no auth required)
    if provider == "smallest":
        return await _get_smallest_voices(
            model=model,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
        )

    try:
        result = await mps_service_key_client.get_voices(
            provider=provider,
            model=model,
            language=language,
            q=q,
            gender=gender,
            accent=accent,
            organization_id=user.selected_organization_id,
            created_by=user.provider_id,
        )
        return VoicesResponse(
            provider=result.get("provider", provider),
            voices=[VoiceInfo(**voice) for voice in result.get("voices", [])],
            facets=result.get("facets"),
        )
    except Exception as e:
        logger.error(f"Failed to fetch voices for {provider}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch voices for {provider}",
        )


async def _get_murf_voices(
    organization_id: int | None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    api_key_override: Optional[str] = None,
) -> VoicesResponse:
    """Fetch voices directly from Murf AI API using the org's stored TTS API key,
    filtered by model (FALCON vs GEN2) and other attributes.

    If ``api_key_override`` is provided it is used directly, allowing the
    frontend to pass the key typed into the form before it has been saved.
    """
    from api.services.configuration.ai_model_configuration import (
        get_resolved_ai_model_configuration,
    )

    api_key: str | None = api_key_override.strip() if api_key_override else None

    if not api_key:
        # Fall back to the key stored in the org's saved TTS configuration
        resolved = await get_resolved_ai_model_configuration(organization_id=organization_id)
        tts_config = resolved.effective.tts if resolved.effective else None
        if tts_config and getattr(tts_config, "provider", None) == "murf":
            raw_key = getattr(tts_config, "api_key", None)
            if isinstance(raw_key, list):
                api_key = raw_key[0] if raw_key else None
            else:
                api_key = raw_key

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No Murf API key configured. Please add your Murf API key in TTS settings first.",
        )

    # Map frontend/configuration model identifier to Murf API model query param:
    # "falcon-2" -> "FALCON"
    # "gen2" / "GEN2" -> "GEN2"
    params: dict[str, str] = {}
    if model:
        m_lower = model.lower()
        if "falcon" in m_lower:
            params["model"] = "FALCON"
        elif "gen2" in m_lower or "gen-2" in m_lower:
            params["model"] = "GEN2"

    murf_url = "https://api.murf.ai/v1/speech/voices"
    headers = {"api-key": api_key, "Accept": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(murf_url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Murf voices API error {resp.status}: {body}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Murf API returned {resp.status}: {body[:200]}",
                    )
                data = await resp.json()
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact Murf API: {e}")

    # Murf response: list of voice objects directly or under a key
    raw_voices = data if isinstance(data, list) else data.get("voices", [])

    voices: list[VoiceInfo] = []
    genders_set: set[str] = set()
    accents_set: set[str] = set()
    languages_set: set[str] = set()

    for v in raw_voices:
        voice_id = v.get("voiceId") or v.get("voice_id") or v.get("id", "")
        name = v.get("displayName") or v.get("name") or voice_id
        v_gender = v.get("gender")
        v_accent = v.get("accent")
        v_locale = v.get("locale") or v.get("language")

        if v_gender:
            genders_set.add(v_gender.lower())
        if v_accent:
            accents_set.add(v_accent.lower())
        if v_locale:
            languages_set.add(v_locale.lower())

        # Client-side / parameter filters
        if q and q.lower() not in name.lower() and q.lower() not in voice_id.lower():
            continue
        if gender and v_gender and v_gender.lower() != gender.lower():
            continue
        if accent and v_accent and v_accent.lower() != accent.lower():
            continue
        if language and v_locale and not v_locale.lower().startswith(language.lower()):
            continue

        voices.append(
            VoiceInfo(
                voice_id=voice_id,
                name=name,
                description=v.get("description"),
                accent=v_accent,
                gender=v_gender,
                language=v_locale,
                preview_url=v.get("sampleAudioUrl") or v.get("preview_url"),
            )
        )

    facets = VoiceFacets(
        genders=sorted(list(genders_set)),
        accents=sorted(list(accents_set)),
        languages=sorted(list(languages_set)),
    )

    return VoicesResponse(provider="murf", voices=voices, facets=facets)


async def _get_smallest_voices(
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
) -> VoicesResponse:
    """Fetch voices from the public Smallest AI get_voices API.

    The endpoint ``GET https://api.smallest.ai/waves/v1/{model}/get_voices``
    requires no authentication and returns the full voice catalog for that model.
    Language names in the API response (e.g. "telugu") are converted to ISO 639-1
    codes (e.g. "te") for consistency with our settings form.
    """
    from api.services.configuration.options.smallest import SMALLEST_LANGUAGE_NAME_TO_ISO

    resolved_model = (model or "lightning_v3.1").replace("-", "_")
    # Smallest AI URL uses hyphens in the path
    model_slug = resolved_model.replace("_", "-")
    url = f"https://api.india.smallest.ai/waves/v1/{model_slug}/get_voices"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Smallest AI voices API error {resp.status}: {body[:200]}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Smallest AI API returned {resp.status}: {body[:200]}",
                    )
                data = await resp.json()
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact Smallest AI API: {e}")

    raw_voices = data.get("voices", []) if isinstance(data, dict) else data

    voices: list[VoiceInfo] = []
    genders_set: set[str] = set()
    accents_set: set[str] = set()
    languages_set: set[str] = set()

    for v in raw_voices:
        tags = v.get("tags", {})
        voice_id = v.get("voiceId") or v.get("voice_id") or v.get("id", "")
        name = v.get("displayName") or v.get("name") or voice_id
        v_gender = tags.get("gender", "")
        v_accent = tags.get("accent", "")
        # API returns full language names ("telugu"); map to ISO codes ("te")
        raw_langs: list[str] = tags.get("language") or tags.get("languages") or []
        iso_langs = [SMALLEST_LANGUAGE_NAME_TO_ISO.get(ln.lower(), ln.lower()) for ln in raw_langs]

        # Primary display language: use the first recommendedLanguage (best language for this voice),
        # falling back to the requested language if the voice supports it, or iso_langs[0].
        raw_recommended: list[str] = tags.get("recommendedLanguages") or []
        recommended_iso = [SMALLEST_LANGUAGE_NAME_TO_ISO.get(r.lower(), r.lower()) for r in raw_recommended]
        if recommended_iso:
            primary_lang = recommended_iso[0]
        elif language and language.lower() in iso_langs:
            primary_lang = language.lower()
        else:
            primary_lang = iso_langs[0] if iso_langs else None

        if v_gender:
            genders_set.add(v_gender.lower())
        if v_accent:
            accents_set.add(v_accent.lower())
        for iso in iso_langs:
            languages_set.add(iso)

        # Filter by language using recommendedLanguages (precise) or iso_langs (fallback).
        # Smallest AI tags ALL Indic voices with every Indic language (code-switching group),
        # so filtering by iso_langs when language="te" would return all 111 Indian voices.
        # recommendedLanguages only lists the voice's PRIMARY languages, giving the correct ~8 Telugu voices.
        if language:
            lang_lower = language.lower()
            if recommended_iso:
                # Voice has recommended language metadata — use it for precise filtering
                if lang_lower not in recommended_iso:
                    continue
            else:
                # No recommendedLanguages — fall back to full language list
                if lang_lower not in iso_langs:
                    continue

        # Filter by gender
        if gender and v_gender and v_gender.lower() != gender.lower():
            continue
        # Filter by accent
        if accent and v_accent and v_accent.lower() != accent.lower():
            continue
        # Search filter
        if q and q.lower() not in name.lower() and q.lower() not in voice_id.lower():
            continue

        voices.append(
            VoiceInfo(
                voice_id=voice_id,
                name=name,
                gender=v_gender or None,
                accent=v_accent or None,
                language=primary_lang,
            )
        )


    facets = VoiceFacets(
        genders=sorted(list(genders_set)),
        accents=sorted(list(accents_set)),
        languages=sorted(list(languages_set)),
    )

    return VoicesResponse(provider="smallest", voices=voices, facets=facets)

