"""Best-effort in-process pipeline prewarm.

Outbound calls spend several seconds ringing. Use that window to resolve
workflow config, instantiate STT/TTS/LLM, load VAD, build the graph, and
start pre-call fetch so the first word is not blocked on that work after
answer.

The cache is process-local. Multi-worker deployments still benefit when
prewarm is kicked off on the same worker that later accepts the media
WebSocket (the telephony WS handler). A cache miss is always safe: the
pipeline falls back to the existing setup path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.services.pipecat.audio_config import AudioConfig, create_audio_config
from api.services.pipecat.audio_model_warmup import (
    create_silero_vad_analyzer_async,
    get_warmed_vad,
)
from api.services.pipecat.pre_call_fetch import execute_pre_call_fetch
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph

PREWARM_TTL_SECONDS = 90.0
PREWARM_MAX_ENTRIES = 128


@dataclass
class PipelineResources:
    """Reusable call-setup objects built before the media socket is ready."""

    workflow: Any
    workflow_run: Any
    user_config: Any
    workflow_graph: WorkflowGraph
    stt: Any
    tts: Any
    llm: Any
    inference_llm: Any
    is_realtime: bool
    merged_call_context_vars: dict
    pre_call_fetch_task: asyncio.Task | None
    vad_analyzer: Any | None
    has_recordings: bool
    keyterms: list[str] | None
    max_call_duration_seconds: int
    max_user_idle_timeout: float
    include_transcript_end_timestamps: bool
    audio_config: AudioConfig | None


@dataclass
class _PrewarmEntry:
    task: asyncio.Task
    created_at: float


_prewarm: dict[int, _PrewarmEntry] = {}


def _entry_should_drop(entry: _PrewarmEntry, now: float) -> bool:
    if now - entry.created_at > PREWARM_TTL_SECONDS:
        return True
    if not entry.task.done():
        return False
    if entry.task.cancelled():
        return True
    return entry.task.exception() is not None


def _evict_expired(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for run_id, entry in list(_prewarm.items()):
        if not _entry_should_drop(entry, now):
            continue
        dropped = _prewarm.pop(run_id, None)
        if dropped and not dropped.task.done():
            dropped.task.cancel()

    if len(_prewarm) <= PREWARM_MAX_ENTRIES:
        return
    overflow = sorted(_prewarm.items(), key=lambda item: item[1].created_at)
    for run_id, entry in overflow[: len(_prewarm) - PREWARM_MAX_ENTRIES]:
        if not entry.task.done():
            entry.task.cancel()
        _prewarm.pop(run_id, None)


def reset_pipeline_prewarm() -> None:
    """Cancel in-flight work and clear the registry. Tests only."""
    for entry in list(_prewarm.values()):
        if not entry.task.done():
            entry.task.cancel()
    _prewarm.clear()


def peek_ready_prewarm(workflow_run_id: int) -> Optional[PipelineResources]:
    """Return completed prewarm resources without consuming them.

    Used after answer to skip a second config fetch while the pipeline
    still ``take``s the same objects a moment later.
    """
    entry = _prewarm.get(workflow_run_id)
    if entry is None or not entry.task.done():
        return None
    if entry.task.cancelled():
        return None
    if entry.task.exception() is not None:
        return None
    result = entry.task.result()
    return result if isinstance(result, PipelineResources) else None


def discard_pipeline_prewarm(workflow_run_id: int) -> None:
    """Cancel and drop any prewarm work for a run that will not be used."""
    entry = _prewarm.pop(workflow_run_id, None)
    if entry and not entry.task.done():
        entry.task.cancel()
        logger.debug(f"Discarded pipeline prewarm for run {workflow_run_id}")


def kickoff_pipeline_prewarm(
    *,
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    user_id: int | None = None,
    provider_name: str,
    call_context_vars: dict | None = None,
) -> None:
    """Start preparing pipeline resources in the background if not already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running loop; skipping pipeline prewarm")
        return

    _evict_expired()
    existing = _prewarm.get(workflow_run_id)
    if existing:
        if not existing.task.done():
            return
        if (
            not existing.task.cancelled()
            and existing.task.exception() is None
            and existing.task.result() is not None
        ):
            return

    async def _run() -> PipelineResources | None:
        try:
            return await prepare_pipeline_resources(
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                user_id=user_id,
                provider_name=provider_name,
                call_context_vars=call_context_vars or {},
            )
        except Exception:
            logger.warning(
                f"Pipeline prewarm failed for run {workflow_run_id}",
                exc_info=True,
            )
            return None

    task = asyncio.create_task(
        _run(),
        name=f"pipeline-prewarm-{workflow_run_id}",
    )
    _prewarm[workflow_run_id] = _PrewarmEntry(
        task=task, created_at=time.monotonic()
    )
    logger.info(f"Kicked off pipeline prewarm for run {workflow_run_id}")


