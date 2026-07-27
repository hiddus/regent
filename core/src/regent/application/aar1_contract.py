"""AAR-1 Contract (M5/M6) phase helpers.

Contract stops legacy dual-write as source of truth and closes production
in-memory A2A. Adaptive rollout remains ROLLOUT_NOT_ALLOWED.
"""

from __future__ import annotations

from typing import Literal

Aar1Phase = Literal["expand", "dual_write", "read_switch", "enforce", "contract"]

CONTRACT_PHASE: Aar1Phase = "contract"


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
