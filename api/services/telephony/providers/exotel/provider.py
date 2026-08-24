"""
Exotel implementation of the TelephonyProvider interface.

Exotel API docs:
  https://developer.exotel.com/api/
  https://support.exotel.com/support/solutions/articles/3000108630-working-with-the-stream-and-voicebot-applet

Key differences from Vobiz/Twilio:
- REST API uses HTTP Basic Auth (api_key:api_token)
- Outbound call endpoint: POST /v1/Accounts/{account_sid}/Calls/connect
- Form-encoded body (not JSON)
- Returns call SID as "Sid" inside a "Call" object
- ExoML (answer XML) uses <Stream> element similar to TwiML
- WebSocket start event fields: stream_sid, call_sid (snake_case; camelCase accepted)
"""

import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

from .urls import build_exotel_answer_url, build_exotel_hangup_url

if TYPE_CHECKING:
    from fastapi import WebSocket

_DEFAULT_SUBDOMAIN = "api.exotel.com"


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def extract_exotel_start_ids(start_msg: Dict[str, Any]) -> tuple[str, str]:
    """Return (stream_sid, call_sid) from an Exotel start event.

    Exotel Voicebot/Stream events use snake_case. Older docs and Twilio-shaped
    payloads use camelCase, so both are accepted.
    """
    start = start_msg.get("start") or {}
    if not isinstance(start, dict):
        start = {}
    stream_sid = _first_nonempty(
        start_msg.get("stream_sid"),
        start.get("stream_sid"),
        start_msg.get("streamSid"),
        start.get("streamSid"),
    )
    call_sid = _first_nonempty(
        start.get("call_sid"),
        start_msg.get("call_sid"),
        start.get("callSid"),
        start_msg.get("callSid"),
    )
    return stream_sid, call_sid


