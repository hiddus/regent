from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from regent.application.learning_update_service import (
    ApplyLearningUpdate,
    LearningUpdateService,
    ProposeLearningUpdate,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import AppProjectModel, GoalModel, LearningUpdateModel


def proposal(**overrides):
    values = {
        "org_key": "org-1",
        "target_type": "routing_policy",
        "target_key": "default",
        "base_version": "v1",
        "candidate_version": "v2",
        "before": {"strategy": "single"},
        "after": {"strategy": "challenge"},
        "evidence_refs": ["observation:1"],
        "applicability": {"goal_kind": "app"},
        "invalidation": {"model_changed": True},
        "ttl_seconds": 3600,
        "actor": "learner",
    }
    values.update(overrides)
    return ProposeLearningUpdate(**values)


@pytest.mark.asyncio
async def test_proposal_is_not_applied_until_consumed(db_sessions) -> None:
    service = LearningUpdateService(db_sessions)
    update = await service.propose(proposal())
    assert update.status == "PROPOSED"
    assert update.first_applied_at is None
    assert update.before_json != update.after_json
    assert update.expires_at is not None

    await service.record_application(
        ApplyLearningUpdate(
            update_id=update.id,
            consumer_type="run",
            consumer_ref="run-42",
            applied_version="v2",
            read_context={"decision": "selected"},
        )
    )
    async with db_sessions() as session:
        refreshed = await session.get(LearningUpdateModel, update.id)
        assert refreshed is not None
        assert refreshed.status == "APPLIED"
        assert refreshed.first_applied_at is not None


@pytest.mark.asyncio
async def test_wrong_version_cannot_claim_application(db_sessions) -> None:
    service = LearningUpdateService(db_sessions)
    update = await service.propose(proposal())
    with pytest.raises(DomainError) as exc:
        await service.record_application(
            ApplyLearningUpdate(
                update_id=update.id,
                consumer_type="run",
                consumer_ref="run-1",
                applied_version="v3",
            )
        )
    assert exc.value.code is ErrorCode.VERSION_CONFLICT


@pytest.mark.asyncio
async def test_application_is_idempotent_per_consumer(db_sessions) -> None:
    service = LearningUpdateService(db_sessions)
    update = await service.propose(proposal())
    command = ApplyLearningUpdate(update.id, "run", "run-1", "v2")
    first = await service.record_application(command)
    replay = await service.record_application(command)
    assert replay.id == first.id


@pytest.mark.asyncio
async def test_expired_update_cannot_be_applied(db_sessions) -> None:
    service = LearningUpdateService(db_sessions)
    update = await service.propose(proposal())
    async with db_sessions() as session, session.begin():
        stored = await session.get(LearningUpdateModel, update.id)
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(DomainError, match="expired"):
        await service.record_application(ApplyLearningUpdate(update.id, "run", "r", "v2"))
    async with db_sessions() as session:
        refreshed = await session.get(LearningUpdateModel, update.id)
        assert refreshed is not None
        assert refreshed.status == "EXPIRED"


@pytest.mark.asyncio
async def test_rollback_reference_must_target_same_object(db_sessions) -> None:
    service = LearningUpdateService(db_sessions)
    original = await service.propose(proposal())
    with pytest.raises(DomainError, match="same target"):
        await service.propose(
            proposal(
                target_key="other",
                candidate_version="v3",
                rollback_update_id=original.id,
            )
        )


@pytest.mark.asyncio
async def test_failure_candidate_is_idempotent_and_applied_by_later_plan(db_sessions) -> None:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            AppProjectModel(
                id=project_id,
                name="learning-project",
                product_intent="learn from generation failures",
                status="ACTIVE",
                created_by="test",
            )
        )
        session.add(
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="build app",
                status="ACTIVE",
                version=1,
                created_by="test",
                correlation_id=uuid.uuid4(),
                metadata_json={},
            )
        )

    service = LearningUpdateService(db_sessions)
    first = await service.propose_failure_constraint(
        goal_id=goal_id,
        org_key=f"project:{project_id}",
        failure_code="SMOKE_FAILED",
        summary="GET / returned 500",
        avoid="verify the root route before submit",
    )
    replay = await service.propose_failure_constraint(
        goal_id=goal_id,
        org_key=f"project:{project_id}",
        failure_code="SMOKE_FAILED",
        summary="GET / returned 500",
        avoid="verify the root route before submit",
    )
    assert replay.id == first.id
    assert first.status == "PROPOSED"

    applied = await service.apply_pending_for_goal(
        goal_id=goal_id,
        consumer_type="generation_plan",
        consumer_ref="plan-2",
    )
    assert applied == [first.id]
    async with db_sessions() as session:
        refreshed = await session.get(LearningUpdateModel, first.id)
        assert refreshed is not None
        assert refreshed.status == "APPLIED"
