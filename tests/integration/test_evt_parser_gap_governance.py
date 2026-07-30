"""EVT_PARSER_GAP governance — certification, tool scope, hidden isolation, replay."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from sqlalchemy import select

from regent.application.evt_gap_service import EvtParserGapService
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    CapabilityModel,
    EvidenceModel,
    ToolCertificationModel,
    ToolSpecModel,
)


def _evt_line(timestamp: str, category: str, value: str, *, valid: bool = True) -> str:
    payload = f"{timestamp}|{category}|{value}"
    crc = f"{zlib.crc32(payload.encode()) & 0xFFFFFFFF:08x}"
    if not valid:
        crc = "00000000" if crc != "00000000" else "ffffffff"
    return f"{payload}|{crc}"


def _public_fixture() -> str:
    rows = [
        _evt_line("2026-01-01T00:00:00Z", "alpha", "10"),
        _evt_line("2026-01-01T00:00:01Z", "beta", "20"),
        _evt_line("2026-01-01T00:00:02Z", "alpha", "30"),
        _evt_line("2026-01-01T00:00:03Z", "gamma", "40", valid=False),
        _evt_line("2026-01-01T00:00:04Z", "beta", "50"),
        _evt_line("2026-01-01T00:00:05Z", "alpha", "60"),
    ]
    return "\n".join(rows)


@pytest.mark.governance
@pytest.mark.asyncio
async def test_evt_parser_gap_capability_certification(db_sessions, tmp_path: Path) -> None:
    service = EvtParserGapService(db_sessions, FileArtifactStore(tmp_path / "artifacts"))
    receipt = await service.execute(
        input_text=_public_fixture(),
        idempotency_key="evt-gap-gov-1",
        actor="test-governance",
    )

    assert receipt.capability_status == "GOAL_CERTIFIED"
    assert receipt.tool_status == "CERTIFIED"
    assert receipt.goal_status == "ACHIEVED"
    assert receipt.work_status == "ACCEPTED"
    assert receipt.run_status == "EXECUTED"
    assert (receipt.valid_count, receipt.invalid_count) == (5, 1)
    assert receipt.replayed is False

    async with db_sessions() as session:
        tool = await session.get(ToolSpecModel, receipt.tool_spec_id)
        cert = await session.get(ToolCertificationModel, receipt.certification_id)
        caps = list(
            await session.scalars(
                select(CapabilityModel).where(CapabilityModel.scope_goal_id == receipt.goal_id)
            )
        )
        evidence = await session.scalar(
            select(EvidenceModel).where(EvidenceModel.goal_id == receipt.goal_id)
        )

    assert tool is not None
    assert tool.scope_goal_id == receipt.goal_id
    assert tool.status == "CERTIFIED"
    assert cert is not None
    assert cert.public_passed is True
    assert cert.hidden_passed is True
    assert cert.security_checks.get("hidden_tests_isolated") is True
    assert len(caps) == 1
    assert caps[0].status == "GOAL_CERTIFIED"
    assert evidence is not None
    assert evidence.payload.get("public_passed") is True
    assert evidence.payload.get("hidden_passed") is True


@pytest.mark.governance
@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_evt_parser_gap_idempotent_replay(db_sessions, tmp_path: Path) -> None:
    service = EvtParserGapService(db_sessions, FileArtifactStore(tmp_path / "artifacts"))
    first = await service.execute(
        input_text=_public_fixture(),
        idempotency_key="evt-gap-gov-replay",
        actor="test-governance",
    )
    second = await service.execute(
        input_text=_public_fixture(),
        idempotency_key="evt-gap-gov-replay",
        actor="test-governance",
    )
    assert second.replayed is True
    assert second.certification_id == first.certification_id
    assert second.tool_spec_id == first.tool_spec_id
    assert second.goal_id == first.goal_id
