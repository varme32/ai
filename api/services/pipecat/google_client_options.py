"""HTTP options and error sanitization for Google Gemini clients."""

from __future__ import annotations

GOOGLE_RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]

_GOOGLE_GATEWAY_HINTS = (
    "502 bad gateway",
    "that's an error",
    "server encountered a temporary error",
    "error 502 (server error)",
)


def google_retry_http_options(existing: dict | object | None = None) -> dict:
    """Merge Gemini HTTP retries onto pipecat's default client headers."""
    options: dict = {}
    if isinstance(existing, dict):
        options = existing.copy()
    elif existing is not None and hasattr(existing, "headers"):
        options["headers"] = dict(getattr(existing, "headers") or {})
    options["retry_options"] = {
        "attempts": 4,
        "initial_delay": 0.4,
        "max_delay": 4.0,
        "http_status_codes": GOOGLE_RETRY_STATUS_CODES,
    }
    return options


def is_google_html_gateway_error(error: object) -> bool:
    text = str(error).lower()
    return any(hint in text for hint in _GOOGLE_GATEWAY_HINTS)


GOOGLE_GATEWAY_USER_MESSAGE = (
    "Google Gemini API returned 502 Bad Gateway. That is Google's frontend, "
    "not Dograh. The call will retry automatically; if it keeps failing on "
    "Windows, disable IPv6 for this machine or switch the LLM/TTS provider."
)
