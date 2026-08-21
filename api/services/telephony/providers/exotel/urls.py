"""Exotel webhook URL builders."""


def build_exotel_answer_url(
    backend_endpoint: str,
    *,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
) -> str:
    """Return the ExoML answer URL for an outbound/inbound Exotel call."""
    return (
        f"{backend_endpoint}/api/v1/telephony"
        f"/exotel-xml/{workflow_id}/{organization_id}/{workflow_run_id}"
    )


def build_exotel_hangup_url(
    backend_endpoint: str,
    *,
    workflow_run_id: int,
) -> str:
    """Return the hangup status-callback URL for an Exotel call."""
    return (
        f"{backend_endpoint}/api/v1/telephony"
        f"/exotel/hangup-callback/{workflow_run_id}"
    )
