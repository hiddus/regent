"""Privacy export/delete behavior tests (PRD §7.4)."""

from __future__ import annotations

import uuid

import pytest

from regent.application.privacy_service import PrivacyService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState
from regent.infrastructure.models import AuditRecordModel, EvidenceModel, GoalModel


@pytest.mark.governance
@pytest.mark.asyncio
async def test_export_requires_owner(db_sessions) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="owner only export",
                created_by="owner-a",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    svc = PrivacyService(db_sessions)
    with pytest.raises(DomainError) as raised:
        await svc.export_goal(goal_id, requester="intruder")
    assert raised.value.code == ErrorCode.FORBIDDEN


@pytest.mark.governance
@pytest.mark.asyncio
async def test_export_includes_evidence_metadata_pii_minimized(db_sessions) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="secret phone 13800138000 should be truncated in preview only",
                created_by="owner-a",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
        await session.flush()
        session.add(
            EvidenceModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                evidence_type="sourced_observation",
                content_hash="b" * 64,
                producer_ref="test",
                quality_tier="OBSERVED",
                payload={"raw": "should-not-export"},
            )
        )
    package = await PrivacyService(db_sessions).export_goal(goal_id, requester="owner-a")
    assert package.owner == "owner-a"
    assert package.as_dict()["pii_minimized"] is True
    assert len(package.evidence) == 1
    assert "raw" not in package.evidence[0]
    assert "payload" not in package.evidence[0]


@pytest.mark.governance
@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_delete_request_writes_audit_and_is_idempotent(db_sessions) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="delete me",
                created_by="owner-a",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    svc = PrivacyService(db_sessions)
    first = await svc.request_delete(goal_id, requester="owner-a")
    second = await svc.request_delete(goal_id, requester="owner-a")
    assert first.replayed is False
    assert second.replayed is True
    assert first.audit_id == second.audit_id
    async with db_sessions() as session:
        audit = await session.get(AuditRecordModel, first.audit_id)
        goal = await session.get(GoalModel, goal_id)
    assert audit is not None
    assert audit.action == "DELETE_REQUESTED"
    assert goal is not None
    assert goal.metadata_json["delete_request"]["status"] == "DELETE_REQUESTED"
