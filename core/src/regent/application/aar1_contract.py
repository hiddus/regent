"""AAR-1 Contract (M5/M6) phase helpers.

Contract stops legacy dual-write as source of truth and closes production
in-memory A2A. Adaptive rollout remains ROLLOUT_NOT_ALLOWED.

Certified hive (pm-dev-independent-qa-v1) is an opt-in fixed template path —
not adaptive free-form multi-agent.
"""

from __future__ import annotations

from typing import Literal

Aar1Phase = Literal["expand", "dual_write", "read_switch", "enforce", "contract"]

CONTRACT_PHASE: Aar1Phase = "contract"

# Seeded certified fixed hive (Durable Hive F2). Not an adaptive topology.
CERTIFIED_HIVE_TEMPLATE_ID = "pm-dev-independent-qa-v1"
SINGLE_AGENT_TEMPLATE_ID = "single-agent-v1"


def is_contract_phase(phase: str | None) -> bool:
    return phase == CONTRACT_PHASE


def memory_a2a_allowed(*, phase: str, use_memory_override: bool | None) -> bool:
    """Return whether in-memory A2A may be used.

    - Explicit override wins (unit tests may pass use_memory=True).
    - Contract (M6): memory path closed regardless of durable wiring.
    - Enforce (M4): memory closed only when durable tasks are expected
      (caller passes durable + default override None → False).
    """
    if use_memory_override is not None:
        return use_memory_override
    return not is_contract_phase(phase)


def legacy_org_writes_allowed(phase: str) -> bool:
    """Pre-Contract phases may dual-write / fail-open to legacy organize fields."""
    return phase in {"expand", "dual_write", "read_switch", "enforce"}


def engine_is_primary_writer(phase: str) -> bool:
    """Contract: OrganizationEngine is the sole organization topology writer."""
    return is_contract_phase(phase)


def certified_hive_preferred(*, enabled: bool) -> str | None:
    """Return preferred certified hive template id when opt-in is on.

    ROLLOUT_NOT_ALLOWED: never invent free-form topologies; only the certified
    fixed template may be preferred over the single-agent champion.
    """
    if not enabled:
        return None
    return CERTIFIED_HIVE_TEMPLATE_ID


def is_certified_hive_topology(topology: dict | None) -> bool:
    if not topology:
        return False
    template_id = str(topology.get("template_id") or "")
    strategy = str(topology.get("strategy") or "")
    return template_id == CERTIFIED_HIVE_TEMPLATE_ID or (
        strategy == "FIXED_TEMPLATE"
        and template_id.startswith("pm-dev")
        and any(
            (r.get("role") == "qa" or r.get("independent_reviewer"))
            for r in (topology.get("roles") or [])
        )
    )
