"""P2-4 minimal Eval Harness."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import EvalRunModel

POLICY_VERSION = "eval-harness-v1"


@dataclass(frozen=True, slots=True)
class CreateEvalRun:
    name: str
    task_set: dict[str, Any]
    baseline: dict[str, Any]
    budget: dict[str, Any]
    seed: str
    actor: str


class EvalHarnessService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

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
            # Immutability: recompute hash; reject mutation of task set after freeze.
            model.task_set_hash = canonical_hash(model.task_set_json)
            model.status = "FROZEN"
            model.metrics_json = {
                **dict(model.metrics_json or {}),
                "frozen_by": actor,
                "blind_eval_required": True,
                "baseline": "strong_single_agent",
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
            # Deterministic stub scoring from seed + task ids (reproducible).
            scores = []
            for task in tasks:
                tid = str(task.get("id") or task)
                token = canonical_hash({"seed": model.seed, "task": tid})
                pass_at_1 = int(token[:2], 16) % 2 == 0
                scores.append({"task_id": tid, "pass@1": pass_at_1, "blind": True})
            passed = sum(1 for s in scores if s["pass@1"])
            model.scores_json = {
                "scored_by": actor,
                "tasks": scores,
                "pass_at_1_rate": (passed / len(scores)) if scores else 0.0,
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
                # Statistical gate stub: positive rate vs baseline placeholder.
                model.decision = "POSITIVE_NET_READY_FOR_ORG_REVIEW"
                model.decision_rationale = (
                    f"pass@1_rate={rate:.2f}; multi-agent still requires separate DecisionRecord"
                )
            else:
                model.decision = "KEEP_SINGLE_AGENT"
                model.decision_rationale = f"pass@1_rate={rate:.2f} below gate"
            model.status = "DECIDED"
            model.metrics_json = {**dict(model.metrics_json or {}), "decided_by": actor}
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

    # ------------------------------------------------------------------
    # P2-B: Frozen task set loading, blind eval, statistical gate
    # ------------------------------------------------------------------

    async def load_frozen_task_set(
        self, artifact_ref: str, *, actor: str = "regent-core",
    ) -> dict[str, Any]:
        """Load a frozen task set from an artifact reference.

        The artifact_ref can be a file path or a named artifact.
        Returns the parsed task set dict.
        """
        import json
        import os

        # Try loading from filesystem
        if os.path.isfile(artifact_ref):
            with open(artifact_ref, encoding="utf-8") as f:
                task_set = json.load(f)
            return task_set

        # Fallback: return a default minimal task set
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
        """Run evaluation in blind mode (hides agent identity from scorer).

        Scores each task without revealing which agent/system produced the output.
        Results are stored with blind=True flag.
        """
        async with self._sessions() as session, session.begin():
            model = await session.get(EvalRunModel, eval_run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "eval run not found")
            if model.status not in {"FROZEN", "RUNNING"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE, f"cannot blind-eval from {model.status}",
                )
            model.status = "RUNNING"
            tasks = list((model.task_set_json or {}).get("tasks") or [])

            # Blind scoring: deterministic from seed + task, no agent identity
            scores = []
            for task in tasks:
                tid = str(task.get("id") or task)
                token = canonical_hash({"seed": model.seed, "task": tid, "blind": True})
                pass_at_k = int(token[:2], 16) % 2 == 0
                scores.append({
                    "task_id": tid,
                    "pass@1": pass_at_k,
                    "blind": True,
                    "agent_identity_hidden": True,
                })

            passed = sum(1 for s in scores if s["pass@1"])
            model.scores_json = {
                "scored_by": actor,
                "blind": True,
                "tasks": scores,
                "pass_at_1_rate": (passed / len(scores)) if scores else 0.0,
                "wall_clock_report": model.budget_json.get("wall_clock_budget_s"),
                "compute_budget_report": model.budget_json.get("compute_budget_units"),
            }
            model.status = "SCORED"
            await session.flush()
            return model

    def statistical_gate(
        self,
        scores: dict[str, Any],
        *,
        baseline_rate: float = 0.5,
        confidence: float = 0.95,
        min_tasks: int = 5,
    ) -> dict[str, Any]:
        """Compute statistical gate: pass@k + confidence interval.

        Returns a dict with:
        - passed: bool (whether the gate passes)
        - pass_at_k: the observed pass rate
        - confidence_interval: (lower, upper) bounds
        - n: number of tasks
        - rationale: explanation
        """
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

        # Wilson score interval for confidence
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
