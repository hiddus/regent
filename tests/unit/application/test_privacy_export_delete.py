"""Privacy §7.1–7.4 behavior tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from regent.application.privacy_service import (
    PiiClass,
    PrivacyService,
    classify_and_minimize,
    privacy_notice,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState
from regent.infrastructure.models import AuditRecordModel, EvidenceModel, GoalModel, ObservationModel


@pytest.mark.governance
def test_privacy_notice_exposes_scopes_and_policy() -> None:
    notice = privacy_notice()
    assert notice["notice_version"]
    assert "Observation" in notice["notice_text"] or "observation" in notice["notice_text"].lower()
    assert "observation" in notice["scopes"]
    assert notice["pii_field_policy"]["email"] == PiiClass.RESTRICTED.value


@pytest.mark.governance
def test_classify_redacts_restricted_pii() -> None:
    result = classify_and_minimize("contact me at alice@example.com or 13800138000")
    assert result.contains_restricted is True
    assert result.classification == PiiClass.RESTRICTED
    assert "alice@example.com" not in result.minimized_text
    assert "13800138000" not in result.minimized_text
    assert "email" in result.findings


@pytest.mark.governance
@pytest.mark.asyncio
async def test_grant_withdraw_and_require_consent(db_sessions, monkeypatch) -> None:
    monkeypatch.setenv("REGENT_PRIVACY_CONSENT_ENFORCED", "true")
    from regent.config import get_settings

    get_settings.cache_clear()
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="consent goal",
                created_by="owner-a",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    svc = PrivacyService(db_sessions)
    granted = await svc.grant_consent(goal_id, subject="owner-a")
    assert granted.status == "GRANTED"
    await svc.require_consent_for_scope(goal_id, scope="observation")
    withdrawn = await svc.withdraw_consent(goal_id, subject="owner-a")
    assert withdrawn.status == "WITHDRAWN"
    with pytest.raises(DomainError) as raised:
        await svc.require_consent_for_scope(goal_id, scope="observation")
    assert raised.value.code == ErrorCode.POLICY_DENIED
    get_settings.cache_clear()


@pytest.mark.governance
@pytest.mark.asyncio
async def test_anonymize_expired_observations(db_sessions) -> None:
    goal_id = uuid.uuid4()
    obs_id = uuid.uuid4()
    old = datetime.now(UTC) - timedelta(days=120)
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="retention goal",
                created_by="owner-a",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
        session.add(
            ObservationModel(
                id=obs_id,
                event_id=f"evt-{obs_id.hex[:8]}",
                goal_id=goal_id,
                metric_name="clicks",
                metric_value={"count": 3, "note": "user@x.com"},
                source="tracker",
                definition_version="v1",
                signature="a" * 64,
                is_bot=False,
                is_internal=False,
                observed_at=old,
            )
        )
    result = await PrivacyService(db_sessions).anonymize_expired(retention_days=90)
    assert result["observations_anonymized"] == 1
    async with db_sessions() as session:
        row = await session.get(ObservationModel, obs_id)
    assert row is not None
    assert row.anonymized_at is not None
    assert row.metric_value.get("anonymized") is True
    assert "user@x.com" not in str(row.metric_value)
    assert row.source.startswith("anonymized:")


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
    assert "13800138000" not in package.goal["original_input_preview"]
    assert package.goal["original_input_pii_class"] == PiiClass.RESTRICTED.value
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
