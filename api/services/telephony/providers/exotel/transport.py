"""Exotel transport factory.

Exotel Voicebot streams 16-bit linear PCM (8 kHz mono) as base64 JSON:
- Incoming start/media events use snake_case (stream_sid, call_sid)
- Outgoing media/clear frames must also use stream_sid
"""

from fastapi import WebSocket
from loguru import logger
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import ExotelFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    stream_sid: str,
    call_sid: str,
):
    """Create a FastAPI WebSocket transport for an Exotel media-streams connection."""
    logger.info(
        f"[run {workflow_run_id}] Creating Exotel transport - "
        f"stream_sid={stream_sid}, call_sid={call_sid}"
    )

    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="exotel"
    )

    api_key = config.get("api_key")
    api_token = config.get("api_token")

    if not api_key or not api_token:
        raise ValueError(
            f"Incomplete Exotel configuration for organization {organization_id}"
        )

    serializer = ExotelFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=ExotelFrameSerializer.InputParams(
            exotel_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    # Exotel rejects / garbles frames under 3.2 KB (200 ms @ 8 kHz 16-bit).
    # 20 × 10 ms = 200 ms = 3200 bytes. Merge into overrides so we do not
    # pass audio_out_10ms_chunks twice into FastAPIWebsocketParams.
    transport_kwargs = realtime_param_overrides(is_realtime)
    transport_kwargs["audio_out_10ms_chunks"] = 20

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
            **transport_kwargs,
        ),
    )

    logger.info(f"[run {workflow_run_id}] Exotel transport created successfully")
    return transport
