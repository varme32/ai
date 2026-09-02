import asyncio

import pytest

from api.services.pipecat.pipeline_prewarm import (
    PipelineResources,
    discard_pipeline_prewarm,
    kickoff_pipeline_prewarm,
    peek_ready_prewarm,
    reset_pipeline_prewarm,
    take_pipeline_prewarm,
)


@pytest.fixture(autouse=True)
def _clear_prewarm():
    reset_pipeline_prewarm()
    yield
    reset_pipeline_prewarm()


def _dummy_resources(run_id: int) -> PipelineResources:
    return PipelineResources(
        workflow=object(),
        workflow_run=object(),
        user_config=object(),
        workflow_graph=object(),
        stt=None,
        tts=None,
        llm=None,
        inference_llm=None,
        is_realtime=False,
        merged_call_context_vars={"run": run_id},
        pre_call_fetch_task=None,
        vad_analyzer=None,
        has_recordings=False,
        keyterms=None,
        max_call_duration_seconds=300,
        max_user_idle_timeout=10.0,
        include_transcript_end_timestamps=False,
        audio_config=None,
    )


@pytest.mark.asyncio
async def test_kickoff_is_idempotent(monkeypatch):
    calls = {"count": 0}

    async def fake_prepare(**_kwargs):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return _dummy_resources(_kwargs["workflow_run_id"])

    monkeypatch.setattr(
        "api.services.pipecat.pipeline_prewarm.prepare_pipeline_resources",
        fake_prepare,
    )

    kickoff_pipeline_prewarm(
        workflow_id=1,
        workflow_run_id=99,
        organization_id=2,
        user_id=3,
        provider_name="vobiz",
    )
    kickoff_pipeline_prewarm(
        workflow_id=1,
        workflow_run_id=99,
        organization_id=2,
        user_id=3,
        provider_name="vobiz",
    )

    resources = await take_pipeline_prewarm(99)
    assert resources is not None
    assert resources.merged_call_context_vars["run"] == 99
    assert calls["count"] == 1
    assert await take_pipeline_prewarm(99) is None


@pytest.mark.asyncio
async def test_take_returns_none_when_nothing_prewarmed():
    assert await take_pipeline_prewarm(12345) is None


@pytest.mark.asyncio
async def test_discard_cancels_in_flight_prewarm(monkeypatch):
    started = asyncio.Event()

    async def fake_prepare(**_kwargs):
        started.set()
        await asyncio.sleep(10)
        return _dummy_resources(_kwargs["workflow_run_id"])

    monkeypatch.setattr(
        "api.services.pipecat.pipeline_prewarm.prepare_pipeline_resources",
        fake_prepare,
    )

    kickoff_pipeline_prewarm(
        workflow_id=1,
        workflow_run_id=7,
        organization_id=2,
        user_id=3,
        provider_name="vobiz",
    )
    await started.wait()
    discard_pipeline_prewarm(7)
    assert await take_pipeline_prewarm(7) is None


@pytest.mark.asyncio
async def test_take_awaits_in_flight_prewarm_instead_of_cancelling(monkeypatch):
    """Answer-time take must reuse ring-time work, not cancel and rebuild."""

    async def fake_prepare(**_kwargs):
        await asyncio.sleep(0.05)
        return _dummy_resources(_kwargs["workflow_run_id"])

    monkeypatch.setattr(
        "api.services.pipecat.pipeline_prewarm.prepare_pipeline_resources",
        fake_prepare,
    )

    kickoff_pipeline_prewarm(
        workflow_id=1,
        workflow_run_id=21,
        organization_id=2,
        user_id=3,
        provider_name="vobiz",
    )
    resources = await take_pipeline_prewarm(21)
    assert resources is not None
    assert resources.merged_call_context_vars["run"] == 21


@pytest.mark.asyncio
async def test_peek_ready_does_not_consume_prewarm(monkeypatch):
    async def fake_prepare(**_kwargs):
        return _dummy_resources(_kwargs["workflow_run_id"])

    monkeypatch.setattr(
        "api.services.pipecat.pipeline_prewarm.prepare_pipeline_resources",
        fake_prepare,
    )

    kickoff_pipeline_prewarm(
        workflow_id=1,
        workflow_run_id=44,
        organization_id=2,
        user_id=3,
        provider_name="vobiz",
    )
    for _ in range(50):
        if peek_ready_prewarm(44) is not None:
            break
        await asyncio.sleep(0.01)

    peeked = peek_ready_prewarm(44)
    assert peeked is not None
    assert peeked.merged_call_context_vars["run"] == 44
    taken = await take_pipeline_prewarm(44)
    assert taken is peeked
    assert peek_ready_prewarm(44) is None
