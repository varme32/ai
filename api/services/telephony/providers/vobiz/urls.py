"""Vobiz callback URL helpers.

Vobiz rejects ``answer_url`` values that are not public HTTPS, and it is
stricter than Twilio/Plivo about query strings. Always send a path-based
https URL with no query parameters.
"""

from __future__ import annotations

from urllib.parse import urlparse

from api.utils.common import is_local_or_private_url

VOBIZ_ANSWER_PATH = "/api/v1/telephony/vobiz-xml"
VOBIZ_HANGUP_PATH = "/api/v1/telephony/vobiz/hangup-callback"


def vobiz_https_origin(origin: str) -> str:
    """Return a public ``https://host`` origin or raise ValueError."""
    raw = (origin or "").strip().rstrip("/")
    if not raw:
        raise ValueError(
            "Vobiz requires a public HTTPS webhook URL. Set PUBLIC_BASE_URL "
            "to a stable https:// domain (named Cloudflare tunnel or public host)."
        )
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"Vobiz answer_url host is missing in '{origin}'")
    if parsed.scheme == "http" or is_local_or_private_url(raw):
        raise ValueError(
            "Vobiz requires a public HTTPS webhook URL. "
            f"'{origin}' is not reachable from the internet. Set PUBLIC_BASE_URL "
            "to a stable https:// domain. Do not use localhost, and do not pin a "
            "trycloudflare.com hostname that changes every time cloudflared restarts."
        )
    port = parsed.port
    if port and port not in (80, 443):
        return f"https://{host}:{port}"
    return f"https://{host}"


def build_vobiz_answer_url(
    origin: str,
    *,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
) -> str:
    """Path-based answer URL with no query string."""
    return (
        f"{vobiz_https_origin(origin)}{VOBIZ_ANSWER_PATH}"
        f"/{int(workflow_id)}/{int(organization_id)}/{int(workflow_run_id)}"
    )


def build_vobiz_hangup_url(origin: str, *, workflow_run_id: int) -> str:
    return (
        f"{vobiz_https_origin(origin)}{VOBIZ_HANGUP_PATH}/{int(workflow_run_id)}"
    )
