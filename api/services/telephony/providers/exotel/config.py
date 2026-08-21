"""Exotel telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExotelConfigurationRequest(BaseModel):
    """Request schema for Exotel configuration."""

    provider: Literal["exotel"] = Field(default="exotel")
    api_key: str = Field(..., description="Exotel API Key (found in Exotel dashboard)")
    api_token: str = Field(..., description="Exotel API Token")
    account_sid: str = Field(
        ..., description="Exotel Account SID (e.g., exoteldemoaccount)"
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of ExoPhone numbers to use for outbound calls",
    )
    subdomain: Optional[str] = Field(
        default=None,
        description=(
            "Exotel API subdomain. Defaults to 'api.exotel.com'. "
            "Set to your cluster subdomain if using a dedicated cluster."
        ),
    )


class ExotelConfigurationResponse(BaseModel):
    """Response schema for Exotel configuration with masked sensitive fields."""

    provider: Literal["exotel"] = Field(default="exotel")
    api_key: str  # Masked
    api_token: str  # Masked
    account_sid: str
    from_numbers: List[str]
    subdomain: Optional[str] = None
