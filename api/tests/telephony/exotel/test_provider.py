"""Exotel WebSocket start-event parsing.

Exotel Voicebot sends snake_case identifiers (``stream_sid``, ``call_sid``).
The handler previously looked only for Twilio-style ``streamSid`` / ``callSid``,
which closed the socket on a real start event.
"""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import InterruptionFrame

from api.routes.telephony import _call_sid_from_start_message
from api.services.telephony.providers.exotel.provider import (
    ExotelProvider,
    extract_exotel_start_ids,
)
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.providers.exotel.serializers import ExotelFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

# Payload captured from a live Exotel Voicebot start event.
_LIVE_EXOTEL_START = {
    "event": "start",
    "stream_sid": "b7368f68b13ee23fa935eb08e42b1a8o",
    "sequence_number": "1",
    "start": {
        "stream_sid": "b7368f68b13ee23fa935eb08e42b1a8o",
        "call_sid": "3aa74daf25c74c4305d096ddf5a21a8o",
        "account_sid": "nabo61",
        "from": "04049170972",
        "to": "04049170972",
        "custom_parameters": {},
        "media_format": {
            "encoding": "base64",
            "sample_rate": "8000",
            "bit_rate": "128kbps",
        },
    },
}


class _FakeWebSocket:
    def __init__(self, *messages: str):
        self.receive_text = AsyncMock(side_effect=messages)
        self.close = AsyncMock()


def _provider() -> ExotelProvider:
    return ExotelProvider(
        {
            "api_key": "key",
            "api_token": "token",
            "account_sid": "nabo61",
            "from_numbers": ["04049170972"],
        }
    )


def test_extract_exotel_start_ids_from_live_snake_case_payload():
    stream_sid, call_sid = extract_exotel_start_ids(_LIVE_EXOTEL_START)
    assert stream_sid == "b7368f68b13ee23fa935eb08e42b1a8o"
    assert call_sid == "3aa74daf25c74c4305d096ddf5a21a8o"


def test_extract_exotel_start_ids_accepts_camel_case():
    stream_sid, call_sid = extract_exotel_start_ids(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {"callSid": "CA123"},
        }
    )
    assert stream_sid == "MZ123"
    assert call_sid == "CA123"


def test_extract_exotel_start_ids_prefers_snake_case():
    stream_sid, call_sid = extract_exotel_start_ids(
        {
            "event": "start",
            "stream_sid": "snake-stream",
            "streamSid": "camel-stream",
            "start": {"call_sid": "snake-call", "callSid": "camel-call"},
        }
    )
    assert stream_sid == "snake-stream"
    assert call_sid == "snake-call"


def test_call_sid_from_start_message_reads_exotel_snake_case():
    assert (
        _call_sid_from_start_message(_LIVE_EXOTEL_START)
        == "3aa74daf25c74c4305d096ddf5a21a8o"
    )


@pytest.mark.asyncio
async def test_handle_websocket_starts_pipeline_from_live_start_event():
    provider = _provider()
    websocket = _FakeWebSocket()

    with patch(
        "api.services.pipecat.run_pipeline.run_pipeline_telephony",
        new_callable=AsyncMock,
    ) as run_pipeline:
        await provider.handle_websocket(
            websocket,
            workflow_id=2,
            organization_id=1,
            workflow_run_id=65,
            initial_msg=_LIVE_EXOTEL_START,
        )

    run_pipeline.assert_awaited_once()
    _, kwargs = run_pipeline.await_args
    assert kwargs["call_id"] == "3aa74daf25c74c4305d096ddf5a21a8o"
    assert kwargs["transport_kwargs"] == {
        "stream_sid": "b7368f68b13ee23fa935eb08e42b1a8o",
        "call_sid": "3aa74daf25c74c4305d096ddf5a21a8o",
    }
    websocket.close.assert_not_awaited()
    websocket.receive_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_websocket_skips_connected_then_reads_snake_case_start():
    provider = _provider()
    websocket = _FakeWebSocket(
        json.dumps({"event": "connected"}),
        json.dumps(_LIVE_EXOTEL_START),
    )

    with patch(
        "api.services.pipecat.run_pipeline.run_pipeline_telephony",
        new_callable=AsyncMock,
    ) as run_pipeline:
        await provider.handle_websocket(
            websocket,
            workflow_id=2,
            organization_id=1,
            workflow_run_id=65,
        )

    run_pipeline.assert_awaited_once()
    _, kwargs = run_pipeline.await_args
    assert kwargs["transport_kwargs"]["stream_sid"] == "b7368f68b13ee23fa935eb08e42b1a8o"
    websocket.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_websocket_closes_when_stream_sid_missing():
    provider = _provider()
    websocket = _FakeWebSocket()

    with patch(
        "api.services.pipecat.run_pipeline.run_pipeline_telephony",
        new_callable=AsyncMock,
    ) as run_pipeline:
        await provider.handle_websocket(
            websocket,
            workflow_id=2,
            organization_id=1,
            workflow_run_id=65,
            initial_msg={"event": "start", "start": {"call_sid": "CA123"}},
        )

    run_pipeline.assert_not_awaited()
    websocket.close.assert_awaited_once_with(code=4400, reason="Missing stream_sid")


def test_exotel_transport_params_override_chunk_size_once():
    kwargs = realtime_param_overrides(False)
    kwargs["audio_out_10ms_chunks"] = 20
    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        **kwargs,
    )
    assert params.audio_out_10ms_chunks == 20


@pytest.mark.asyncio
async def test_exotel_serializer_emits_snake_case_stream_sid():
    serializer = ExotelFrameSerializer(stream_sid="stream-1", call_sid="call-1")
    payload = await serializer.serialize(InterruptionFrame())
    message = json.loads(payload)
    assert message == {"event": "clear", "stream_sid": "stream-1"}
    assert "streamSid" not in message


@pytest.mark.asyncio
async def test_exotel_serializer_holds_media_until_min_chunk():
    from pipecat.frames.frames import OutputAudioRawFrame, StartFrame

    from api.services.telephony.providers.exotel.serializers import (
        EXOTEL_MIN_MEDIA_BYTES,
        EXOTEL_PCM_FRAME_BYTES,
    )

    serializer = ExotelFrameSerializer(stream_sid="stream-1", call_sid="call-1")
    await serializer.setup(StartFrame(audio_in_sample_rate=8000, audio_out_sample_rate=8000))

    twenty_ms = b"\x00" * EXOTEL_PCM_FRAME_BYTES
    held = 0
    emitted = None
    for _ in range(EXOTEL_MIN_MEDIA_BYTES // EXOTEL_PCM_FRAME_BYTES):
        emitted = await serializer.serialize(
            OutputAudioRawFrame(audio=twenty_ms, sample_rate=8000, num_channels=1)
        )
        if emitted is None:
            held += 1
            continue
        break

    assert held >= 9
    assert emitted is not None
    message = json.loads(emitted)
    raw = base64.b64decode(message["media"]["payload"])
    assert len(raw) == EXOTEL_MIN_MEDIA_BYTES
    assert len(raw) % EXOTEL_PCM_FRAME_BYTES == 0
    assert message["stream_sid"] == "stream-1"
