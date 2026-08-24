"""Exotel telephony routes (webhooks, ExoML answer URLs, status callbacks).

Mounted under /api/v1/telephony by api.routes.telephony via the
provider registry.
"""

import json
from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import HTMLResponse

from api.db import db_client
from api.services.pipecat.pipeline_prewarm import kickoff_pipeline_prewarm
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.telephony_helper import parse_webhook_request

router = APIRouter()


@router.api_route(
    "/exotel-xml/{workflow_id}/{organization_id}/{workflow_run_id}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def handle_exotel_xml_webhook_path(
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
    request: Request,
):
    """Path-based ExoML answer URL (Exotel requires a clean URL without query strings)."""
    return await _handle_exotel_xml(
        workflow_id, organization_id, workflow_run_id, request=request
    )


@router.api_route("/exotel-xml", methods=["GET", "POST"], include_in_schema=False)
async def handle_exotel_xml_webhook(
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
):
    """Query-string based ExoML answer URL (fallback)."""
    return await _handle_exotel_xml(
        workflow_id, organization_id, workflow_run_id, request=request
    )


async def _handle_exotel_xml(
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
    request: Optional[Request] = None,
):
    """
    Handle Exotel answer webhook — return ExoML that starts a bidirectional stream.
    Exotel calls this URL when the remote party answers the call.
    """
    set_current_run_id(workflow_run_id)
    req_method = request.method if request else "UNKNOWN"
    logger.info(
        f"[run {workflow_run_id}] ===== EXOTEL XML ENDPOINT HIT ===== "
        f"method={req_method}, workflow_id={workflow_id}, org_id={organization_id}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    logger.debug(f"[run {workflow_run_id}] Using provider: {provider.PROVIDER_NAME}")

    if workflow_run:
        kickoff_pipeline_prewarm(
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            provider_name=provider.PROVIDER_NAME,
        )

    response_content = await provider.get_webhook_response(
        workflow_id, organization_id, workflow_run_id
    )

    logger.info(
        f"[run {workflow_run_id}] Returning Exotel XML:\n{response_content}"
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/exotel/hangup-callback/{workflow_run_id}")
async def handle_exotel_hangup_callback(
    workflow_run_id: int,
    request: Request,
):
    """
    Handle Exotel StatusCallback when a call ends (terminal event).
    Exotel POSTs to StatusCallback URL with call duration/status.
    """
    set_current_run_id(workflow_run_id)

    callback_data, _ = await parse_webhook_request(request)

    logger.info(
        f"[run {workflow_run_id}] Received Exotel hangup callback: "
        f"{json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for Exotel hangup callback"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    parsed_data = provider.parse_status_callback(callback_data)

    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    await _process_status_update(workflow_run_id, status_update)

    logger.info(
        f"[run {workflow_run_id}] Exotel hangup callback processed successfully"
    )
    return {"status": "success"}


@router.post("/exotel/hangup-callback/workflow/{workflow_id}")
async def handle_exotel_hangup_callback_by_workflow(
    workflow_id: int,
    request: Request,
):
    """Handle Exotel hangup callback identified by workflow_id + CallSid lookup."""
    try:
        callback_data, _ = await parse_webhook_request(request)
    except ValueError:
        callback_data = {}

    call_sid = callback_data.get("CallSid", "")
    logger.info(
        f"[workflow {workflow_id}] Exotel hangup callback for call {call_sid}: "
        f"{json.dumps(callback_data)}"
    )

    if not call_sid:
        logger.warning(f"[workflow {workflow_id}] No CallSid in Exotel hangup callback")
        return {"status": "error", "message": "No CallSid found"}

    workflow = await db_client.get_workflow_by_id(workflow_id)
    if not workflow:
        return {"status": "error", "message": "workflow_not_found"}

    try:
        workflow_run = await db_client.get_workflow_run_by_call_id(call_sid)
    except Exception as e:
        logger.error(f"[workflow {workflow_id}] Error finding run for call {call_sid}: {e}")
        return {"status": "error", "message": str(e)}

    if not workflow_run or workflow_run.workflow_id != workflow_id:
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)

    provider = await get_telephony_provider_for_run(workflow_run, workflow.organization_id)

    try:
        parsed_data = provider.parse_status_callback(callback_data)
        status = StatusCallbackRequest(
            call_id=parsed_data["call_id"],
            status=parsed_data["status"],
            from_number=parsed_data.get("from_number"),
            to_number=parsed_data.get("to_number"),
            direction=parsed_data.get("direction"),
            duration=parsed_data.get("duration"),
            extra=parsed_data.get("extra", {}),
        )
        await _process_status_update(workflow_run_id, status)
        logger.info(f"[run {workflow_run_id}] Exotel hangup (workflow path) processed")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[run {workflow_run_id}] Error processing Exotel hangup: {e}")
        return {"status": "error", "message": str(e)}
