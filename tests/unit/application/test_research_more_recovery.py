"""Unit tests for RESEARCH_MORE capability-driven recovery."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from regent.application.capability_resolution_service import (
    CapabilityCandidate,
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
)
from regent.application.research_more_recovery import ResearchMoreRecoveryService
from regent.infrastructure.evidence_capability import CAPABILITY_NAME
from regent.infrastructure.models import GoalModel, GoalSpecModel


def test_resolution_reuses_allowlisted_http_capability() -> None:
    capability_id = uuid.uuid4()
    plan = CapabilityResolutionService().resolve(
        [
            CapabilityGap(
                requirement_key="evidence.http_snapshot",
                capability_name=CAPABILITY_NAME,
                build_allowed=False,
                human_resolvable=True,
            )
        ],
        [CapabilityCandidate(capability_id, CAPABILITY_NAME, "VERIFIED")],
        [],
    )
    assert plan.items[0].method is ResolutionMethod.REUSE
    assert plan.items[0].capability_id == capability_id


@pytest.mark.asyncio
async def test_recover_emits_discovery_round_with_capability_feeds() -> None:
    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    round_id = uuid.uuid4()
    capability_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="做一个 AI 新闻 App",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"execution_stage": "RESEARCH_MORE"},
    )
    spec = GoalSpecModel(
        id=uuid.uuid4(),
        goal_id=goal_id,
        version=1,
        status="FROZEN",
        content_hash="abc",
        explicit_constraints={},
        success_criteria={"usable": True},
    )

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, *args, **kwargs: {
        GoalModel: goal,
    }.get(model))
    session.scalar = AsyncMock(side_effect=[spec, MagicMock(status="VERIFIED"), 1, None])
    session.add = MagicMock()

    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)

    with (
        patch(
            "regent.application.research_more_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=capability_id),
        ),
        patch.object(ResearchMoreRecoveryService, "_append", AsyncMock()),
    ):
        result = await ResearchMoreRecoveryService(factory).recover(
            goal_id=goal_id,
            project_id=project_id,
            round_id=round_id,
            actor="test",
        )

    assert result.recovered is True
    assert result.method == "REUSE"
    assert result.capability_id == capability_id
    assert result.authorized_urls
    assert goal.metadata_json["execution_stage"] == "DISCOVERING"
    assert goal.metadata_json["awaiting_authorized_sources"] is False
    assert goal.metadata_json["capability_resolution"]["method"] == "REUSE"
    assert session.add.call_count >= 2


@pytest.mark.asyncio
async def test_recover_adapts_instead_of_waiting_after_max_attempts() -> None:
    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    round_id = uuid.uuid4()
    capability_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="做一个 AI 新闻 App 聚合 36氪",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={
            "research_more_recovery_attempts": 2,
            "authorized_source_urls": ["https://techcrunch.com/feed/"],
        },
    )
    spec = GoalSpecModel(
        id=uuid.uuid4(),
        goal_id=goal_id,
        version=1,
        status="FROZEN",
        content_hash="abc",
        explicit_constraints={},
        success_criteria={"ok": True},
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=goal)
    session.scalar = AsyncMock(side_effect=[spec, 3, None])
    session.add = MagicMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)

    with (
        patch(
            "regent.application.research_more_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=capability_id),
        ),
        patch.object(ResearchMoreRecoveryService, "_append", AsyncMock()),
    ):
        result = await ResearchMoreRecoveryService(factory).recover(
            goal_id=goal_id,
            project_id=project_id,
            round_id=round_id,
            actor="test",
        )

    assert result.recovered is True
    assert result.method == "ADAPT_CONTINUE"
    assert goal.metadata_json["awaiting_authorized_sources"] is False
    assert goal.metadata_json["research_more_adapted"] is True
    assert goal.metadata_json["discovery_policy"] == "adapt_select_with_available_evidence"
    assert goal.metadata_json["execution_stage"] == "DISCOVERING"
