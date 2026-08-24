"""Exotel frame serializer.

Pipecat's Exotel serializer emits Twilio-style ``streamSid`` keys. Exotel's
Voicebot protocol uses snake_case ``stream_sid`` on outgoing media/clear/mark
frames, so rewrite those keys before they hit the wire.
"""

import json

from pipecat.frames.frames import Frame
from pipecat.serializers.exotel import (
    ExotelFrameSerializer as _PipecatExotelFrameSerializer,
)


class ExotelFrameSerializer(_PipecatExotelFrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        payload = await super().serialize(frame)
        if not isinstance(payload, str):
            return payload
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            return payload
        if "streamSid" not in message:
            return payload
        message["stream_sid"] = message.pop("streamSid")
        return json.dumps(message)


__all__ = ["ExotelFrameSerializer"]
