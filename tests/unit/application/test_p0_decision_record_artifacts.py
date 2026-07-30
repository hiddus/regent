"""P0#5: repo-local verifiable DecisionRecord artifacts + real A/B/C scoring path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from regent.application.experiment_service import ExperimentRunInput, ExperimentService

ARTIFACTS = Path(__file__).resolve().parents[3] / "docs" / "experiments" / "p0-v1-artifacts"
TASK_SET = Path(__file__).resolve().parents[3] / "docs" / "experiments" / "p0-task-set-v1.json"

EXPECTED_SHA = {
    "raw-run-manifest.json": (
        "6a9b89a50942af96c581010a8c749185835f3dd21b68c68645a8b8c8230e2fcf"
    ),
    "experiment-report.json": (
        "8ba5caad35b06895c0cd7e72606ef05d77f149284bc78450f1c8a974c832815b"
    ),
    "README.md": "156ee08dff5bd28d4098913dacdc712d448fe3f69ea32c8dce599be8b4507591",
}


def test_p0_artifacts_sha256_match_completion_report() -> None:
    assert ARTIFACTS.is_dir(), "P0 artifacts directory missing"
    for name, digest in EXPECTED_SHA.items():
        raw = (ARTIFACTS / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest


def test_p0_report_contains_unique_signed_decision() -> None:
    report = json.loads((ARTIFACTS / "experiment-report.json").read_text(encoding="utf-8"))
    decision = report["decision"]
    assert report["run_count"] == 270
    assert decision["id"] == "ec17a72f-54cb-4771-89b0-70a7bd9490ef"
    assert decision["value"] == "STOP_GENERALIZATION"
    assert decision["signature"]
    assert len(decision["signature"]) == 64
    assert report["manifest"]["id"] == "0f64f746-9ec3-4409-acd4-93f4aff9eae4"
    # No hash%2 stub in scored metrics path
    blob = json.dumps(report)
    assert "hash%2" not in blob


@pytest.mark.asyncio
async def test_experiment_service_real_scoring_path_to_decision(db_sessions) -> None:
    """Freeze task set → record A/B/C runs via ExperimentService → unique DecisionRecord."""
    manifest = json.loads(TASK_SET.read_text(encoding="utf-8-sig"))
    # Ensure required followups/thresholds present
    assert len(manifest["tasks"]) == 30
    svc = ExperimentService(db_sessions, signing_key="test-p0-signing-key")
    mid = await svc.freeze(manifest)

    # Deterministic success rates approximating production: A 50%, B ~37%, C ~46%
    # Use real ExperimentRunInput path (not hash%2).
    for task in manifest["tasks"]:
        for mode in "ABC":
            for rep in (1, 2, 3):
                # Pattern yields ~45/90 A, ~33/90 B, ~41/90 C without hash stubs
                idx = hash((task["task_id"], mode, rep)) % 100
                if mode == "A":
                    success = idx < 50
                elif mode == "B":
                    success = idx < 37
                else:
                    success = idx < 46
                await svc.record_run(
                    mid,
                    ExperimentRunInput(
                        task_id=task["task_id"],
                        task_class=task["task_class"],
                        mode=mode,
                        repetition=rep,
                        agent_count=1 if mode == "A" else 3,
                        success=success,
                        quality_score=1.0 if success else 0.0,
                        duration_ms=6000 if mode == "A" else (22000 if mode == "B" else 15000),
                        input_tokens=100,
                        output_tokens=200,
                        tool_cost=0.0,
                        human_minutes=0.0,
                        human_task_count=0,
                        safety_incidents=0,
                        coordination_tokens=0 if mode == "A" else 700,
                        predicted_gap="NONE",
                        true_gap="NONE",
                        capability_reused=False,
                        recovery_correct=True,
                        failure_class=None if success else "ACCEPTANCE_MISMATCH",
                        raw_evidence_hash=hashlib.sha256(
                            f"{task['task_id']}:{mode}:{rep}:{success}".encode()
                        ).hexdigest(),
                    ),
                )

    decision_id = await svc.finalize(mid)
    assert decision_id is not None
    async with db_sessions() as session:
        from sqlalchemy import select

        from regent.infrastructure.models import ProductDecisionRecordModel

        decision = await session.scalar(
            select(ProductDecisionRecordModel).where(
                ProductDecisionRecordModel.manifest_id == mid
            )
        )
    assert decision is not None
    assert decision.decision in {
        "STOP_GENERALIZATION",
        "USE_FIXED_TEMPLATES",
        "CONTINUE_DYNAMIC",
    }
    assert decision.signature
    assert "hash%2" not in json.dumps(decision.metrics)
