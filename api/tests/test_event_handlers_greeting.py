import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.services.pipecat.event_handlers import (
    _start_opening_needs_fetch_context,
    wait_until_webrtc_can_send_audio,
)


def _engine(*, greeting=None, prompt=None, context=None, start_id="start"):
    node = SimpleNamespace(greeting=greeting, prompt=prompt)
    workflow = SimpleNamespace(start_node_id=start_id, nodes={start_id: node})
    return SimpleNamespace(workflow=workflow, _call_context_vars=context or {})


def test_static_greeting_does_not_need_fetch_context():
    engine = _engine(greeting="Thanks for calling, how can I help?")
    assert _start_opening_needs_fetch_context(engine) is False


def test_templated_greeting_needs_missing_var():
    engine = _engine(greeting="Hi {{customer_name}}, this is Sam.")
    assert _start_opening_needs_fetch_context(engine) is True


def test_templated_greeting_already_in_context_does_not_wait():
    engine = _engine(
        greeting="Hi {{customer_name}}, this is Sam.",
        context={"customer_name": "Jane"},
    )
    assert _start_opening_needs_fetch_context(engine) is False


def test_prompt_checked_when_no_greeting():
    engine = _engine(prompt="You are calling {{account_id}} about a bill.")
    assert _start_opening_needs_fetch_context(engine) is True


@pytest.mark.asyncio
async def test_wait_until_webrtc_is_noop_for_non_webrtc_transport():
    transport = SimpleNamespace(_client=object())
    await asyncio.wait_for(wait_until_webrtc_can_send_audio(transport, timeout_s=0.2), 0.5)


@pytest.mark.asyncio
async def test_wait_until_webrtc_is_noop_for_exotel_fastapi_client():
    """Exotel client has _can_send but no audio output track — must not wait."""

    class FakeExotelClient:
        def _can_send(self):
            return False

    transport = SimpleNamespace(_client=FakeExotelClient())
    await asyncio.wait_for(wait_until_webrtc_can_send_audio(transport, timeout_s=2.0), 0.3)


@pytest.mark.asyncio
async def test_wait_until_webrtc_returns_when_audio_track_ready():
    client = MagicMock()
    client._can_send.return_value = True
    client._audio_output_track = object()
    transport = SimpleNamespace(_client=client)
    await asyncio.wait_for(wait_until_webrtc_can_send_audio(transport, timeout_s=0.5), 0.6)
    client._can_send.assert_called()
