import socket

from api.network_bootstrap import apply_network_bootstrap
from api.services.pipecat.google_client_options import (
    google_retry_http_options,
    is_google_html_gateway_error,
)


def test_detects_google_html_502():
    err = (
        "502 Bad Gateway. {'message': '<!DOCTYPE html> Error 502 (Server Error)"
        " The server encountered a temporary error', 'status': 'Bad Gateway'}"
    )
    assert is_google_html_gateway_error(err) is True


def test_ignores_unrelated_errors():
    assert is_google_html_gateway_error("quota exceeded") is False


def test_retry_options_include_502():
    options = google_retry_http_options({"headers": {"x-goog-api-client": "test"}})
    assert options["headers"]["x-goog-api-client"] == "test"
    assert 502 in options["retry_options"]["http_status_codes"]
    assert options["retry_options"]["attempts"] >= 3


def test_network_bootstrap_is_idempotent():
    apply_network_bootstrap()
    first = socket.getaddrinfo
    apply_network_bootstrap()
    assert socket.getaddrinfo is first
    assert getattr(socket.getaddrinfo, "_dograh_ipv4", False) is True
