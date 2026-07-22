from regent.infrastructure.models import (
    ArtifactModel,
    AuditRecordModel,
    Base,
    FileChangeSetModel,
    GenerationRunModel,
    GoalModel,
    GoalSpecModel,
    OutboxEventModel,
    RunModel,
    WorkModel,
    WorkspaceSnapshotModel,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable


def compile_table(model: type[Base]) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_kernel_metadata_contains_required_tables() -> None:
    assert set(Base.metadata.tables) == {
        "app_projects",
        "goals",
        "goal_specs",
        "works",
        "runs",
        "audit_records",
        "outbox_events",
        "worker_leases",
        "artifacts",
        "evidence",
        "discovery_rounds",
        "product_hypotheses",
        "hypothesis_evidence_refs",
        "hypothesis_decisions",
        "requirement_revisions",
        "capability_resolution_plans",
        "capability_resolution_items",
        "generation_plans",
        "generation_runs",
        "file_change_sets",
        "workspace_snapshots",
        "dependency_resolutions",
        "app_builds",
        "verification_reports",
        "release_candidates",
        "deployments",
        "durable_timers",
        "execution_permits",
        "human_tasks",
        "capabilities",
        "agent_specs",
        "organizations",
        "assignments",
        "tool_specs",
        "tool_certifications",
        "observations",
        "experience_records",
        "side_effect_attempts",
        "experiment_manifests",
        "experiment_runs",
        "product_decision_records",
        "metric_definition_bindings",
        "gate_evaluations",
        "iteration_decisions",
        "conversations",
        "conversation_messages",
        "conversation_commands",
        "app_preview_releases",
        "self_improvement_runs",
    }


def test_goal_spec_version_is_unique_per_goal() -> None:
    ddl = compile_table(GoalSpecModel)
    assert "CONSTRAINT uq_goal_specs_goal_version UNIQUE (goal_id, version)" in ddl


def test_status_checks_are_in_postgresql_ddl() -> None:
    assert "ck_goals_status" in compile_table(GoalModel)
    assert "ck_works_status" in compile_table(WorkModel)
    assert "ck_runs_status" in compile_table(RunModel)
    assert "ck_outbox_events_status" in compile_table(OutboxEventModel)


def test_one_active_run_per_work_uses_partial_unique_index() -> None:
    index = next(
        item for item in RunModel.__table__.indexes if item.name == "uq_runs_one_active_per_work"
    )
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "CREATE UNIQUE INDEX uq_runs_one_active_per_work" in ddl
    assert "WHERE status IN" in ddl
    assert "CREATED" in ddl
    assert "RUNNING" in ddl


def test_artifact_versions_are_scoped_to_work_or_unscoped_goal() -> None:
    indexes = {item.name: item for item in ArtifactModel.__table__.indexes}
    work_ddl = str(
        CreateIndex(indexes["uq_artifacts_work_type_version"]).compile(dialect=postgresql.dialect())
    )
    goal_ddl = str(
        CreateIndex(indexes["uq_artifacts_goal_type_version_unscoped"]).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ON artifacts (work_id, artifact_type, version)" in work_ddl
    assert "WHERE work_id IS NOT NULL" in work_ddl
    assert "ON artifacts (goal_id, artifact_type, version)" in goal_ddl
    assert "WHERE work_id IS NULL" in goal_ddl


def test_audit_and_outbox_are_append_or_dispatch_oriented() -> None:
    assert "updated_at" not in AuditRecordModel.__table__.columns
    assert "updated_at" not in OutboxEventModel.__table__.columns
    assert "occurred_at" in AuditRecordModel.__table__.columns
    assert "available_at" in OutboxEventModel.__table__.columns


def test_generation_tables_enforce_single_outputs() -> None:
    assert "ck_generation_runs_status" in compile_table(GenerationRunModel)
    assert "uq_file_change_sets_run" in compile_table(FileChangeSetModel)
    assert "uq_workspace_snapshots_run" in compile_table(WorkspaceSnapshotModel)
