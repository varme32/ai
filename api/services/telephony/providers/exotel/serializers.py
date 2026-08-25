"""Exotel frame serializer.

Pipecat's Exotel serializer emits Twilio-style ``streamSid`` keys. Exotel's
Voicebot protocol uses snake_case ``stream_sid`` on outgoing media/clear/mark
frames, so rewrite those keys before they hit the wire.

Exotel also requires media payloads to be a multiple of 320 bytes (20 ms of
8 kHz 16-bit PCM) and at least 3.2 KB (200 ms). Smaller frames decode as
static and stutter on the call.
"""

import base64
import json

from pipecat.frames.frames import Frame
from pipecat.serializers.exotel import (
    ExotelFrameSerializer as _PipecatExotelFrameSerializer,
)

# 20 ms of 8 kHz 16-bit mono PCM.
EXOTEL_PCM_FRAME_BYTES = 320
# Exotel minimum media chunk: 3.2 KB = 200 ms at 8 kHz 16-bit mono.
EXOTEL_MIN_MEDIA_BYTES = 3200


def _rewrite_stream_sid(message: dict) -> dict:
    if "streamSid" in message:
        message["stream_sid"] = message.pop("streamSid")
    return message


class ExotelFrameSerializer(_PipecatExotelFrameSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._out_buf = bytearray()

    async def serialize(self, frame: Frame) -> str | bytes | None:
        payload = await super().serialize(frame)
        if not isinstance(payload, str):
            return payload
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            return payload

        event = message.get("event")
        if event == "clear":
            self._out_buf.clear()
            return json.dumps(_rewrite_stream_sid(message))

        if event != "media":
            return json.dumps(_rewrite_stream_sid(message))

        media = message.get("media") or {}
        raw = base64.b64decode(media.get("payload") or "")
        self._out_buf.extend(raw)
        if len(self._out_buf) < EXOTEL_MIN_MEDIA_BYTES:
            return None

        n = min(
            (len(self._out_buf) // EXOTEL_PCM_FRAME_BYTES) * EXOTEL_PCM_FRAME_BYTES,
            EXOTEL_MIN_MEDIA_BYTES,
        )
        chunk = bytes(self._out_buf[:n])
        del self._out_buf[:n]
        message["media"] = {**media, "payload": base64.b64encode(chunk).decode("ascii")}
        return json.dumps(_rewrite_stream_sid(message))


__all__ = [
    "ExotelFrameSerializer",
    "EXOTEL_MIN_MEDIA_BYTES",
    "EXOTEL_PCM_FRAME_BYTES",
]