async def take_pipeline_prewarm(workflow_run_id: int) -> Optional[PipelineResources]:
    """Pop a completed (or in-flight) prewarm for this run.

    Always awaits in-flight work: it is the same setup an inline rebuild
    would do, and cancelling it at answer just adds a second cold start
    while the caller is already on the line. Returns None on miss or
    failure so the caller can build resources itself.
    """
    entry = _prewarm.pop(workflow_run_id, None)
    if entry is None:
        return None
    try:
        return await entry.task
    except asyncio.CancelledError:
        return None
    except Exception:
        logger.warning(
            f"Pipeline prewarm failed for run {workflow_run_id}; building inline",
            exc_info=True,
        )
        return None


async def prepare_pipeline_resources(
    *,
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    user_id: int | None = None,
    provider_name: str,
    call_context_vars: dict | None = None,
    workflow_run=None,
    resolved_user_config=None,
    audio_config: AudioConfig | None = None,
) -> PipelineResources:
    """Resolve config and construct services/graph/VAD for a workflow run."""
    from api.schemas.workflow_configurations import (
        DEFAULT_MAX_CALL_DURATION_SECONDS,
        DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS,
    )
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )
    from api.services.managed_model_services import (
        MPS_CORRELATION_ID_CONTEXT_KEY,
        ensure_mps_correlation_id,
    )
    from api.services.pipecat.service_factory import (
        create_llm_service,
        create_realtime_llm_service,
        create_stt_service,
        create_tts_service,
    )

    workflow_scope = {"organization_id": organization_id}

    if workflow_run is None:
        # Fetch workflow_run and workflow in parallel — they are independent queries
        workflow_run, workflow = await asyncio.gather(
            db_client.get_workflow_run(workflow_run_id, **workflow_scope),
            db_client.get_workflow(workflow_id, **workflow_scope),
        )
        if not workflow_run:
            raise ValueError(f"Workflow run {workflow_run_id} not found")
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        if workflow_run.workflow_id != workflow_id:
            raise ValueError("workflow_run_workflow_mismatch")
        if workflow_run.is_completed:
            raise ValueError("Workflow run already completed")
    else:
        workflow = await db_client.get_workflow(workflow_id, **workflow_scope)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

    merged_call_context_vars = dict(workflow_run.initial_context or {})
    if call_context_vars:
        merged_call_context_vars = {**merged_call_context_vars, **call_context_vars}

    run_definition = workflow_run.definition
    run_workflow_json = run_definition.workflow_json
    run_configs = run_definition.workflow_configurations or {}

    max_call_duration_seconds = DEFAULT_MAX_CALL_DURATION_SECONDS
    max_user_idle_timeout = DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS
    keyterms = None
    transcript_config = run_configs.get("transcript_configuration") or {}
    include_transcript_end_timestamps = bool(
        transcript_config.get("include_end_timestamps", False)
    )

    if "max_call_duration" in run_configs:
        max_call_duration_seconds = run_configs["max_call_duration"]
    if "max_user_idle_timeout" in run_configs:
        max_user_idle_timeout = run_configs["max_user_idle_timeout"]
    if "dictionary" in run_configs:
        dictionary = run_configs["dictionary"]
        if dictionary and isinstance(dictionary, str):
            keyterms = [
                term.strip() for term in dictionary.split(",") if term.strip()
            ]

    if resolved_user_config is None:
        user_config = await get_effective_ai_model_configuration_for_workflow(
            organization_id=workflow.organization_id,
            workflow_configurations=run_configs,
        )
    else:
        user_config = resolved_user_config


    is_realtime = bool(user_config.is_realtime and user_config.realtime is not None)
    if audio_config is None:
        audio_config = create_audio_config(provider_name)

    recordings_task = asyncio.create_task(
        db_client.has_active_recordings(workflow.organization_id)
    )

    if is_realtime:
        llm = create_realtime_llm_service(user_config, audio_config)
        stt = None
        tts = None
        inference_llm = create_llm_service(
            user_config,
            correlation_id=None,  # MPS id resolved below
        )
        # Gemini Live still uses local Silero for barge-in. Warm it here
        # so answer does not pay the ONNX load.
        _prewarmed = get_warmed_vad()
        if _prewarmed is not None:
            vad_task = None
            _prewarmed_vad = _prewarmed
        else:
            vad_task = asyncio.create_task(create_silero_vad_analyzer_async())
            _prewarmed_vad = None
        # Run MPS correlation lookup in parallel — does not block service creation

        mps_task = asyncio.create_task(
            ensure_mps_correlation_id(
                ai_model_config=user_config,
                workflow_run_id=workflow_run_id,
                initial_context=merged_call_context_vars,
            )
        )
    else:
        # Fire MPS + VAD simultaneously — both are I/O-bound and independent
        mps_task = asyncio.create_task(
            ensure_mps_correlation_id(
                ai_model_config=user_config,
                workflow_run_id=workflow_run_id,
                initial_context=merged_call_context_vars,
            )
        )
        # Reuse pre-warmed VAD singleton if available — avoids 300-800ms ONNX load
        _prewarmed = get_warmed_vad()
        if _prewarmed is not None:
            vad_task = None
            _prewarmed_vad = _prewarmed
        else:
            vad_task = asyncio.create_task(create_silero_vad_analyzer_async())
            _prewarmed_vad = None

        # STT/TTS/LLM creation is synchronous (no I/O) — runs while tasks are in flight
        stt = create_stt_service(
            user_config,
            audio_config,
            keyterms=keyterms,
            correlation_id=None,  # MPS id resolved below
        )
        tts = create_tts_service(
            user_config,
            audio_config,
            correlation_id=None,  # MPS id resolved below
        )
        llm = create_llm_service(user_config, correlation_id=None)
        inference_llm = None

    # Now await MPS result (likely already done since STT/TTS/LLM creation ran above)
    mps_correlation_id = await mps_task
    if mps_correlation_id:
        merged_call_context_vars[MPS_CORRELATION_ID_CONTEXT_KEY] = mps_correlation_id

    if is_realtime:
        runtime_configuration = {
            "realtime_provider": user_config.realtime.provider,
            "realtime_model": user_config.realtime.model,
            "llm_provider": user_config.llm.provider,
            "llm_model": user_config.llm.model,
        }
    else:
        runtime_configuration = {
            "stt_provider": user_config.stt.provider,
            "stt_model": user_config.stt.model,
            "tts_provider": user_config.tts.provider,
            "tts_model": user_config.tts.model,
            "llm_provider": user_config.llm.provider,
            "llm_model": user_config.llm.model,
        }
    merged_call_context_vars = {
        **merged_call_context_vars,
        "runtime_configuration": runtime_configuration,
    }
    await db_client.update_workflow_run(
        workflow_run_id, initial_context=merged_call_context_vars
    )

    workflow_graph = WorkflowGraph(
        ReactFlowDTO.model_validate(run_workflow_json),
        skip_instance_constraints_for={"trigger"},
    )

    pre_call_fetch_task = None
    start_node = workflow_graph.nodes.get(workflow_graph.start_node_id)
    if (
        start_node
        and start_node.pre_call_fetch_enabled
        and start_node.pre_call_fetch_url
    ):
        logger.info(
            f"Pre-call fetch enabled for workflow run {workflow_run_id}, "
            f"firing request to {start_node.pre_call_fetch_url}"
        )
        pre_call_fetch_task = asyncio.create_task(
            execute_pre_call_fetch(
                url=start_node.pre_call_fetch_url,
                credential_uuid=start_node.pre_call_fetch_credential_uuid,
                call_context_vars=merged_call_context_vars,
                workflow_id=workflow_id,
                organization_id=workflow.organization_id,
            )
        )

    has_recordings = await recordings_task
    if vad_task is not None:
        # Task was launched because no warmed singleton was available
        vad_analyzer = await vad_task
    elif _prewarmed_vad is not None:
        # Reuse the pre-warmed singleton — no ONNX load needed
        vad_analyzer = _prewarmed_vad
    else:
        vad_analyzer = None

    logger.info(
        f"Prepared pipeline resources for run {workflow_run_id} "
        f"(realtime={is_realtime}, pre_call_fetch={pre_call_fetch_task is not None})"
    )

    return PipelineResources(
        workflow=workflow,
        workflow_run=workflow_run,
        user_config=user_config,
        workflow_graph=workflow_graph,
        stt=stt,
        tts=tts,
        llm=llm,
        inference_llm=inference_llm,
        is_realtime=is_realtime,
        merged_call_context_vars=merged_call_context_vars,
        pre_call_fetch_task=pre_call_fetch_task,
        vad_analyzer=vad_analyzer,
        has_recordings=has_recordings,
        keyterms=keyterms,
        max_call_duration_seconds=max_call_duration_seconds,
        max_user_idle_timeout=max_user_idle_timeout,
        include_transcript_end_timestamps=include_transcript_end_timestamps,
        audio_config=audio_config,
    )
