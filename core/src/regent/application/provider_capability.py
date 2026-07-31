"""Provider capability matrix for durable external effects (G0)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderCapability(StrEnum):
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    QUERY_BY_OPERATION_KEY = "QUERY_BY_OPERATION_KEY"
    QUERY_BY_EXTERNAL_ID = "QUERY_BY_EXTERNAL_ID"
    NATIVE_FENCING = "NATIVE_FENCING"
    CANCEL_BEFORE_COMMIT = "CANCEL_BEFORE_COMMIT"


@dataclass(frozen=True, slots=True)
class ProviderCapabilityProfile:
    name: str
    capabilities: frozenset[ProviderCapability]
    irreversible: bool = True

    def allows_auto_irreversible(self) -> bool:
        if not self.irreversible:
            return True
        caps = self.capabilities
        return ProviderCapability.IDEMPOTENT_REPLAY in caps or (
            ProviderCapability.QUERY_BY_OPERATION_KEY in caps
            or ProviderCapability.QUERY_BY_EXTERNAL_ID in caps
        )


# Registry: extend as providers are certified. Missing entry = fail-closed for irreversible.
PROVIDER_CAPABILITY_MATRIX: dict[str, ProviderCapabilityProfile] = {
    "allowlisted-http-source-v1": ProviderCapabilityProfile(
        name="allowlisted-http-source-v1",
        capabilities=frozenset(
            {
                ProviderCapability.IDEMPOTENT_REPLAY,
                ProviderCapability.QUERY_BY_OPERATION_KEY,
            }
        ),
        irreversible=False,  # fetch is read-side; still tracked for UNKNOWN
    ),
    "static-preview-deploy-v1": ProviderCapabilityProfile(
        name="static-preview-deploy-v1",
        capabilities=frozenset(
            {
                ProviderCapability.IDEMPOTENT_REPLAY,
                ProviderCapability.QUERY_BY_OPERATION_KEY,
                ProviderCapability.QUERY_BY_EXTERNAL_ID,
            }
        ),
        irreversible=True,
    ),
    "mcp-governed-v1": ProviderCapabilityProfile(
        name="mcp-governed-v1",
        capabilities=frozenset(
            {
                ProviderCapability.IDEMPOTENT_REPLAY,
                ProviderCapability.QUERY_BY_OPERATION_KEY,
            }
        ),
        irreversible=True,
    ),
    "scheduler-dispatch-v1": ProviderCapabilityProfile(
        name="scheduler-dispatch-v1",
        capabilities=frozenset(
            {
                ProviderCapability.IDEMPOTENT_REPLAY,
                ProviderCapability.QUERY_BY_OPERATION_KEY,
            }
        ),
        irreversible=True,
    ),
    # CD-7.2: controlled capability package download (read-side; still EO-tracked).
    "capability-acquire-v1": ProviderCapabilityProfile(
        name="capability-acquire-v1",
        capabilities=frozenset(
            {
                ProviderCapability.IDEMPOTENT_REPLAY,
                ProviderCapability.QUERY_BY_OPERATION_KEY,
            }
        ),
        irreversible=False,
    ),
}


def require_auto_irreversible(provider: str) -> ProviderCapabilityProfile:
    profile = PROVIDER_CAPABILITY_MATRIX.get(provider)
    if profile is None:
        raise PermissionError(f"provider not registered in capability matrix: {provider}")
    if not profile.allows_auto_irreversible():
        raise PermissionError(
            f"provider {provider} lacks IDEMPOTENT_REPLAY or query capability "
            "for irreversible auto dispatch"
        )
    return profile
