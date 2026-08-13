"""PenguinHarness-inspired harness evolution for Regent skill packs.

Does **not** retrain model weights. Improves the Agent Harness around the model:
Evaluate (product QA gaps) → Diagnose → Edit LESSONS.md (agent/skill state) →
Re-score → keep only on **strict improvement**, else rollback.

Roles mirrored from PenguinHarness:
- Target Agent harness = skill GUIDANCE + LESSONS overlays
  (PM / Product / Tech / Test / UX / Ops)
- Evaluator = Live Preview QA + Delivery Role Swarm + blocking gap codes
- Optimizer = this service (hypothesis → lesson edit → gated accept);
  failed delivery roles must re-verify before Preview success
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from regent.agent.skills import SKILLS_ROOT, load_skill_manifest
from regent.application.delivery_success_policy import is_blocking_delivery_gap_code
from regent.model import ModelProvider

# Gap code → skill pack that owns the harness lesson.
_GAP_TO_SKILL: dict[str, str] = {
    "stylesheet-present": "ui",
    "stylesheet-substance": "ui",
    "styled-surface": "ui",
    "semantic-main": "ui",
    "product-structure": "ui",
    "preview-asset-reachability": "ui",
    "preview-internal-nav": "ui",
    "preview-home-reachable": "runtime-contract",
    "preview-browse-url": "runtime-contract",
    "min-visible-text": "product",
    "forbid-demo-shell": "product",
    "forbid-demo-copy": "product",
    "forbid-placeholder-content": "product",
    "goal-semantic-alignment": "product",
    "SMOKE_FAILED": "http-api",
    "preview_qa:stylesheet-substance": "ui",
    "preview_qa:styled-surface": "ui",
    "preview_qa:preview-internal-nav": "ui",
    "preview-content-depth": "product",
    "preview_qa:preview-content-depth": "product",
    "hive-live-content-review": "product",
    "delivery-role-swarm": "product",
    "delivery-product-outline": "product",
    "delivery-tech-api": "http-api",
    "delivery-test-scenarios": "test-harness",
    "delivery-ux-surface": "ui",
    "delivery-ops-host": "ops-environment",
    "HOST_RESOURCE": "ops-environment",
    "disk_percent": "ops-environment",
    "mem_percent": "ops-environment",
    "preview_venv_count": "ops-environment",
    "load1_per_cpu": "ops-environment",
}

_FORBIDDEN_LESSON_PATTERNS = (
    re.compile(r"disable\s+(live\s+)?(preview\s+)?qa", re.I),
    re.compile(r"skip\s+(product|delivery)\s+gate", re.I),
    re.compile(r"soft[- ]?pass\s+without", re.I),
    re.compile(r"ignore\s+blocking", re.I),
)

_MAX_LESSON_CHARS = 4000
_MIN_LESSON_CHARS = 80


class HarnessLessonProposal(BaseModel):
    skill_id: str = Field(min_length=1, max_length=64)
    lesson_markdown: str = Field(min_length=_MIN_LESSON_CHARS, max_length=_MAX_LESSON_CHARS)
    addressed_gaps: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=2000)
    role: str = Field(pattern=r"^(PM|Product|Tech|Test|UX|Ops)$")


@dataclass(frozen=True, slots=True)
class HarnessEvolutionReceipt:
    id: uuid.UUID
    status: str  # REJECTED | ACCEPTED | NOOP
    skill_id: str
    baseline_hash: str
    candidate_hash: str | None
    baseline_score: float
    candidate_score: float
    gaps: list[str]
    lesson_path: str | None
    snapshot_path: str | None
    reason: str
    role: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "status": self.status,
            "skill_id": self.skill_id,
            "baseline_hash": self.baseline_hash,
            "candidate_hash": self.candidate_hash,
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "gaps": list(self.gaps),
            "lesson_path": self.lesson_path,
            "snapshot_path": self.snapshot_path,
            "reason": self.reason,
            "role": self.role,
            "evidence": dict(self.evidence),
        }


def lessons_root(workspace_root: Path) -> Path:
    return Path(workspace_root) / "harness-lessons"


def lesson_file(workspace_root: Path, skill_id: str) -> Path:
    return lessons_root(workspace_root) / skill_id / "LESSONS.md"


def read_lessons(workspace_root: Path, skill_id: str) -> str:
    path = lesson_file(workspace_root, skill_id)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def map_gaps_to_skills(gaps: list[str]) -> dict[str, list[str]]:
    """Return skill_id → list of gap codes owned by that skill."""
    out: dict[str, list[str]] = {}
    for raw in gaps:
        code = str(raw or "").strip()
        if not code:
            continue
        head = code.split(":", 1)[-1] if code.startswith("preview_qa:") else code
        head = head.split(":", 1)[0].strip()
        skill = _GAP_TO_SKILL.get(code) or _GAP_TO_SKILL.get(head)
        if skill is None:
            # Host reasons look like "disk_percent=93.0 >= 85.0"
            token = head.split("=", 1)[0].strip()
            skill = _GAP_TO_SKILL.get(token)
        if skill is None:
            for key, sid in _GAP_TO_SKILL.items():
                if head.startswith(key) or code.startswith(key):
                    skill = sid
                    break
        skill = skill or "product"
        out.setdefault(skill, []).append(code)
    return out


def score_harness(*, gaps: list[str], lesson_text: str) -> float:
    """Higher is better. Penalize open blocking gaps; reward concrete lesson coverage.

    Uncovered gap tokens are heavily penalized so a focused new lesson that names the
    failing gap can beat a long baseline that never mentions it (strict-improvement gate).
    """
    blocking = [g for g in gaps if is_blocking_delivery_gap_code(g.split(":", 1)[-1])]
    score = 100.0 - 12.0 * len(blocking) - 4.0 * max(0, len(gaps) - len(blocking))
    lower = (lesson_text or "").lower()
    for gap in gaps:
        token = gap.split(":", 1)[-1].lower()
        if not token:
            continue
        if token in lower:
            score += 14.0
        else:
            score -= 20.0
    for needle in (
        "font-family",
        "max-width",
        "relative",
        "detail",
        "hover",
        "今日必读",
        "refresh",
        "stylesheet",
        "<main>",
        "href",
        "preview-venv",
        "soft-pause",
        "allowlisted",
        "HOST_RESOURCE",
        "prune",
        "reap",
        "/api/countries",
        "/api/crosswalks",
        "preview-content-depth",
        "content depth",
        "statute",
        "source",
        "trigger",
        "evidence",
        ">=10",
        "≥10",
        "points",
        "steps",
    ):
        if needle.lower() in lower:
            score += 2.5
    return round(score, 2)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _validate_lesson(proposal: HarnessLessonProposal, gaps: list[str]) -> str | None:
    text = proposal.lesson_markdown.strip()
    if len(text) < _MIN_LESSON_CHARS:
        return "lesson too short"
    if len(text) > _MAX_LESSON_CHARS:
        return "lesson too long"
    for pat in _FORBIDDEN_LESSON_PATTERNS:
        if pat.search(text):
            return f"forbidden governance weakening: {pat.pattern}"
    addressed = {str(x).strip() for x in proposal.addressed_gaps if str(x).strip()}
    if not addressed:
        return "no addressed gaps"
    gap_heads = {g.split(":", 1)[-1] for g in gaps}
    if not any(
        a in gaps or a in gap_heads or a.split(":", 1)[-1] in gap_heads for a in addressed
    ):
        return "addressed_gaps do not intersect failure gaps"
    # Require at least one concrete imperative.
    if not re.search(r"(must|MUST|禁止|必须|require|fail)", text):
        return "lesson lacks enforceable must/forbid language"
    return None


class HarnessEvolutionService:
    """Closed-loop skill-harness optimizer (PenguinHarness agent-optimization analogue)."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        workspace_root: Path,
        skills_root: Path | None = None,
    ) -> None:
        self._provider = provider
        self._workspace_root = Path(workspace_root)
        self._skills_root = skills_root or SKILLS_ROOT

    async def evolve_from_gaps(
        self,
        *,
        gaps: list[str],
        actor: str,
        goal_context: str = "",
        preview_url: str | None = None,
        preferred_skill_id: str | None = None,
    ) -> HarnessEvolutionReceipt:
        run_id = uuid.uuid4()
        clean_gaps = [str(g).strip() for g in gaps if str(g).strip()]
        if not clean_gaps:
            return HarnessEvolutionReceipt(
                id=run_id,
                status="NOOP",
                skill_id="",
                baseline_hash="",
                candidate_hash=None,
                baseline_score=0.0,
                candidate_score=0.0,
                gaps=[],
                lesson_path=None,
                snapshot_path=None,
                reason="no gaps to evolve from",
            )

        by_skill = map_gaps_to_skills(clean_gaps)
        if preferred_skill_id and preferred_skill_id in by_skill:
            skill_id = preferred_skill_id
            skill_gaps = by_skill[skill_id]
        else:
            # Prefer UI/product for surface failures.
            skill_id = next(
                (s for s in ("ui", "product", "http-api", "runtime-contract") if s in by_skill),
                next(iter(by_skill)),
            )
            skill_gaps = by_skill[skill_id]

        baseline_lesson = read_lessons(self._workspace_root, skill_id)
        baseline_hash = _hash_text(baseline_lesson)
        baseline_score = score_harness(gaps=skill_gaps, lesson_text=baseline_lesson)

        try:
            guidance = load_skill_manifest(skill_id, root=self._skills_root).guidance
        except FileNotFoundError:
            guidance = ""

        generated = await self._provider.generate_structured(
            system_prompt=(
                "You are the Optimizer in a PenguinHarness-style self-evolution loop for Regent.\n"
                "Edit ONLY the LESSONS overlay for one skill pack (PM / Tech / UX harness), "
                "not model weights and not governance gates.\n"
                "Produce durable, enforceable lessons the generation Agent must follow next time.\n"
                "Roles: PM=acceptance/IA/ops actions; Tech=routes/preview paths/API; "
                "UX=visual design, typography, clickable detail journey.\n"
                "Do NOT propose disabling QA, soft-passing without evidence, or weakening gates.\n"
                "Return complete LESSONS.md markdown (can replace prior lessons), addressed_gaps, "
                "rationale, and role."
            ),
            user_prompt=str(
                {
                    "skill_id": skill_id,
                    "gaps": skill_gaps,
                    "all_gaps": clean_gaps,
                    "goal_context": (goal_context or "")[:4000],
                    "preview_url": preview_url,
                    "baseline_lessons": baseline_lesson[:3000],
                    "current_guidance_excerpt": (guidance or "")[:2500],
                    "actor": actor,
                }
            ),
            response_model=HarnessLessonProposal,
        )
        proposal = generated.output
        if proposal.skill_id.strip() != skill_id:
            proposal = proposal.model_copy(update={"skill_id": skill_id})

        reject = _validate_lesson(proposal, skill_gaps)
        if reject:
            return HarnessEvolutionReceipt(
                id=run_id,
                status="REJECTED",
                skill_id=skill_id,
                baseline_hash=baseline_hash,
                candidate_hash=_hash_text(proposal.lesson_markdown),
                baseline_score=baseline_score,
                candidate_score=baseline_score,
                gaps=skill_gaps,
                lesson_path=None,
                snapshot_path=None,
                reason=reject,
                role=proposal.role,
                evidence={"addressed_gaps": proposal.addressed_gaps},
            )

        candidate_score = score_harness(
            gaps=skill_gaps, lesson_text=proposal.lesson_markdown
        )
        lower_cand = (proposal.lesson_markdown or "").lower()
        gap_tokens = {
            g.split(":", 1)[-1].lower() for g in skill_gaps if str(g).strip()
        }
        addressed_tokens = {
            str(x).split(":", 1)[-1].lower()
            for x in (proposal.addressed_gaps or [])
            if str(x).strip()
        }
        covers_all = bool(gap_tokens) and (
            gap_tokens <= addressed_tokens
            or all(token in lower_cand for token in gap_tokens)
        )
        # Strict improvement (PenguinHarness): keep if score rises.
        # Empty baseline: allow first lesson that covers every gap (bootstrap).
        empty_baseline = not (baseline_lesson or "").strip()
        improved = candidate_score > baseline_score or (
            empty_baseline and covers_all and candidate_score >= baseline_score
        )
        if not improved:
            return HarnessEvolutionReceipt(
                id=run_id,
                status="REJECTED",
                skill_id=skill_id,
                baseline_hash=baseline_hash,
                candidate_hash=_hash_text(proposal.lesson_markdown),
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                gaps=skill_gaps,
                lesson_path=None,
                snapshot_path=None,
                reason=(
                    f"no strict improvement ({candidate_score} <= {baseline_score}); rolled back"
                ),
                role=proposal.role,
                evidence={
                    "addressed_gaps": proposal.addressed_gaps,
                    "rationale": proposal.rationale,
                },
            )

        target = lesson_file(self._workspace_root, skill_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path = None
        if target.is_file():
            snap_dir = (
                lessons_root(self._workspace_root)
                / skill_id
                / "snapshots"
            )
            snap_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snap_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{baseline_hash[:12]}.md"
            snapshot_path.write_text(baseline_lesson, encoding="utf-8")

        header = (
            f"<!-- harness-evolution run={run_id} role={proposal.role} "
            f"score={candidate_score} gaps={','.join(skill_gaps[:8])} -->\n"
        )
        target.write_text(header + proposal.lesson_markdown.strip() + "\n", encoding="utf-8")
        # Persist receipt sidecar for Trace observability.
        receipt_path = target.parent / "LAST_EVOLUTION.json"
        receipt = HarnessEvolutionReceipt(
            id=run_id,
            status="ACCEPTED",
            skill_id=skill_id,
            baseline_hash=baseline_hash,
            candidate_hash=_hash_text(proposal.lesson_markdown),
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            gaps=skill_gaps,
            lesson_path=str(target),
            snapshot_path=str(snapshot_path) if snapshot_path else None,
            reason="strict improvement accepted",
            role=proposal.role,
            evidence={
                "addressed_gaps": proposal.addressed_gaps,
                "rationale": proposal.rationale,
                "actor": actor,
            },
        )
        receipt_path.write_text(
            json.dumps(receipt.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return receipt
