"""Exotel telephony provider package."""

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import ExotelConfigurationRequest, ExotelConfigurationResponse
from .provider import ExotelProvider
from .transport import create_transport

_UI_METADATA = ProviderUIMetadata(
    display_name="Exotel",
    docs_url="https://developer.exotel.com/api/",
    fields=[
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="text",
            sensitive=True,
            description="Exotel API Key (from Settings > API Credentials in your dashboard)",
        ),
        ProviderUIField(
            name="api_token",
            label="API Token",
            type="password",
            sensitive=True,
            description="Exotel API Token",
        ),
        ProviderUIField(
            name="account_sid",
            label="Account SID",
            type="text",
            description="Exotel Account SID (e.g., exoteldemoaccount)",
        ),
        ProviderUIField(
            name="from_numbers",
            label="ExoPhone Numbers",
            type="string-array",
            description="ExoPhone numbers to use for outbound calls (e.g., 0XXXXXXXXXX)",
        ),
        ProviderUIField(
            name="subdomain",
            label="API Subdomain",
            type="text",
            required=False,
            description=(
                "Leave blank to use the default (api.exotel.com). "
                "Set only if your Exotel account uses a dedicated cluster subdomain."
            ),
            placeholder="api.exotel.com",
        ),
        ProviderUIField(
            name="app_id",
            label="Exotel App ID / Flow ID",
            type="text",
            required=False,
            description=(
                "The App ID of your Voicebot Applet from Exotel Dashboard (App Bazaar > My Apps). "
                "Required by Exotel Calls/connect API to route calls through your Voicebot flow."
            ),
            placeholder="e.g. 123456",
        ),
    ],
)


def _config_loader(value: dict) -> dict:
    return {
        "provider": "exotel",
        "api_key": value.get("api_key"),
        "api_token": value.get("api_token"),
        "account_sid": value.get("account_sid"),
        "from_numbers": value.get("from_numbers", []),
        "subdomain": value.get("subdomain"),
        "app_id": value.get("app_id"),
    }


SPEC = ProviderSpec(
    name="exotel",
    provider_cls=ExotelProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=ExotelConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=ExotelConfigurationResponse,
    account_id_credential_field="account_sid",
)

register(SPEC)

__all__ = [
    "SPEC",
    "ExotelConfigurationRequest",
    "ExotelConfigurationResponse",
    "ExotelProvider",
    "create_transport",
]