class ExotelProvider(TelephonyProvider):
    """
    Exotel implementation of TelephonyProvider.
    Uses Exotel REST API v1 + ExoML WebSocket media streams.
    """

    PROVIDER_NAME = WorkflowRunMode.EXOTEL.value
    WEBHOOK_ENDPOINT = "exotel-xml"

    # Shared persistent HTTP session
    _shared_session: aiohttp.ClientSession | None = None

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        """Return the shared aiohttp session, creating it on first use."""
        if cls._shared_session is None or cls._shared_session.closed:
            connector = aiohttp.TCPConnector(
                limit=10,
                keepalive_timeout=60,
                enable_cleanup_closed=True,
            )
            cls._shared_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30, connect=5),
                connector=connector,
            )
        return cls._shared_session

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize ExotelProvider.

        Args:
            config: Dictionary containing:
                - api_key: Exotel API Key
                - api_token: Exotel API Token
                - account_sid: Exotel Account SID
                - from_numbers: List of ExoPhone numbers
                - subdomain: (optional) API subdomain override
        """
        self.api_key = config.get("api_key")
        self.api_token = config.get("api_token")
        self.account_sid = config.get("account_sid")
        self.from_numbers = config.get("from_numbers", [])
        self.subdomain = config.get("subdomain") or _DEFAULT_SUBDOMAIN

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = f"https://{self.subdomain}/v1/Accounts/{self.account_sid}"

    # ------------------------------------------------------------------
    # TelephonyProvider interface
    # ------------------------------------------------------------------

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Exotel.

        Exotel API:
          POST /v1/Accounts/{account_sid}/Calls/connect
          Basic Auth: api_key:api_token
          Content-Type: application/x-www-form-urlencoded

        Required fields:
          From   - ExoPhone number
          To     - Destination number
          Url    - ExoML URL (answer URL)
          CallerId - ExoPhone (same as From for most cases)
        """
        if not self.validate_config():
            raise ValueError("Exotel provider not properly configured")

        endpoint = f"{self.base_url}/Calls/connect.json"

        if from_number is None:
            from_number = random.choice(self.from_numbers)
        logger.info(f"Selected Exotel ExoPhone {from_number} for outbound call")

        # Exotel India expects numbers in local 0-prefix format (09XXXXXXXXX)
        # Convert E.164 (+91XXXXXXXXXX) → 0XXXXXXXXXX
        def _to_exotel_format(number: str) -> str:
            n = number.strip()
            if n.startswith("+91") and len(n) == 13:
                return "0" + n[3:]   # +919513886363 → 09513886363
            if n.startswith("91") and len(n) == 12:
                return "0" + n[2:]   # 919513886363  → 09513886363
            return n                  # already local format or SIP

        from_exotel = _to_exotel_format(from_number)
        to_exotel = _to_exotel_format(to_number)
        logger.info(f"Exotel normalized: from={from_exotel}, to={to_exotel}")

        workflow_id = kwargs.pop("workflow_id", None)
        organization_id = kwargs.pop("organization_id", None)
        backend_endpoint, _ = await get_backend_endpoints()

        try:
            if workflow_id is None or organization_id is None:
                from urllib.parse import parse_qs
                parsed = urlparse(webhook_url)
                query = parse_qs(parsed.query)
                if workflow_id is None and query.get("workflow_id"):
                    workflow_id = int(query["workflow_id"][0])
                if organization_id is None and query.get("organization_id"):
                    organization_id = int(query["organization_id"][0])
            if not workflow_id or not organization_id or not workflow_run_id:
                raise ValueError(
                    "Exotel answer_url is missing workflow_id, organization_id, "
                    "or workflow_run_id"
                )
            answer_url = build_exotel_answer_url(
                backend_endpoint,
                workflow_id=int(workflow_id),
                organization_id=int(organization_id),
                workflow_run_id=int(workflow_run_id),
            )
            hangup_url = build_exotel_hangup_url(
                backend_endpoint, workflow_run_id=int(workflow_run_id)
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        logger.info(f"Exotel answer_url={answer_url}")

        # Exotel expects form-encoded data with local-format numbers
        data = {
            "From": from_exotel,
            "To": to_exotel,
            "CallerId": from_exotel,
            "Url": answer_url,
            "StatusCallback": hangup_url,
            # Note: StatusCallbackEvents is Twilio-specific — Exotel does NOT support it.
            # Exotel fires StatusCallback at call end automatically.
        }

        auth = aiohttp.BasicAuth(self.api_key, self.api_token)
        session = await self.get_session()

        async with session.post(endpoint, data=data, auth=auth) as response:
            if response.status not in (200, 201):
                error_data = await response.text()
                logger.error(f"Exotel API error: {error_data}")
                raise HTTPException(
                    status_code=response.status,
                    detail=f"Failed to initiate Exotel call: {error_data}",
                )

            response_data = await response.json()
            logger.info(f"Exotel API response: {response_data}")

            # Response shape: {"Call": {"Sid": "...", "Status": "queued", ...}}
            call_obj = response_data.get("Call", {})
            call_id = call_obj.get("Sid")

            if not call_id:
                logger.error(
                    f"No call Sid in Exotel response. Keys: {list(response_data.keys())}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Exotel API response missing call Sid. Response: {response_data}",
                )

            logger.info(f"Exotel call initiated. Sid: {call_id}")
            return CallInitiationResult(
                call_id=call_id,
                status=call_obj.get("Status", "queued"),
                caller_number=from_number,
                provider_metadata={"call_sid": call_id},
                raw_response=response_data,
            )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Fetch call details from Exotel."""
        endpoint = f"{self.base_url}/Calls/{call_id}.json"
        auth = aiohttp.BasicAuth(self.api_key, self.api_token)

        session = await self.get_session()
        async with session.get(endpoint, auth=auth) as response:
            if response.status != 200:
                error_data = await response.text()
                raise Exception(f"Failed to get Exotel call status: {error_data}")
            return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(
            self.api_key and self.api_token and self.account_sid and self.from_numbers
        )

    # ------------------------------------------------------------------
    # Webhook / XML response
    # ------------------------------------------------------------------

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate ExoML response directing Exotel to stream audio over WebSocket.

        Exotel <Stream> element:
          bidirectional="true"   — two-way audio
          audiotrack="both"      — stream both legs
          Content-Type via attribute is not supported; Exotel uses MULAW 8kHz by default
        """
        _, wss_backend_endpoint = await get_backend_endpoints()
        ws_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws"
            f"/{workflow_id}/{organization_id}/{workflow_run_id}"
        )
        exoml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" bidirectional="true" />
    </Connect>
