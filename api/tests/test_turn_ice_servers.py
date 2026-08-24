from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.turn_credentials import (
    build_turn_uris,
    resolve_turn_credentials,
    select_aiortc_turn_uri,
)
from api.routes.webrtc_signaling import (
    NonRelayFilterPolicy,
    SignalingManager,
    filter_outbound_sdp,
    get_ice_servers,
    sdp_has_ice_candidate,
    sdp_ice_candidate_kinds,
)


class TestBuildTurnUris:
    def test_openrelay_style_ports_include_plain_tcp_on_443(self):
        uris = build_turn_uris(host="openrelay.metered.ca", port=80, tls_port=443)
        assert uris[0] == "turn:openrelay.metered.ca:443?transport=tcp"
        assert "turns:openrelay.metered.ca:443?transport=tcp" in uris
        assert "turn:openrelay.metered.ca:80?transport=tcp" in uris
        assert "turn:openrelay.metered.ca:80" in uris

    def test_coturn_default_ports_do_not_hit_tls_port_without_tls(self):
        uris = build_turn_uris(host="turn.example.com", port=3478, tls_port=5349)
        assert "turn:turn.example.com:5349?transport=tcp" not in uris
        assert uris[0] == "turns:turn.example.com:5349?transport=tcp"
        assert "turn:turn.example.com:3478?transport=tcp" in uris


class TestSelectAiortcTurnUri:
    def test_prefers_plain_tcp_over_turns(self):
        uris = build_turn_uris(host="openrelay.metered.ca", port=80, tls_port=443)
        assert (
            select_aiortc_turn_uri(uris) == "turn:openrelay.metered.ca:443?transport=tcp"
        )

    def test_coturn_uses_plain_tcp_on_3478(self):
        uris = build_turn_uris(host="turn.example.com", port=3478, tls_port=5349)
        assert select_aiortc_turn_uri(uris) == "turn:turn.example.com:3478?transport=tcp"

    def test_empty_list(self):
        assert select_aiortc_turn_uri([]) is None


class TestResolveTurnCredentials:
    @patch("api.routes.turn_credentials.TURN_SECRET", None)
    @patch("api.routes.turn_credentials.TURN_USERNAME", "turnuser")
    @patch("api.routes.turn_credentials.TURN_PASSWORD", "turnpass")
    @patch("api.routes.turn_credentials.TURN_HOST", "openrelay.metered.ca")
    @patch("api.routes.turn_credentials.TURN_PORT", 80)
    @patch("api.routes.turn_credentials.TURN_TLS_PORT", 443)
    def test_static_credentials_include_tcp_on_443(self):
        credentials = resolve_turn_credentials("1")
        assert credentials["username"] == "turnuser"
        assert credentials["password"] == "turnpass"
        assert credentials["uris"][0] == "turn:openrelay.metered.ca:443?transport=tcp"

    @patch("api.routes.turn_credentials.TURN_SECRET", None)
    @patch("api.routes.turn_credentials.TURN_USERNAME", None)
    @patch("api.routes.turn_credentials.TURN_PASSWORD", None)
    def test_missing_configuration_raises(self):
        with pytest.raises(ValueError, match="TURN server not configured"):
            resolve_turn_credentials("1")


class TestSdpIceCandidates:
    def test_kinds_and_presence(self):
        sdp = (
            "v=0\r\n"
            "a=candidate:1 1 udp 1 10.26.10.0 43233 typ host\r\n"
            "a=candidate:2 1 udp 2 1.1.1.1 3478 typ relay\r\n"
        )
        assert sdp_ice_candidate_kinds(sdp) == ["host/udp", "relay/udp"]
        assert sdp_has_ice_candidate(sdp) is True
        assert sdp_has_ice_candidate("v=0\r\n") is False

    def test_private_host_only_answer_is_empty_after_private_filter(self):
        sdp = "v=0\r\na=candidate:1 1 udp 1 10.26.10.0 43233 typ host\r\n"
        with patch(
            "api.routes.webrtc_signaling.ICE_OUTBOUND_POLICY",
            NonRelayFilterPolicy.PRIVATE,
        ):
            filtered = filter_outbound_sdp(sdp)
        assert sdp_has_ice_candidate(filtered) is False


