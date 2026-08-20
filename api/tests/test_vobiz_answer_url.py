import pytest
from fastapi import HTTPException

from api.services.telephony.providers.vobiz.urls import (
    build_vobiz_answer_url,
    build_vobiz_hangup_url,
    vobiz_https_origin,
)


def test_vobiz_https_origin_strips_path_and_forces_https():
    assert (
        vobiz_https_origin("https://voice.example.com/extra")
        == "https://voice.example.com"
    )


def test_vobiz_https_origin_rejects_localhost():
    with pytest.raises(ValueError, match="public HTTPS"):
        vobiz_https_origin("http://localhost:8000")


def test_vobiz_https_origin_rejects_private_ip():
    with pytest.raises(ValueError, match="public HTTPS"):
        vobiz_https_origin("http://192.168.1.10:8000")


def test_build_vobiz_answer_url_has_no_query_string():
    url = build_vobiz_answer_url(
        "https://voice.example.com",
        workflow_id=11,
        organization_id=22,
        workflow_run_id=33,
    )
    assert url == "https://voice.example.com/api/v1/telephony/vobiz-xml/11/22/33"
    assert "?" not in url


def test_build_vobiz_hangup_url():
    assert (
        build_vobiz_hangup_url("https://voice.example.com", workflow_run_id=33)
        == "https://voice.example.com/api/v1/telephony/vobiz/hangup-callback/33"
    )


@pytest.mark.asyncio
async def test_initiate_call_sends_path_url_and_drops_dograh_kwargs(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from api.services.telephony.providers.vobiz.provider import VobizProvider

    provider = VobizProvider(
        {
            "auth_id": "MA123",
            "auth_token": "token",
            "from_numbers": ["15551230000"],
        }
    )

    monkeypatch.setattr(
        "api.services.telephony.providers.vobiz.provider.get_backend_endpoints",
        AsyncMock(return_value=("https://voice.example.com", "wss://voice.example.com")),
    )

    posted = {}

    class _Resp:
        status = 201

        async def text(self):
            return ""

        async def json(self):
            return {"call_uuid": "call-1", "message": "call fired"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, endpoint, json, headers):
            posted["json"] = json
            posted["endpoint"] = endpoint
            return _Resp()

    monkeypatch.setattr(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        _Session,
    )

    result = await provider.initiate_call(
        to_number="+919876543210",
        webhook_url=(
            "https://stale.trycloudflare.com/api/v1/telephony/vobiz-xml"
            "?workflow_id=11&workflow_run_id=33&organization_id=22"
        ),
        workflow_run_id=33,
        workflow_id=11,
        organization_id=22,
    )

    assert result.call_id == "call-1"
    body = posted["json"]
    assert body["answer_url"] == (
        "https://voice.example.com/api/v1/telephony/vobiz-xml/11/22/33"
    )
    assert "?" not in body["answer_url"]
    assert "workflow_id" not in body
    assert "organization_id" not in body
    assert body["hangup_url"].endswith("/vobiz/hangup-callback/33")


@pytest.mark.asyncio
async def test_initiate_call_rejects_localhost_origin(monkeypatch):
    from unittest.mock import AsyncMock

    from api.services.telephony.providers.vobiz.provider import VobizProvider

    provider = VobizProvider(
        {
            "auth_id": "MA123",
            "auth_token": "token",
            "from_numbers": ["15551230000"],
        }
    )
    monkeypatch.setattr(
        "api.services.telephony.providers.vobiz.provider.get_backend_endpoints",
        AsyncMock(return_value=("http://localhost:8000", "ws://localhost:8000")),
    )

    with pytest.raises(HTTPException) as exc:
        await provider.initiate_call(
            to_number="+919876543210",
            webhook_url="http://localhost:8000/api/v1/telephony/vobiz-xml?workflow_id=1&workflow_run_id=2&organization_id=3",
            workflow_run_id=2,
            workflow_id=1,
            organization_id=3,
        )
    assert exc.value.status_code == 400
    assert "public HTTPS" in exc.value.detail