</Response>"""
        return exoml

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        """Generate ExoML response for an inbound call."""
        from fastapi import Response

        exoml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}" bidirectional="true" />
    </Connect>
</Response>"""
        return Response(content=exoml, media_type="application/xml")

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        initial_msg: Optional[dict] = None,
        **kwargs,
    ) -> None:
        """
        Handle Exotel WebSocket media stream.

        Exotel sends a JSON "start" event first:
          {"event": "start", "stream_sid": "...", "start": {"call_sid": "...", ...}}
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        if initial_msg is not None:
            start_msg = initial_msg
        else:
            first_msg = await websocket.receive_text()
            start_msg = json.loads(first_msg)
        logger.debug(f"[run {workflow_run_id}] Exotel first WS message: {start_msg}")

        # Exotel sends a 'connected' event first on initial WebSocket handshake,
        # followed by the 'start' event containing stream details.
        while start_msg.get("event") == "connected":
            next_msg = await websocket.receive_text()
            start_msg = json.loads(next_msg)
            logger.debug(f"[run {workflow_run_id}] Exotel next WS message: {start_msg}")

        if start_msg.get("event") != "start":
            logger.error(
                f"[run {workflow_run_id}] Expected 'start' event, got: {start_msg.get('event')}"
            )
            await websocket.close(code=4400, reason="Expected start event")
            return

        stream_sid, call_sid = extract_exotel_start_ids(start_msg)

        if not stream_sid:
            logger.error(
                f"[run {workflow_run_id}] Missing stream_sid in Exotel start event"
            )
            await websocket.close(code=4400, reason="Missing stream_sid")
            return

        logger.info(
            f"[run {workflow_run_id}] Exotel WebSocket connected — "
            f"stream_sid={stream_sid}, call_sid={call_sid}"
        )

        try:
            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=call_sid,
                transport_kwargs={"stream_sid": stream_sid, "call_sid": call_sid},
            )
            logger.info(f"[run {workflow_run_id}] Exotel pipeline completed")
        except Exception as e:
            logger.error(
                f"[run {workflow_run_id}] Error in Exotel WebSocket handler: {e}"
            )
            raise

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        return "exotel" in headers.get("user-agent", "").lower()

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        country = "IN"
        from_raw = webhook_data.get("From", "")
        to_raw = webhook_data.get("To", "")
        return NormalizedInboundData(
            provider=ExotelProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallSid", ""),
            from_number=normalize_telephony_address(
                from_raw, country_hint=country
            ).canonical
            if from_raw
            else "",
            to_number=normalize_telephony_address(
                to_raw, country_hint=country
            ).canonical
            if to_raw
            else "",
            direction=webhook_data.get("Direction", "inbound"),
            call_status=webhook_data.get("Status", ""),
            account_id=webhook_data.get("AccountSid"),
            from_country=country,
            to_country=country,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if not webhook_account_id:
            return False
        stored = config_data.get("account_sid")
        return stored == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """
        Exotel does not send signed webhooks on all plans.
        Accept all callbacks from Exotel (validated by account_sid match upstream).
        Override this if your Exotel plan supports HMAC signing.
        """
        return True

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Exotel does not use per-request HMAC signatures on standard plans.
        Always return True — security is enforced by account_sid matching upstream.
        """
        return True

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        """
        Exotel inbound configuration is done through the Exotel dashboard:
        set the VoiceApp's ExoML URL to the Dograh answer URL.
        This method is a no-op — return success so UI doesn't block.
        """
        logger.info(
            f"Exotel configure_inbound for {address}: manual dashboard config required. "
            f"Set VoiceApp URL to: {webhook_url}"
        )
        return ProviderSyncResult(ok=True)

    # ------------------------------------------------------------------
    # Status callback
    # ------------------------------------------------------------------

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Exotel status callback into generic format."""
        call_status = data.get("Status", data.get("CallStatus", ""))
        return {
            "call_id": data.get("CallSid", ""),
            "status": TelephonyCallStatus.from_raw(call_status) or call_status,
            "from_number": data.get("From"),
            "to_number": data.get("To"),
            "direction": data.get("Direction", "outbound"),
            "duration": data.get("Duration"),
            "extra": data,
        }

    # ------------------------------------------------------------------
    # Error responses
    # ------------------------------------------------------------------

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi import Response

        exoml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, there was an error processing your call. {message}</Say>
    <Hangup/>
</Response>"""
        return Response(content=exoml, media_type="application/xml")

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )
        exoml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Hangup/>
</Response>"""
        return Response(content=exoml, media_type="application/xml")

    # ------------------------------------------------------------------
    # Call transfer (not supported)
    # ------------------------------------------------------------------

    async def transfer_call(self, destination, transfer_id, conference_name, timeout=30, **kwargs):
        raise NotImplementedError("Exotel provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Cost (CDR)
    # ------------------------------------------------------------------

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """Fetch call cost from Exotel CDR endpoint."""
        endpoint = f"{self.base_url}/Calls/{call_id}.json"
        auth = aiohttp.BasicAuth(self.api_key, self.api_token)

        try:
            session = await self.get_session()
            async with session.get(endpoint, auth=auth) as response:
                if response.status != 200:
                    error_data = await response.text()
                    return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": error_data}

                call_data = await response.json()
                call_obj = call_data.get("Call", {})
                price_str = call_obj.get("Price", "0") or "0"
                try:
                    cost = abs(float(price_str))
                except ValueError:
                    cost = 0.0

                return {
                    "cost_usd": cost,
                    "duration": int(call_obj.get("Duration", 0) or 0),
                    "status": call_obj.get("Status", "unknown"),
                    "price_unit": call_obj.get("PriceUnit", "USD"),
                    "raw_response": call_data,
                }
        except Exception as e:
            logger.error(f"Exception fetching Exotel call cost: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}