class TestGetIceServers:
    @patch("api.routes.turn_credentials.TURN_HOST", "openrelay.metered.ca")
    @patch("api.routes.turn_credentials.TURN_PORT", 80)
    @patch("api.routes.turn_credentials.TURN_TLS_PORT", 443)
    @patch("api.routes.turn_credentials.TURN_SECRET", None)
    @patch("api.routes.turn_credentials.TURN_USERNAME", "openrelayproject")
    @patch("api.routes.turn_credentials.TURN_PASSWORD", "openrelayproject")
    def test_aiortc_is_given_a_single_tcp_turn_uri(self):
        servers = get_ice_servers(user_id="1")
        assert len(servers) == 2
        urls = servers[1].urls
        if isinstance(urls, str):
            urls = [urls]
        assert urls == ["turn:openrelay.metered.ca:443?transport=tcp"]
        assert servers[1].username == "openrelayproject"
        assert servers[1].credential == "openrelayproject"

    @patch("api.routes.turn_credentials.TURN_HOST", "turn.example.com")
    @patch("api.routes.turn_credentials.TURN_PORT", 3478)
    @patch("api.routes.turn_credentials.TURN_TLS_PORT", 5349)
    @patch("api.routes.turn_credentials.TURN_SECRET", None)
    @patch("api.routes.turn_credentials.TURN_USERNAME", "turnuser")
    @patch("api.routes.turn_credentials.TURN_PASSWORD", "turnpass")
    def test_aiortc_coturn_uses_tcp_on_3478(self):
        servers = get_ice_servers(user_id="1")
        urls = servers[1].urls
        if isinstance(urls, str):
            urls = [urls]
        assert urls == ["turn:turn.example.com:3478?transport=tcp"]


class _FakeWebSocket:
    def __init__(self):
        self.send_json = AsyncMock()
        self.application_state = "connected"


class _FakePeerConnectionNoRelay:
    def __init__(self, *args, **kwargs):
        self._pc_id = None
        self.disconnect = AsyncMock()

    def event_handler(self, _name):
        def decorator(fn):
            return fn

        return decorator

    async def initialize(self, sdp, type):
        return None

    def get_answer(self):
        return {
            "sdp": "v=0\r\na=candidate:1 1 udp 1 10.26.10.0 43233 typ host\r\n",
            "type": "answer",
            "pc_id": "pc-1",
        }


@pytest.mark.asyncio
async def test_offer_aborts_when_answer_has_no_reachable_candidates():
    manager = SignalingManager()
    ws = _FakeWebSocket()
    user = SimpleNamespace(id=7, provider_id="provider")

    with (
        patch(
            "api.routes.webrtc_signaling.ICE_OUTBOUND_POLICY",
            NonRelayFilterPolicy.PRIVATE,
        ),
        patch(
            "api.routes.webrtc_signaling.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message="")
            ),
        ),
        patch("api.routes.webrtc_signaling.kickoff_pipeline_prewarm"),
        patch(
            "api.routes.webrtc_signaling.SmallWebRTCConnection",
            _FakePeerConnectionNoRelay,
        ),
        patch("api.routes.webrtc_signaling.run_pipeline_smallwebrtc") as mock_pipeline,
    ):
        await manager._handle_offer(
            ws,
            {
                "pc_id": "pc-1",
                "sdp": "v=0\r\n",
                "type": "offer",
            },
            workflow_id=2,
            workflow_run_id=46,
            user=user,
            organization_id=1,
            connection_key="conn-1",
            enforce_call_concurrency=False,
        )

    ws.send_json.assert_awaited()
    payload = ws.send_json.await_args.args[0]
    assert payload["type"] == "error"
    assert payload["payload"]["error_type"] == "webrtc_ice_failed"
    mock_pipeline.assert_not_called()
