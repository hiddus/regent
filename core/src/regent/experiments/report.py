import argparse
import asyncio
import hashlib
import hmac
import json
import uuid
from pathlib import Path

from sqlalchemy import select

from regent.config import get_settings
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.models import (
    ExperimentManifestModel,
    ExperimentRunModel,
    ProductDecisionRecordModel,
)


async def generate(manifest_id: uuid.UUID, output_dir: Path) -> None:
    settings = get_settings()
    if settings.experiment_signing_key is None:
        raise RuntimeError("experiment signing key is unavailable")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        manifest = await session.get(ExperimentManifestModel, manifest_id)
        decision = await session.scalar(
            select(ProductDecisionRecordModel).where(
                ProductDecisionRecordModel.manifest_id == manifest_id
            )
        )
        runs = list(
            await session.scalars(
                select(ExperimentRunModel)
                .where(ExperimentRunModel.manifest_id == manifest_id)
                .order_by(
                    ExperimentRunModel.task_id,
                    ExperimentRunModel.mode,
                    ExperimentRunModel.repetition,
                )
            )
        )
    if manifest is None or decision is None or len(runs) != 270:
        raise RuntimeError("completed experiment and unique decision are required")
    rows = [
        {
            "run_id": str(run.id),
            "task_id": run.task_id,
            "task_class": run.task_class,
            "mode": run.mode,
            "repetition": run.repetition,
            "agent_count": run.agent_count,
            "success": run.success,
            "quality_score": run.quality_score,
            "duration_ms": run.duration_ms,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "human_minutes": run.human_minutes,
            "safety_incidents": run.safety_incidents,
            "coordination_tokens": run.coordination_tokens,
            "predicted_gap": run.predicted_gap,
            "true_gap": run.true_gap,
            "capability_reused": run.capability_reused,
            "recovery_correct": run.recovery_correct,
            "failure_class": run.failure_class,
            "raw_evidence_hash": run.raw_evidence_hash,
        }
        for run in runs
    ]
    raw = {
        "manifest_id": str(manifest.id),
        "manifest_digest": manifest.digest,
        "manifest_signature": manifest.signature,
        "runs": rows,
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    raw["run_manifest_signature"] = hmac.new(
        settings.experiment_signing_key.get_secret_value().encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    confusion: dict[str, dict[str, int]] = {}
    for mode in "ABC":
        mode_runs = [run for run in runs if run.mode == mode]

        def truth_gap(value: str) -> bool:
            return value in {"CONFIGURATION", "TOOL"}

        confusion[mode] = {
            "tp": sum(
                truth_gap(run.true_gap) and truth_gap(run.predicted_gap) for run in mode_runs
            ),
            "fp": sum(
                not truth_gap(run.true_gap) and truth_gap(run.predicted_gap) for run in mode_runs
            ),
            "tn": sum(
                not truth_gap(run.true_gap) and not truth_gap(run.predicted_gap)
                for run in mode_runs
            ),
            "fn": sum(
                truth_gap(run.true_gap) and not truth_gap(run.predicted_gap) for run in mode_runs
            ),
        }
    report = {
        "manifest": {"id": str(manifest.id), "digest": manifest.digest},
        "run_count": len(runs),
        "metrics": decision.metrics,
        "gap_confusion": confusion,
        "safety_incidents": sum(run.safety_incidents for run in runs),
        "human_task_count": sum(run.human_task_count for run in runs),
        "rollback_or_recovery_failures": sum(not run.recovery_correct for run in runs),
        "decision": {
            "id": str(decision.id),
            "value": decision.decision,
            "rationale": decision.rationale,
            "evidence_digest": decision.evidence_digest,
            "signature": decision.signature,
        },
        "next_round_change_limit": "At most one primary hypothesis or mechanism may change.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw-run-manifest.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "experiment-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = (
        "# Regent P0 A/B/C Experiment Report\n\n"
        f"- Manifest: `{manifest.id}`\n"
        f"- Signed runs: `{len(runs)}`\n"
        f"- Decision: **{decision.decision}**\n"
        f"- Rationale: {decision.rationale}\n"
        f"- Evidence digest: `{decision.evidence_digest}`\n"
        f"- Decision signature: `{decision.signature}`\n\n"
        "Detailed metrics, confidence intervals, dispersion, failure classes, gap confusion, "
        "safety and human-intervention records are in `experiment-report.json`.\n"
    )
    (output_dir / "README.md").write_text(markdown, encoding="utf-8")
    print(f"REPORT_DIR={output_dir}")
    print(f"RUNS={len(runs)}")
    print(f"DECISION={decision.decision}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-id", type=uuid.UUID, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(generate(args.manifest_id, args.output_dir))


if __name__ == "__main__":
    main()
