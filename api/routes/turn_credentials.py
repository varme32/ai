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


def build_turn_uris(
    host: Optional[str] = None,
    port: Optional[int] = None,
    tls_port: Optional[int] = None,
) -> List[str]:
    """Build TURN URIs for browsers and aiortc.

    Managed providers that listen on 443 (Metered OpenRelay) often speak
    TURN-over-TCP without TLS on that port. Include that URI when the TLS
    port is 443 so aioice — which uses only the first TURN URI — can
    allocate a relay on cloud hosts that block UDP. Standard coturn
    (TLS on 5349) is left as ``turns:`` so we do not hit a TLS port with
    a plain TCP ALLOCATE.
    """
    host = host or TURN_HOST
    is_metered = bool(host and "metered" in host.lower())

    if is_metered:
        port = port if port is not None and port != 3478 else 80
        tls_port = tls_port if tls_port is not None and tls_port != 5349 else 443
    else:
        port = TURN_PORT if port is None else port
        tls_port = TURN_TLS_PORT if tls_port is None else tls_port

    uris: List[str] = []
    uris.extend(
        [
            f"turn:{host}:{port}?transport=tcp",
            f"turn:{host}:{port}",
        ]
    )
    if tls_port:
        uris.extend(
            [
                f"turns:{host}:{tls_port}?transport=tcp",
                f"turns:{host}:{tls_port}",
            ]
        )
        if tls_port == 443:
            uris.append(f"turn:{host}:{tls_port}?transport=tcp")
    return uris


def select_aiortc_turn_uri(uris: List[str]) -> Optional[str]:
    """Pick the single TURN URI aiortc/aioice will actually use.

    aioice accepts only one TURN server and has no fallback. A ``turns:``
    URI against a host that speaks plain TURN-over-TCP fails the TLS
    handshake in a few hundred milliseconds, so gathering completes with
    only a private host candidate — fatal on Render and similar platforms.
    Prefer plain ``turn:...?transport=tcp``.
    """
    for uri in uris:
        if uri.startswith("turn:") and "transport=tcp" in uri:
            return uri
    for uri in uris:
        if uri.startswith("turns:"):
            return uri
    for uri in uris:
        if uri.startswith("turn:"):
            return uri
    return None


def resolve_turn_credentials(user_id: str, ttl: int = TURN_CREDENTIAL_TTL) -> dict:
    """Return TURN credentials for the configured auth mode.

    Raises:
        ValueError: If neither HMAC nor static TURN credentials are configured.
    """
    if TURN_SECRET:
        return generate_turn_credentials(user_id, ttl=ttl)
    if TURN_USERNAME and TURN_PASSWORD:
        return {
            "username": TURN_USERNAME,
            "password": TURN_PASSWORD,
            "ttl": ttl,
            "uris": build_turn_uris(),
        }
    raise ValueError("TURN server not configured")


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

    return {
        "username": username,
        "password": password,
        "ttl": ttl,
        "uris": build_turn_uris(),
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
    try:
        credentials = resolve_turn_credentials(str(user.id))
        if TURN_SECRET:
            logger.debug(f"Generated time-limited TURN credentials for user {user.id}")
        else:
            logger.debug(
                f"Returning static TURN credentials for user {user.id} (host={TURN_HOST})"
            )
        return TurnCredentialsResponse(**credentials)
    except ValueError:
        logger.warning("TURN credentials requested but TURN not configured")
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )
