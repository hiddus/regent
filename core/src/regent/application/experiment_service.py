import hashlib
import hmac
import json
import math
import statistics
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    ExperimentManifestModel,
    ExperimentRunModel,
    ProductDecisionRecordModel,
)


@dataclass(frozen=True, slots=True)
class ExperimentRunInput:
    task_id: str
    task_class: str
    mode: str
    repetition: int
    agent_count: int
    success: bool
    quality_score: float
    duration_ms: int
    input_tokens: int
    output_tokens: int
    tool_cost: float
    human_minutes: float
    human_task_count: int
    safety_incidents: int
    coordination_tokens: int
    predicted_gap: str
    true_gap: str
    capability_reused: bool
    recovery_correct: bool
    failure_class: str | None
    raw_evidence_hash: str


class ExperimentService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], signing_key: str) -> None:
        if not signing_key:
            raise ValueError("experiment signing key is required")
        self._sessions = sessions
        self._key = signing_key.encode()

    async def freeze(self, manifest: dict[str, Any]) -> uuid.UUID:
        self._validate_manifest(manifest)
        canonical = self._canonical(manifest)
        digest = hashlib.sha256(canonical).hexdigest()
        signature = hmac.new(self._key, canonical, hashlib.sha256).hexdigest()
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ExperimentManifestModel).where(
                    ExperimentManifestModel.name == manifest["name"],
                    ExperimentManifestModel.version == manifest["version"],
                )
            )
            if existing is not None:
                if existing.digest != digest:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "frozen manifest cannot change")
                return existing.id
            manifest_id = uuid.uuid4()
            session.add(
                ExperimentManifestModel(
                    id=manifest_id,
                    name=manifest["name"],
                    version=manifest["version"],
                    status="FROZEN",
                    manifest=manifest,
                    digest=digest,
                    signature=signature,
                )
            )
            return manifest_id

    async def record_run(self, manifest_id: uuid.UUID, item: ExperimentRunInput) -> uuid.UUID:
        if item.mode not in {"A", "B", "C"} or item.repetition not in {1, 2, 3}:
            raise ValueError("invalid experiment cell")
        if not 1 <= item.agent_count <= 4:
            raise ValueError("agent count exceeds P0 bound")
        async with self._sessions() as session, session.begin():
            manifest = await session.get(ExperimentManifestModel, manifest_id)
            if manifest is None or manifest.status != "FROZEN":
                raise DomainError(ErrorCode.INVALID_STATE, "manifest is not open for runs")
            task_map = {task["task_id"]: task for task in manifest.manifest["tasks"]}
            task = task_map.get(item.task_id)
            if task is None or task["task_class"] != item.task_class:
                raise DomainError(ErrorCode.POLICY_DENIED, "run does not match frozen task")
            existing = await session.scalar(
                select(ExperimentRunModel).where(
                    ExperimentRunModel.manifest_id == manifest_id,
                    ExperimentRunModel.task_id == item.task_id,
                    ExperimentRunModel.mode == item.mode,
                    ExperimentRunModel.repetition == item.repetition,
                )
            )
            if existing is not None:
                return existing.id
            run_id = uuid.uuid4()
            session.add(ExperimentRunModel(id=run_id, manifest_id=manifest_id, **asdict(item)))
            return run_id

    async def finalize(self, manifest_id: uuid.UUID) -> uuid.UUID:
        async with self._sessions() as session, session.begin():
            manifest = await session.get(ExperimentManifestModel, manifest_id, with_for_update=True)
            if manifest is None:
                raise DomainError(ErrorCode.NOT_FOUND, "manifest not found")
            existing = await session.scalar(
                select(ProductDecisionRecordModel).where(
                    ProductDecisionRecordModel.manifest_id == manifest_id
                )
            )
            if existing is not None:
                return existing.id
            runs = list(
                await session.scalars(
                    select(ExperimentRunModel).where(ExperimentRunModel.manifest_id == manifest_id)
                )
            )
            expected = len(manifest.manifest["tasks"]) * 3 * int(manifest.manifest["repetitions"])
            if len(runs) != expected:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"experiment requires {expected} runs; found {len(runs)}",
                )
            metrics = {
                mode: self._mode_metrics([run for run in runs if run.mode == mode])
                for mode in "ABC"
            }
            decision, rationale = self._decide(metrics, manifest.manifest["thresholds"])
            evidence_digest = hashlib.sha256(
                "".join(sorted(run.raw_evidence_hash for run in runs)).encode()
            ).hexdigest()
            decision_payload = {
                "manifest_digest": manifest.digest,
                "decision": decision,
                "metrics": metrics,
                "evidence_digest": evidence_digest,
            }
            signature = hmac.new(
                self._key, self._canonical(decision_payload), hashlib.sha256
            ).hexdigest()
            decision_id = uuid.uuid4()
            session.add(
                ProductDecisionRecordModel(
                    id=decision_id,
                    manifest_id=manifest_id,
                    decision=decision,
                    rationale=rationale,
                    metrics=metrics,
                    evidence_digest=evidence_digest,
                    signature=signature,
                )
            )
            manifest.status = "COMPLETED"
            return decision_id

    @staticmethod
    def _mode_metrics(runs: list[ExperimentRunModel]) -> dict[str, Any]:
        successes = sum(run.success for run in runs)
        rate = successes / len(runs)
        z = 1.96
        denominator = 1 + z * z / len(runs)
        centre = (rate + z * z / (2 * len(runs))) / denominator
        margin = (
            z * math.sqrt((rate * (1 - rate) + z * z / (4 * len(runs))) / len(runs)) / denominator
        )
        durations = [run.duration_ms for run in runs]
        qualities = [run.quality_score for run in runs]
        return {
            "run_count": len(runs),
            "success_rate": rate,
            "success_ci95": [max(0.0, centre - margin), min(1.0, centre + margin)],
            "quality_median": statistics.median(qualities),
            "quality_pstdev": statistics.pstdev(qualities),
            "duration_median_ms": statistics.median(durations),
            "duration_pstdev_ms": statistics.pstdev(durations),
            "input_tokens": sum(run.input_tokens for run in runs),
            "output_tokens": sum(run.output_tokens for run in runs),
            "human_minutes": sum(run.human_minutes for run in runs),
            "safety_incidents": sum(run.safety_incidents for run in runs),
            "coordination_tokens": sum(run.coordination_tokens for run in runs),
            "recovery_rate": sum(run.recovery_correct for run in runs) / len(runs),
            "failures": {
                name: sum(run.failure_class == name for run in runs)
                for name in sorted({run.failure_class for run in runs if run.failure_class})
            },
        }

    @staticmethod
    def _decide(metrics: dict[str, dict[str, Any]], thresholds: dict[str, Any]) -> tuple[str, str]:
        a, b, c = metrics["A"], metrics["B"], metrics["C"]
        lift = (c["success_rate"] - a["success_rate"]) * 100
        time_reduction = (
            100
            * (a["duration_median_ms"] - c["duration_median_ms"])
            / max(1, a["duration_median_ms"])
        )
        safe = c["safety_incidents"] == 0
        if safe and (
            lift >= thresholds["c_success_lift_points"]
            or (
                lift >= -thresholds["c_max_success_drop_points"]
                and time_reduction >= thresholds["c_time_reduction_percent"]
            )
        ):
            return (
                "CONTINUE_DYNAMIC",
                "Dynamic organization passed the frozen success/time gate "
                "without a severe safety incident.",
            )
        close = (
            abs((b["success_rate"] - c["success_rate"]) * 100)
            <= thresholds["fixed_dynamic_close_points"]
        )
        if close and (
            b["duration_median_ms"] < c["duration_median_ms"]
            or b["coordination_tokens"] < c["coordination_tokens"]
        ):
            return (
                "USE_FIXED_TEMPLATES",
                "Fixed and dynamic success were close while the fixed template "
                "had lower measured overhead.",
            )
        return (
            "STOP_GENERALIZATION",
            "Dynamic organization did not pass the frozen net-benefit gates in this round.",
        )

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any]) -> None:
        tasks = manifest.get("tasks", [])
        followups = manifest.get("followup_tasks", [])
        classes = [task.get("task_class") for task in tasks]
        if len(tasks) != 30 or len(followups) < 10:
            raise ValueError("P0 manifest requires 30 main and at least 10 follow-up tasks")
        if any(
            classes.count(name) != 10
            for name in ("CAPABILITY_SUFFICIENT", "CAPABILITY_COMPOSITION", "TOOL_GAP")
        ):
            raise ValueError("P0 task classes must contain ten tasks each")
        if manifest.get("modes") != ["A", "B", "C"] or manifest.get("repetitions") != 3:
            raise ValueError("P0 requires A/B/C with three repetitions")
        if any(not task.get("hidden_test_digest") for task in tasks):
            raise ValueError("every task requires a frozen hidden-test digest")

    @staticmethod
    def _canonical(value: dict[str, Any]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
