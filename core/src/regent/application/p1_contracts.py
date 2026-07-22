import hashlib
import json
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from regent.domain.errors import DomainError, ErrorCode


class EvidenceClassification(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class EvidenceClaim(BaseModel):
    claim_key: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1)
    classification: EvidenceClassification
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class ProductHypothesisProposal(BaseModel):
    candidate_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    target_users_hypothesis: str = Field(min_length=1)
    problem_hypothesis: str = Field(min_length=1)
    value_proposition: str = Field(min_length=1)
    candidate_solution: str = Field(min_length=1)
    minimum_validation: str = Field(min_length=1)
    success_signals: list[str] = Field(min_length=1)
    failure_signals: list[str] = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimated_cost: dict[str, int | float | str] = Field(default_factory=dict)
    estimated_time: str = Field(min_length=1)
    reversibility: str = Field(min_length=1)
    claims: list[EvidenceClaim] = Field(min_length=1)


class HypothesisDecisionValue(StrEnum):
    SELECT = "SELECT"
    RESEARCH_MORE = "RESEARCH_MORE"
    STOP = "STOP"


class HypothesisSelection(BaseModel):
    decision: HypothesisDecisionValue
    selected_candidate_key: str | None = None
    comparison: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    missing_evidence: list[str] = Field(default_factory=list)
    next_validation: str | None = None
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selected_key(self) -> "HypothesisSelection":
        if self.decision == HypothesisDecisionValue.SELECT and not self.selected_candidate_key:
            raise ValueError("SELECT requires selected_candidate_key")
        if (
            self.decision != HypothesisDecisionValue.SELECT
            and self.selected_candidate_key is not None
        ):
            raise ValueError("only SELECT may provide selected_candidate_key")
        return self


class AppRequirementProposal(BaseModel):
    product_outcome: str = Field(min_length=1)
    target_users: list[str] = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    value_proposition: str = Field(min_length=1)
    user_journeys: list[dict[str, Any]] = Field(min_length=1)
    functional_requirements: list[dict[str, Any]] = Field(min_length=1)
    non_functional_requirements: list[dict[str, Any]] = Field(default_factory=list)
    data_requirements: list[dict[str, Any]] = Field(default_factory=list)
    external_integrations: list[dict[str, Any]] = Field(default_factory=list)
    success_metrics: list[dict[str, Any]] = Field(min_length=1)
    event_definitions: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    governance_requirements: dict[str, Any] = Field(default_factory=dict)
    release_gates: list[dict[str, Any]] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    source_evidence: list[uuid.UUID] = Field(default_factory=list)


class FileOperation(StrEnum):
    CREATE = "CREATE"
    REPLACE = "REPLACE"
    DELETE = "DELETE"


class FileMode(StrEnum):
    REGULAR = "REGULAR"
    EXECUTABLE = "EXECUTABLE"


class FileChange(BaseModel):
    relative_path: str = Field(min_length=1, max_length=512)
    operation: FileOperation
    content_artifact_uri: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mode: FileMode = FileMode.REGULAR
    media_type: str = "text/plain"
    rationale: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or ":" in parts[0]
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("path must be normalized and relative")
        if parts[0] in {".git", ".regent"}:
            raise ValueError("reserved path")
        return normalized


class FileChangeSet(BaseModel):
    changes: list[FileChange] = Field(min_length=1)
    generator_ref: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: Literal["generation-files-v1"] = "generation-files-v1"


class GenerationPlanContract(BaseModel):
    goal_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_decision_id: uuid.UUID
    requirement_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_resolution_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_profile_ref: Literal["python-web-v1"] = "python-web-v1"
    runtime_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_bundle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_ref: str = Field(min_length=1)
    model_ref: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: Literal["generation-plan-v1"] = "generation-plan-v1"
    budget: dict[str, int | float] = Field(default_factory=dict)
    planned_paths: list[str] = Field(default_factory=list)
    dependency_intents: list[dict[str, Any]] = Field(default_factory=list)
    verification_commands: list[str] = Field(min_length=1)
    acceptance_contract: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSnapshotContract(BaseModel):
    generation_run_id: uuid.UUID
    manifest_uri: str = Field(min_length=1)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_archive_uri: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    runtime_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_hash(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_evidence_references(
    hypotheses: list[ProductHypothesisProposal],
    available_evidence_ids: set[uuid.UUID],
) -> None:
    for hypothesis in hypotheses:
        for claim in hypothesis.claims:
            missing = set(claim.evidence_ids) - available_evidence_ids
            if missing:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"claim {claim.claim_key} references unavailable evidence",
                )
            if claim.classification is EvidenceClassification.OBSERVED and not claim.evidence_ids:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"observed claim {claim.claim_key} requires evidence",
                )


def inherit_constraints(
    root_constraints: dict[str, Any],
    proposed_constraints: dict[str, Any],
) -> dict[str, Any]:
    conflicts = {
        key
        for key, value in root_constraints.items()
        if key in proposed_constraints and proposed_constraints[key] != value
    }
    if conflicts:
        raise DomainError(
            ErrorCode.POLICY_DENIED,
            f"cannot override root constraints: {sorted(conflicts)}",
        )
    return {**proposed_constraints, **root_constraints}
