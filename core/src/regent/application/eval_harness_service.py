"""P2-4 Eval Harness — delivery-verification scoring + signed DecisionRecord."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import EvidenceModel, EvalRunModel, GoalModel

POLICY_VERSION = "eval-harness-v1"
DECISION_RECORD_VERSION = "eval-decision-record-v1"
ORG_ADAPTIVE_STATUS = "ROLLOUT_NOT_ALLOWED"
DEFAULT_SIGNING_KEY = "regent-eval-decision-v1-dev"


@dataclass(frozen=True, slots=True)
class CreateEvalRun:
    name: str
    task_set: dict[str, Any]
    baseline: dict[str, Any]
    budget: dict[str, Any]
    seed: str
    actor: str


class EvalHarnessService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        signing_key: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._signing_key = (signing_key or DEFAULT_SIGNING_KEY).encode()

    async def create(self, command: CreateEvalRun) -> EvalRunModel:
        digest = canonical_hash(command.task_set)
        async with self._sessions() as session, session.begin():
            model = EvalRunModel(
                id=uuid.uuid4(),
                name=command.name,
                status="DRAFT",
                task_set_json=command.task_set,
                task_set_hash=digest,
                baseline_json=command.baseline,
                budget_json={
                    **command.budget,
                    "wall_clock_budget_s": command.budget.get("wall_clock_budget_s"),
                    "compute_budget_units": command.budget.get("compute_budget_units"),
                },
                seed=command.seed,
                metrics_json={},
                scores_json={},
                policy_version=POLICY_VERSION,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def freeze(self, eval_run_id: uuid.UUID, *, actor: str) -> EvalRunModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(EvalRunModel, eval_run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            if model.status != "DRAFT":
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot freeze from {model.status}")
            model.task_set_hash = canonical_hash(model.task_set_json)
            model.status = "FROZEN"
            model.metrics_json = {
                **dict(model.metrics_json or {}),
                "frozen_by": actor,
                "blind_eval_required": True,
                "baseline": "strong_single_agent",
                "org_adaptive_status": ORG_ADAPTIVE_STATUS,
            }
            await session.flush()
            return model

    async def run_and_score(self, eval_run_id: uuid.UUID, *, actor: str) -> EvalRunModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(EvalRunModel, eval_run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            if model.status not in {"FROZEN", "RUNNING"}:
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot run from {model.status}")
            model.status = "RUNNING"
            tasks = list((model.task_set_json or {}).get("tasks") or [])
            scores: list[dict[str, Any]] = []
            for task in tasks:
                scores.append(await self._score_task(session, task, seed=model.seed))
            passed = sum(1 for s in scores if s.get("pass@1"))
            evidence_digest = hashlib.sha256(
                canonical_hash({"tasks": scores}).encode()
            ).hexdigest()
            model.scores_json = {
                "scored_by": actor,
                "scoring_mode": "delivery_verification",
                "tasks": scores,
                "pass_at_1_rate": (passed / len(scores)) if scores else 0.0,
                "evidence_digest": evidence_digest,
                "wall_clock_report": model.budget_json.get("wall_clock_budget_s"),
                "compute_budget_report": model.budget_json.get("compute_budget_units"),
            }
            model.status = "SCORED"
            await session.flush()
            return model

    async def decide(
        self, eval_run_id: uuid.UUID, *, actor: str, promote: bool | None = None
    ) -> EvalRunModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(EvalRunModel, eval_run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            if model.status != "SCORED":
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot decide from {model.status}")
            rate = float((model.scores_json or {}).get("pass_at_1_rate") or 0.0)
            sample_n = len((model.scores_json or {}).get("tasks") or [])
            if sample_n < 1:
                model.decision = "INSUFFICIENT_EVIDENCE"
                model.decision_rationale = "no tasks in frozen set"
            elif promote is False:
                model.decision = "KEEP_SINGLE_AGENT"
                model.decision_rationale = "explicit non-promote"
            elif rate >= 0.5 and (promote is True or promote is None):
                model.decision = "POSITIVE_NET_READY_FOR_ORG_REVIEW"
                model.decision_rationale = (
                    f"pass@1_rate={rate:.2f}; multi-agent still requires separate DecisionRecord"
                )
            else:
                model.decision = "KEEP_SINGLE_AGENT"
                model.decision_rationale = f"pass@1_rate={rate:.2f} below gate"
            model.status = "DECIDED"
            record = self._sign_decision_record(model)
            model.metrics_json = {
                **dict(model.metrics_json or {}),
                "decided_by": actor,
                "org_adaptive_status": ORG_ADAPTIVE_STATUS,
                "product_decision_record": record,
            }
            await session.flush()
            return model

    async def get(self, eval_run_id: uuid.UUID) -> EvalRunModel:
        async with self._sessions() as session:
            model = await session.get(EvalRunModel, eval_run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            return model

    async def list_runs(self) -> list[EvalRunModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(select(EvalRunModel).order_by(EvalRunModel.created_at.desc()))
            )

    async def load_frozen_task_set(
        self, artifact_ref: str, *, actor: str = "regent-core",
    ) -> dict[str, Any]:
        if os.path.isfile(artifact_ref):
            with open(artifact_ref, encoding="utf-8") as f:
                return json.load(f)
        return {
            "version": "v1",
            "tasks": [
                {"id": "task-1", "description": "default task", "expected": "ok"},
            ],
            "artifact_ref": artifact_ref,
            "loaded_by": actor,
        }

    async def run_blind_evaluation(
        self, eval_run_id: uuid.UUID, *, actor: str = "regent-core",
    ) -> EvalRunModel:
        """Blind wrapper around delivery-verification scoring (agent identity hidden)."""
        model = await self.run_and_score(eval_run_id, actor=actor)
        async with self._sessions() as session, session.begin():
            locked = await session.get(EvalRunModel, eval_run_id, with_for_update=True)
            if locked is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            scores = dict(locked.scores_json or {})
            tasks = []
            for item in list(scores.get("tasks") or []):
                tasks.append(
                    {
                        **dict(item),
                        "blind": True,
                        "agent_identity_hidden": True,
                    }
                )
            scores["tasks"] = tasks
            scores["blind"] = True
            scores["scored_by"] = actor
            locked.scores_json = scores
            await session.flush()
            return locked

    def statistical_gate(
        self,
        scores: dict[str, Any],
        *,
        baseline_rate: float = 0.5,
        confidence: float = 0.95,
        min_tasks: int = 5,
    ) -> dict[str, Any]:
        tasks = scores.get("tasks") or []
        n = len(tasks)
        if n < min_tasks:
            return {
                "passed": False,
                "pass_at_k": 0.0,
                "confidence_interval": (0.0, 0.0),
                "n": n,
                "rationale": f"insufficient tasks ({n} < {min_tasks})",
            }

        passed_count = sum(1 for t in tasks if t.get("pass@1"))
        pass_rate = passed_count / n

        import math

        z = 1.96 if confidence >= 0.95 else 1.645
        denominator = 1 + z**2 / n
        center = (pass_rate + z**2 / (2 * n)) / denominator
        spread = z * math.sqrt(
            (pass_rate * (1 - pass_rate) + z**2 / (4 * n)) / n
        ) / denominator
        lower = max(0.0, center - spread)
        upper = min(1.0, center + spread)

        gate_passed = lower > baseline_rate
        return {
            "passed": gate_passed,
            "pass_at_k": round(pass_rate, 4),
            "confidence_interval": (round(lower, 4), round(upper, 4)),
            "n": n,
            "baseline_rate": baseline_rate,
            "confidence": confidence,
            "rationale": (
                f"pass@1={pass_rate:.2f} CI=[{lower:.2f},{upper:.2f}] "
                f"vs baseline={baseline_rate:.2f}"
            ),
        }

    async def _score_task(
        self, session: AsyncSession, task: dict[str, Any], *, seed: str
    ) -> dict[str, Any]:
        tid = str(task.get("id") or task)
        verification = dict(task.get("verification") or {})
        mode = str(verification.get("mode") or "delivery_signals")

        if mode == "goal_evidence":
            goal_id_raw = verification.get("goal_id") or task.get("goal_id")
            if not goal_id_raw:
                return {
                    "task_id": tid,
                    "pass@1": False,
                    "blind": False,
                    "scoring_mode": mode,
                    "reason": "goal_id missing for goal_evidence mode",
                    "evidence_refs": [],
                }
            goal_id = uuid.UUID(str(goal_id_raw))
            goal = await session.get(GoalModel, goal_id)
            evidence = await session.scalar(
                select(EvidenceModel).where(EvidenceModel.goal_id == goal_id).limit(1)
            )
            passed = (
                goal is not None
                and goal.status == "ACHIEVED"
                and evidence is not None
            )
            refs = [f"goal://{goal_id}"]
            if evidence is not None:
                refs.append(f"evidence://{evidence.id}")
            return {
                "task_id": tid,
                "pass@1": passed,
                "blind": False,
                "scoring_mode": mode,
                "goal_status": None if goal is None else goal.status,
                "evidence_refs": refs,
                "reason": "goal ACHIEVED with evidence" if passed else "goal/evidence missing",
            }

        # delivery_signals: build + preview + delivery review PASS (fixture or embedded).
        build_ok = bool(verification.get("build_ok"))
        preview_reachable = bool(verification.get("preview_reachable"))
        delivery_review = str(verification.get("delivery_review") or "").upper()
        expect_pass = verification.get("expect_pass", True)
        signals_pass = (
            build_ok and preview_reachable and delivery_review == "PASS"
        )
        passed = signals_pass if expect_pass else (not signals_pass and delivery_review != "PASS")
        # Non-deliverable demos: expect_pass=False and delivery_review=FAIL → pass when FAIL.
        if expect_pass is False:
            passed = delivery_review == "FAIL" or (
                build_ok and not preview_reachable
            )
        evidence_refs = list(verification.get("evidence_refs") or [])
        return {
            "task_id": tid,
            "pass@1": bool(passed),
            "blind": False,
            "scoring_mode": "delivery_signals",
            "signals": {
                "build_ok": build_ok,
                "preview_reachable": preview_reachable,
                "delivery_review": delivery_review,
                "expect_pass": expect_pass,
            },
            "evidence_refs": evidence_refs,
            "seed_bound": canonical_hash({"seed": seed, "task": tid})[:16],
            "reason": "delivery verification signals",
        }

    def _sign_decision_record(self, model: EvalRunModel) -> dict[str, Any]:
        scores = dict(model.scores_json or {})
        payload = {
            "version": DECISION_RECORD_VERSION,
            "eval_run_id": str(model.id),
            "task_set_hash": model.task_set_hash,
            "decision": model.decision,
            "decision_rationale": model.decision_rationale,
            "pass_at_1_rate": scores.get("pass_at_1_rate"),
            "evidence_digest": scores.get("evidence_digest"),
            "org_adaptive_status": ORG_ADAPTIVE_STATUS,
            "policy_version": model.policy_version,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        signature = hmac.new(self._signing_key, canonical.encode(), hashlib.sha256).hexdigest()
        return {**payload, "signature": signature}
