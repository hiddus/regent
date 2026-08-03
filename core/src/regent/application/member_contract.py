"""Fixed-template member contracts and whole-template certification (PRD §10.3 / Spec §18.5).

Each member freezes: role boundary + non-duties, capability/tool allowlist +
delegation scope, stop/clarify/fail/handoff conditions.
Template certification binds member_manifest, topology, model endpoints,
prompt/skill/tool, and verification contract hashes. Any digest change
creates a new version; old certifications must not be inherited.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field, field_validator

from regent.application.aar1_contract import CERTIFIED_HIVE_TEMPLATE_ID
from regent.application.p1_contracts import canonical_hash

MEMBER_CONTRACT_SCHEMA_VERSION = "member-contract/v1"
TEMPLATE_CERT_VERSION = "template-certification/v1"


class MemberContract(BaseModel):
    """Three-element frozen contract for one template member."""

    role: str = Field(min_length=1, max_length=64)
    responsibilities: list[str] = Field(min_length=1)
    non_responsibilities: list[str] = Field(min_length=1)
    capability_allowlist: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)
    delegatable_to: list[str] = Field(default_factory=list)
    max_delegation_depth: int = Field(default=0, ge=0, le=8)
    stop_conditions: list[str] = Field(min_length=1)
    clarify_conditions: list[str] = Field(min_length=1)
    fail_conditions: list[str] = Field(min_length=1)
    handoff_conditions: list[str] = Field(min_length=1)
    independent_reviewer: bool = False
    clarification_required_on_uncertainty: bool = True

    @field_validator(
        "responsibilities",
        "non_responsibilities",
        "stop_conditions",
        "clarify_conditions",
        "fail_conditions",
        "handoff_conditions",
        mode="before",
    )
    @classmethod
    def _non_empty_strings(cls, value: Any) -> Any:
        if isinstance(value, list) and any(not str(v).strip() for v in value):
            raise ValueError("contract lists must not contain empty strings")
        return value

    def content_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class TemplateCertificationDigest(BaseModel):
    template_id: str
    semantic_version: str
    member_manifest_hash: str
    topology_hash: str
    model_endpoint_hash: str
    prompt_skill_tool_hash: str
    verification_contract_hash: str
    certification_hash: str
    schema_version: str = TEMPLATE_CERT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class CertificationCheck:
    accepted: bool
    reason: str
    expected_hash: str | None = None
    provided_hash: str | None = None
    digest: TemplateCertificationDigest | None = None


def certified_hive_member_contracts() -> list[MemberContract]:
    """Frozen three-element contracts for pm-dev-independent-qa-v1."""
    return [
        MemberContract(
            role="pm",
            responsibilities=[
                "decompose goal into delivery plan",
                "clarify ambiguous requirements",
                "hand off scoped work packages to dev",
            ],
            non_responsibilities=[
                "write production source artifacts",
                "approve own verification as QA",
                "grant Permit or alter fencing",
            ],
            capability_allowlist=["delivery-review-v1"],
            tool_allowlist=["todo_write", "read_file", "list_files"],
            delegatable_to=["dev"],
            max_delegation_depth=1,
            stop_conditions=["goal cancelled", "hard budget exhausted"],
            clarify_conditions=[
                "requirement ambiguity above template threshold",
                "missing acceptance criteria",
            ],
            fail_conditions=["unrecoverable planning contradiction"],
            handoff_conditions=["plan frozen with acceptance criteria"],
            clarification_required_on_uncertainty=True,
        ),
        MemberContract(
            role="dev",
            responsibilities=[
                "implement scoped work packages",
                "produce file artifacts in sandbox",
                "report blockers and evidence refs",
            ],
            non_responsibilities=[
                "self-certify as independent QA",
                "change organization topology",
                "bypass Permit for external side effects",
            ],
            capability_allowlist=["product-surface-v1"],
            tool_allowlist=[
                "list_files",
                "read_file",
                "write_file",
                "edit_file",
                "run_command",
                "todo_write",
                "plan_list",
                "plan_update",
                "delegate_plan_item",
                "submit",
            ],
            delegatable_to=[],
            max_delegation_depth=1,
            stop_conditions=["work package complete", "blocker requires human"],
            clarify_conditions=["spec gap blocking implementation"],
            fail_conditions=["sandbox integrity failure", "repeated compile failure"],
            handoff_conditions=["artifacts ready for independent QA"],
            clarification_required_on_uncertainty=True,
        ),
        MemberContract(
            role="qa",
            responsibilities=[
                "independently verify artifacts against acceptance criteria",
                "reject on evidence failure",
                "record verification evidence",
            ],
            non_responsibilities=[
                "author the artifacts under review",
                "share writer identity or credentials with producer",
                "silently waive verification gaps",
            ],
            capability_allowlist=["delivery-review-v1"],
            tool_allowlist=["read_file", "list_files", "run_command"],
            delegatable_to=[],
            max_delegation_depth=1,
            stop_conditions=["verification passed", "verification failed terminal"],
            clarify_conditions=["acceptance criteria incomplete"],
            fail_conditions=["verifier toolchain failure"],
            handoff_conditions=["verification report sealed"],
            independent_reviewer=True,
            clarification_required_on_uncertainty=True,
        ),
    ]


def enrich_topology_with_member_contracts(
    topology: Mapping[str, Any],
    *,
    members: Sequence[MemberContract] | None = None,
) -> dict[str, Any]:
    """Attach member contracts to a topology copy (does not mutate input)."""
    topo = dict(topology)
    contracts = list(members or [])
    if not contracts and str(topo.get("template_id") or "") == CERTIFIED_HIVE_TEMPLATE_ID:
        contracts = certified_hive_member_contracts()
    if not contracts:
        return topo
    by_role = {c.role: c.model_dump(mode="json") for c in contracts}
    roles = []
    for role in list(topo.get("roles") or []):
        item = dict(role)
        contract = by_role.get(str(item.get("role") or ""))
        if contract:
            item["member_contract"] = contract
            item["member_contract_hash"] = MemberContract.model_validate(contract).content_hash()
            item["clarification_required_on_uncertainty"] = contract[
                "clarification_required_on_uncertainty"
            ]
        roles.append(item)
    topo["roles"] = roles
    topo["member_contracts_schema"] = MEMBER_CONTRACT_SCHEMA_VERSION
    topo["member_manifest_hash"] = member_manifest_hash(contracts)
    return topo


def member_manifest_hash(members: Sequence[MemberContract]) -> str:
    payload = [m.model_dump(mode="json") for m in sorted(members, key=lambda m: m.role)]
    return canonical_hash({"schema": MEMBER_CONTRACT_SCHEMA_VERSION, "members": payload})


def compute_template_certification(
    *,
    template_id: str,
    semantic_version: str,
    topology: Mapping[str, Any],
    model_endpoints: Mapping[str, Any] | None = None,
    prompt_skill_tool: Mapping[str, Any] | None = None,
    verification_contract: Mapping[str, Any] | None = None,
    members: Sequence[MemberContract] | None = None,
) -> TemplateCertificationDigest:
    contracts = list(members or [])
    if not contracts:
        embedded = []
        for role in topology.get("roles") or []:
            mc = role.get("member_contract")
            if mc:
                embedded.append(MemberContract.model_validate(mc))
        contracts = embedded or (
            certified_hive_member_contracts()
            if template_id == CERTIFIED_HIVE_TEMPLATE_ID
            else []
        )
    member_hash = member_manifest_hash(contracts) if contracts else canonical_hash([])
    topology_for_hash = {
        k: v for k, v in dict(topology).items() if k not in {"member_manifest_hash"}
    }
    topo_hash = canonical_hash(topology_for_hash)
    model_hash = canonical_hash(dict(model_endpoints or {"model_ref": "configured-model"}))
    pst_hash = canonical_hash(dict(prompt_skill_tool or {}))
    verify_hash = canonical_hash(
        dict(
            verification_contract
            or {
                "invariants": list(topology.get("invariants") or []),
                "independent_qa": any(
                    r.get("independent_reviewer") for r in (topology.get("roles") or [])
                ),
            }
        )
    )
    cert_payload = {
        "template_id": template_id,
        "semantic_version": semantic_version,
        "member_manifest_hash": member_hash,
        "topology_hash": topo_hash,
        "model_endpoint_hash": model_hash,
        "prompt_skill_tool_hash": pst_hash,
        "verification_contract_hash": verify_hash,
        "schema_version": TEMPLATE_CERT_VERSION,
    }
    certification_hash = canonical_hash(cert_payload)
    return TemplateCertificationDigest(
        template_id=template_id,
        semantic_version=semantic_version,
        member_manifest_hash=member_hash,
        topology_hash=topo_hash,
        model_endpoint_hash=model_hash,
        prompt_skill_tool_hash=pst_hash,
        verification_contract_hash=verify_hash,
        certification_hash=certification_hash,
    )


def validate_certification_inheritance(
    *,
    previous: TemplateCertificationDigest | Mapping[str, Any],
    current: TemplateCertificationDigest | Mapping[str, Any],
) -> CertificationCheck:
    """Reject inheriting an old certification when any digest component changed."""
    prev = (
        previous
        if isinstance(previous, TemplateCertificationDigest)
        else TemplateCertificationDigest.model_validate(previous)
    )
    curr = (
        current
        if isinstance(current, TemplateCertificationDigest)
        else TemplateCertificationDigest.model_validate(current)
    )
    if prev.certification_hash == curr.certification_hash:
        return CertificationCheck(
            accepted=True,
            reason="identical_certification",
            expected_hash=prev.certification_hash,
            provided_hash=curr.certification_hash,
            digest=curr,
        )
    changed = []
    for field_name in (
        "member_manifest_hash",
        "topology_hash",
        "model_endpoint_hash",
        "prompt_skill_tool_hash",
        "verification_contract_hash",
        "semantic_version",
        "template_id",
    ):
        if getattr(prev, field_name) != getattr(curr, field_name):
            changed.append(field_name)
    return CertificationCheck(
        accepted=False,
        reason=f"certification_invalidated:{','.join(changed) or 'certification_hash'}",
        expected_hash=prev.certification_hash,
        provided_hash=curr.certification_hash,
        digest=curr,
    )


def verify_template_certification(
    *,
    template_id: str,
    semantic_version: str,
    topology: Mapping[str, Any],
) -> CertificationCheck:
    """Fail closed unless an embedded approved digest matches current bindings."""
    embedded = topology.get("template_certification")
    if not isinstance(embedded, Mapping):
        return CertificationCheck(accepted=False, reason="certification_digest_missing")
    try:
        approved = TemplateCertificationDigest.model_validate(embedded)
    except Exception:
        return CertificationCheck(accepted=False, reason="certification_digest_invalid")
    current = compute_template_certification(
        template_id=template_id,
        semantic_version=semantic_version,
        topology={k: v for k, v in topology.items() if k != "template_certification"},
    )
    return validate_certification_inheritance(previous=approved, current=current)


def certification_safety_confirmation(
    check: CertificationCheck,
    *,
    template_id: str,
) -> dict[str, Any]:
    """Wrap a failed certification check as ConfirmationRequest payload."""
    from regent.application.confirmation import safety_invariant_request

    return safety_invariant_request(
        action="verify_template_certification",
        summary=f"模板认证未通过：{template_id}",
        rationale="未通过认证的 hive/template 不得进入可行候选（fail-closed）",
        rules_applied=("template_certification", "verify_template_certification"),
        detail=f"reason={check.reason}; expected={check.expected_hash}; provided={check.provided_hash}",
    ).as_dict()

TEMPLATE_REGRESSION_SCENARIOS: tuple[str, ...] = (
    "happy_path",
    "clarification_required",
    "peer_output_conflict",
    "verifier_reject",
    "fault_injection",
)


@dataclass
class TemplateRegressionResult:
    scenario: str
    passed: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def run_template_regression_suite(
    *,
    template_id: str,
    topology: Mapping[str, Any],
    certification: TemplateCertificationDigest,
    scenario_hooks: Mapping[str, Any] | None = None,
) -> list[TemplateRegressionResult]:
    """Whole-template regression: normal / clarify / peer conflict / verifier reject / inject.

    Hooks may override scenario outcomes for integration tests; default is
    structural contract checks (no external LLM).
    """
    hooks = dict(scenario_hooks or {})
    members = []
    for role in topology.get("roles") or []:
        mc = role.get("member_contract")
        if mc:
            members.append(MemberContract.model_validate(mc))
    if not members and template_id == CERTIFIED_HIVE_TEMPLATE_ID:
        members = certified_hive_member_contracts()

    results: list[TemplateRegressionResult] = []
    for scenario in TEMPLATE_REGRESSION_SCENARIOS:
        if scenario in hooks:
            outcome = hooks[scenario]
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=bool(outcome.get("passed")),
                    detail=str(outcome.get("detail") or "hook"),
                    evidence=dict(outcome.get("evidence") or {}),
                )
            )
            continue
        if scenario == "happy_path":
            ok = bool(members) and certification.certification_hash
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=ok,
                    detail="member contracts + certification present",
                )
            )
        elif scenario == "clarification_required":
            ok = all(m.clarification_required_on_uncertainty for m in members) and all(
                m.clarify_conditions for m in members
            )
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=ok,
                    detail="all members require clarify on uncertainty",
                )
            )
        elif scenario == "peer_output_conflict":
            roles = {m.role for m in members}
            ok = "qa" in roles and "dev" in roles
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=ok,
                    detail="producer/reviewer roles present for conflict handling",
                )
            )
        elif scenario == "verifier_reject":
            qa = next((m for m in members if m.independent_reviewer or m.role == "qa"), None)
            ok = qa is not None and "reject" in " ".join(qa.responsibilities + qa.fail_conditions).lower()
            # also accept explicit reject language in stop/fail
            if qa and not ok:
                blob = " ".join(
                    qa.responsibilities + qa.fail_conditions + qa.stop_conditions
                ).lower()
                ok = "reject" in blob or "verification failed" in blob or "failed" in blob
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=bool(ok),
                    detail="independent verifier can reject",
                )
            )
        elif scenario == "fault_injection":
            ok = "producer_reviewer_separation" in set(topology.get("invariants") or [])
            results.append(
                TemplateRegressionResult(
                    scenario=scenario,
                    passed=ok,
                    detail="topology keeps producer/reviewer separation under injection",
                )
            )
    return results


def sha256_hex(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
