"""A2A compatibility projection — boundary only, not an internal kernel protocol.

Spec §17.1: internal agents keep full-trajectory AgentEnvelope; A2A is a
cross-org interop projection. Agent Card declares identity/capability only
and never grants Goal permissions.
"""

from __future__ import annotations

from typing import Any, Mapping

A2A_PROJECTION_VERSION = "a2a-projection/v1"

_RUN_STATUS_MAP = {
    "QUEUED": "submitted",
    "RUNNING": "working",
    "SUCCEEDED": "completed",
    "EXECUTED": "completed",
    "FAILED": "failed",
    "CANCELLED": "canceled",
    "WAITING_HUMAN": "input_required",
}


def project_context_id(*, goal_id: str, correlation_id: str | None = None) -> str:
    return str(correlation_id or goal_id)


def project_run_state(status: str) -> str:
    normalized = status.upper()
    if normalized not in _RUN_STATUS_MAP:
        raise ValueError(f"unsupported Regent run state for A2A projection: {status}")
    return _RUN_STATUS_MAP[normalized]


def project_auth_required(*, permit_pending: bool) -> str | None:
    return "auth_required" if permit_pending else None


def project_agent_card(
    *,
    agent_id: str,
    name: str,
    capabilities: list[str],
    model_ref: str | None = None,
) -> dict[str, Any]:
    """Restricted Agent Card view — identity + capability declaration only."""
    return {
        "schema_version": A2A_PROJECTION_VERSION,
        "agent_id": agent_id,
        "name": name,
        "capabilities": list(capabilities),
        "model_ref": model_ref,
        "grants_goal_permission": False,
        "note": "Agent Card cannot grant current Goal permissions; Permit/fencing required",
    }


def project_envelope_to_a2a(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Map a Regent AgentEnvelope-like dict to an A2A boundary projection."""
    goal_id = str(envelope.get("goal_id") or "")
    correlation_id = envelope.get("correlation_id")
    status = str(envelope.get("status") or envelope.get("run_status") or "RUNNING")
    permit_pending = bool(envelope.get("permit_pending"))
    projection = {
        "schema_version": A2A_PROJECTION_VERSION,
        "contextId": project_context_id(goal_id=goal_id, correlation_id=correlation_id),
        "state": project_run_state(status),
        "auth": project_auth_required(permit_pending=permit_pending),
        "untrusted_data": True,
        "ingress_requirements": [
            "identity_allowlist",
            "signature_verification",
            "permit_fencing",
            "tenant_isolation",
            "UNTRUSTED_DATA_mark",
        ],
        "internal_envelope_retained": True,
    }
    if status.upper() == "WAITING_HUMAN":
        projection["state"] = "input_required"
    return projection


def assert_not_replacing_kernel(
    framework_name: str | None, *, replaces_kernel: bool = False
) -> None:
    """Reject framework use only when it replaces Regent kernel responsibilities."""
    if framework_name and replaces_kernel:
        raise ValueError(
            f"{framework_name} must not replace Regent Kernel "
            "(Outbox/Lease/state machines/evidence chain)"
        )
