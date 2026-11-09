import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from regent.config import get_settings
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.models import (
    ArtifactModel,
    AuditRecordModel,
    CapabilityModel,
    ExperimentManifestModel,
    ExperimentRunModel,
    GoalModel,
    ObservationModel,
    OrganizationModel,
    ProductDecisionRecordModel,
    RunModel,
    SideEffectAttemptModel,
    ToolCertificationModel,
    ToolSpecModel,
    WorkerLeaseModel,
    WorkModel,
)


async def audit() -> dict[str, Any]:
    engine = create_engine(get_settings())
    sessions = create_session_factory(engine)
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    async with sessions() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        checks["migration_head"] = revision == "20260717_0010"
        csv_count = await session.scalar(
            select(func.count())
            .select_from(GoalModel)
            .where(
                GoalModel.metadata_json["baseline"].as_string() == "CSV_SUMMARY_BASELINE",
                GoalModel.status == "ACHIEVED",
            )
        )
        checks["csv_baseline"] = bool(csv_count)
        evt_count = await session.scalar(
            select(func.count())
            .select_from(ToolCertificationModel)
            .join(ToolSpecModel, ToolSpecModel.id == ToolCertificationModel.tool_spec_id)
            .where(
                ToolSpecModel.name == "evt-summary",
                ToolSpecModel.status == "CERTIFIED",
                ToolCertificationModel.public_passed.is_(True),
                ToolCertificationModel.hidden_passed.is_(True),
            )
        )
        checks["evt_certified"] = bool(evt_count)
        organizations = list(await session.scalars(select(OrganizationModel)))
        checks["organization_bound"] = bool(organizations) and all(
            item.max_agents <= 4 for item in organizations
        )
        checks["capability_gap_recorded"] = bool(
            await session.scalar(
                select(func.count())
                .select_from(CapabilityModel)
                .where(CapabilityModel.status.in_(("CANDIDATE", "GOAL_CERTIFIED", "REVOKED")))
            )
        )
        checks["unknown_reconciled"] = bool(
            await session.scalar(
                select(func.count())
                .select_from(SideEffectAttemptModel)
                .where(SideEffectAttemptModel.status == "RECONCILED")
            )
        )
        artifact_total = await session.scalar(select(func.count()).select_from(ArtifactModel)) or 0
        artifact_hashed = (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactModel)
                .where(func.length(ArtifactModel.content_hash) == 64)
            )
            or 0
        )
        checks["artifact_hash_coverage"] = artifact_total > 0 and artifact_total == artifact_hashed
        transition_gaps = 0
        for model, aggregate in ((GoalModel, "goal"), (WorkModel, "work"), (RunModel, "run")):
            rows = list(
                await session.execute(select(model.id, model.version).where(model.version > 0))
            )
            for aggregate_id, version in rows:
                maximum = await session.scalar(
                    select(func.max(AuditRecordModel.aggregate_version)).where(
                        AuditRecordModel.aggregate_type == aggregate,
                        AuditRecordModel.aggregate_id == aggregate_id,
                    )
                )
                if maximum != version:
                    transition_gaps += 1
        checks["state_transition_audit_coverage"] = transition_gaps == 0
        active_leases = await session.scalar(
            select(func.count())
            .select_from(WorkerLeaseModel)
            .where(WorkerLeaseModel.expires_at > func.now())
        )
        checks["worker_lease"] = bool(active_leases)
        checks["signed_observations"] = bool(
            await session.scalar(
                select(func.count())
                .select_from(ObservationModel)
                .where(func.length(ObservationModel.signature) == 64)
            )
        )
        manifest = await session.scalar(
            select(ExperimentManifestModel).where(
                ExperimentManifestModel.name == "REGENT_P0_ABC",
                ExperimentManifestModel.version == "1.0.0",
            )
        )
        run_count = 0
        decision_count = 0
        if manifest is not None:
            run_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(ExperimentRunModel)
                    .where(ExperimentRunModel.manifest_id == manifest.id)
                )
                or 0
            )
            decision_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(ProductDecisionRecordModel)
                    .where(ProductDecisionRecordModel.manifest_id == manifest.id)
                )
                or 0
            )
        checks["frozen_experiment_270"] = run_count == 270
        checks["unique_product_decision"] = decision_count == 1
        details.update(
            revision=revision,
            csv_achieved=csv_count,
            evt_certified=evt_count,
            artifact_total=artifact_total,
            transition_audit_gaps=transition_gaps,
            experiment_runs=run_count,
            decision_records=decision_count,
        )
    checks["console_artifact"] = Path("/app/apps/regent-console/index.html").exists()
    await engine.dispose()
    return {"passed": all(checks.values()), "checks": checks, "details": details}


def main() -> None:
    result = asyncio.run(audit())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
