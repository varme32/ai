"""TURN credentials endpoint for time-limited WebRTC authentication.

This module implements the TURN REST API credential generation as specified in
draft-uberti-behave-turn-rest-00. It generates ephemeral credentials that are
valid for a configurable TTL and are cryptographically bound to the user.

The credential format:
- Username: {expiration_timestamp}:{user_id}
- Password: base64(hmac-sha1(shared_secret, username))

References:
- https://datatracker.ietf.org/doc/html/draft-uberti-behave-turn-rest-00
- https://github.com/coturn/coturn/wiki/turnserver#turn-rest-api
"""

import base64
import hashlib
import hmac
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

import os

from api.constants import (
    ENVIRONMENT,
    TURN_CREDENTIAL_TTL,
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
    TURN_TLS_PORT,
)

TURN_USERNAME = os.getenv("TURN_USERNAME")
TURN_PASSWORD = os.getenv("TURN_PASSWORD")
from api.db.models import UserModel
from api.enums import Environment
from api.services.auth.depends import get_user

router = APIRouter(prefix="/turn", tags=["turn"])


class TurnCredentialsResponse(BaseModel):
    """Response model for TURN credentials."""

    username: str
    password: str
    ttl: int
    uris: List[str]


class TurnConfigResponse(BaseModel):
    """Response model for TURN configuration status."""

    enabled: bool
    host: Optional[str] = None


def generate_turn_credentials(user_id: str, ttl: int = TURN_CREDENTIAL_TTL) -> dict:
    """Generate time-limited TURN credentials using HMAC-SHA1.

    Args:
        user_id: Unique identifier for the user (for auditing)
        ttl: Time-to-live in seconds for the credentials

    Returns:
        Dictionary with username, password, ttl, and TURN URIs

    Raises:
        ValueError: If TURN_SECRET is not configured
    """
    if not TURN_SECRET:
        raise ValueError("TURN_SECRET is not configured")

    # Calculate expiration timestamp
    expiration = int(time.time()) + ttl

    # Username format: {expiration}:{user_id}
    # This allows the TURN server to:
    # 1. Verify the credential hasn't expired
    # 2. Track usage per user for auditing
    username = f"{expiration}:{user_id}"

    # Password: base64(hmac-sha1(secret, username))
    # This is the standard TURN REST API algorithm
    password = base64.b64encode(
        hmac.new(
            TURN_SECRET.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    # Build TURN URIs
    # Note: aiortc and cloud environments (Render) require TURNS over TLS/TCP (port 443)
    # first, as outbound UDP is frequently blocked or cannot punch NAT holes.
    uris = []

    # 1. Prioritize TURNS (TLS) on port 443 if configured
    if TURN_TLS_PORT:
        uris.extend(
            [
                f"turns:{TURN_HOST}:{TURN_TLS_PORT}?transport=tcp",  # TURN over TLS+TCP
                f"turns:{TURN_HOST}:{TURN_TLS_PORT}",  # TURN over TLS
            ]
        )

    # 2. Add TCP/UDP fallbacks
    uris.extend(
        [
            f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",
            f"turn:{TURN_HOST}:{TURN_PORT}",
        ]
    )

    return {
        "username": username,
        "password": password,
        "ttl": ttl,
        "uris": uris,
    }


@router.get("/credentials", response_model=TurnCredentialsResponse)
async def get_turn_credentials(
    user: UserModel = Depends(get_user),
) -> TurnCredentialsResponse:
    """Get TURN credentials for WebRTC connections.

    Supports two modes:
    - **Time-limited (preferred):** TURN_SECRET set → generates HMAC credentials
      compatible with coturn's ``use-auth-secret`` mode.
    - **Static (managed TURN):** TURN_USERNAME + TURN_PASSWORD set → returns
      those credentials directly. Use for hosted TURN providers like Metered.ca.

    Returns:
        TurnCredentialsResponse with username, password, ttl, and TURN URIs
    """
    if not TURN_SECRET and not (TURN_USERNAME and TURN_PASSWORD):
        logger.warning("TURN credentials requested but TURN not configured")
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    try:
        if TURN_SECRET:
            # Time-limited HMAC credentials — works with coturn use-auth-secret
            credentials = generate_turn_credentials(str(user.id))
            logger.debug(f"Generated time-limited TURN credentials for user {user.id}")
        else:
            # Static credentials — for managed TURN providers (Metered.ca, OpenRelay, etc.)
            uris = []
            if TURN_TLS_PORT:
                uris.extend([
                    f"turns:{TURN_HOST}:{TURN_TLS_PORT}?transport=tcp",
                    f"turns:{TURN_HOST}:{TURN_TLS_PORT}",
                ])
            uris.extend([
                f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",
                f"turn:{TURN_HOST}:{TURN_PORT}",
            ])
            credentials = {
                "username": TURN_USERNAME,
                "password": TURN_PASSWORD,
                "ttl": TURN_CREDENTIAL_TTL,
                "uris": uris,
            }
            logger.debug(f"Returning static TURN credentials for user {user.id} (host={TURN_HOST})")

        return TurnCredentialsResponse(**credentials)
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )
