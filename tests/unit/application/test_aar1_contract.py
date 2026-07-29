"""AAR-1 M5 Contract + M6 memory-path gate tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from regent.application.aar1_contract import (
    engine_is_primary_writer,
    is_contract_phase,
    legacy_org_writes_allowed,
    memory_a2a_allowed,
)
from regent.application.agent_mesh import AgentMesh
from regent.domain.errors import DomainError, ErrorCode


class TestAar1ContractHelpers:
    def test_contract_phase_flags(self) -> None:
        assert is_contract_phase("contract")
        assert engine_is_primary_writer("contract")
        assert not legacy_org_writes_allowed("contract")
        assert legacy_org_writes_allowed("enforce")
        assert legacy_org_writes_allowed("dual_write")

    def test_memory_closed_in_contract_unless_override(self) -> None:
        assert memory_a2a_allowed(phase="contract", use_memory_override=None) is False
        assert memory_a2a_allowed(phase="contract", use_memory_override=True) is True
        assert memory_a2a_allowed(phase="enforce", use_memory_override=None) is True


class TestM6MemoryPathClosed:
    def test_delegate_rejected_in_contract(self) -> None:
        with patch("regent.config.get_settings") as gs:
            gs.return_value = MagicMock(aar1_phase="contract")
            mesh = AgentMesh()
            with pytest.raises(RuntimeError, match="contract phase"):
                mesh.delegate_task("a", "b", "x")

    def test_route_with_envelope_rejected_in_contract(self) -> None:
        import uuid

        from regent.application.agent_envelope import AgentEnvelope

        with patch("regent.config.get_settings") as gs:
            gs.return_value = MagicMock(aar1_phase="contract")
            mesh = AgentMesh()
            env = AgentEnvelope(
                envelope_id=uuid.uuid4(),
                source_agent="a",
                dest_agent="b",
                content={"hello": "world"},
                capability_scope=frozenset({"read"}),
            )
            with pytest.raises(RuntimeError, match="contract phase"):
                mesh.route_with_envelope(env)

    def test_explicit_memory_override_still_works(self) -> None:
        with patch("regent.config.get_settings") as gs:
            gs.return_value = MagicMock(aar1_phase="contract")
            mesh = AgentMesh(use_memory=True)
            task = mesh.delegate_task("a", "b", "ok")
            assert task.from_agent == "a"

    def test_enforce_with_durable_closes_memory(self) -> None:
        with patch("regent.config.get_settings") as gs:
            gs.return_value = MagicMock(aar1_phase="enforce")
            mesh = AgentMesh(durable_tasks=object())
            with pytest.raises(RuntimeError, match="AgentTaskService"):
                mesh.delegate_task("a", "b", "x")


class TestM5StopLegacyWrites:
    def test_dual_write_forbidden_in_contract(self) -> None:
        from regent.application.organization_service import OrganizationService

        svc = OrganizationService.__new__(OrganizationService)
        with patch("regent.config.get_settings") as gs:
            gs.return_value = MagicMock(aar1_phase="contract")
            with pytest.raises(DomainError) as exc:
                # run coroutine synchronously via pytest-asyncio? use asyncio.run
                import asyncio

                async def _run() -> None:
                    await svc._dual_write_organization(
                        session=MagicMock(),
                        organization_id=__import__("uuid").uuid4(),
                        goal_id=__import__("uuid").uuid4(),
                        strategy="SINGLE_AGENT",
                        best_template_id="single-agent-v1",
                        utility=0.5,
                        gaps=[],
                    )

                asyncio.run(_run())
            assert exc.value.code is ErrorCode.INVALID_STATE

    def test_default_settings_phase_is_contract(self) -> None:
        from regent.config import Settings

        # Fresh settings instance (bypass lru cache of get_settings)
        s = Settings(_env_file=None)
        assert s.aar1_phase == "contract"
        assert s.aar1_certified_hive is False

    def test_certified_hive_helpers(self) -> None:
        from regent.application.aar1_contract import (
            CERTIFIED_HIVE_TEMPLATE_ID,
            certified_hive_preferred,
            is_certified_hive_topology,
        )

        assert certified_hive_preferred(enabled=False) is None
        assert certified_hive_preferred(enabled=True) == CERTIFIED_HIVE_TEMPLATE_ID
        assert is_certified_hive_topology(
            {
                "template_id": CERTIFIED_HIVE_TEMPLATE_ID,
                "strategy": "FIXED_TEMPLATE",
                "roles": [
                    {"role": "pm"},
                    {"role": "dev"},
                    {"role": "qa", "independent_reviewer": True},
                ],
            }
        )
        assert not is_certified_hive_topology(
            {"template_id": "single-agent-v1", "strategy": "SINGLE_AGENT"}
        )


def test_contract_migration_revision_chain() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "core"
        / "migrations"
        / "versions"
        / "20260727_0033_aar1_foundation_contract.py"
    )
    spec = importlib.util.spec_from_file_location("aar1_contract_mig", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.revision == "20260727_0033"
    assert mig.down_revision == "20260727_0032"
    assert callable(mig.upgrade)
    assert callable(mig.downgrade)


def test_organizations_current_version_not_null_in_orm() -> None:
    from regent.infrastructure.models import OrganizationModel
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    ddl = str(CreateTable(OrganizationModel.__table__).compile(dialect=postgresql.dialect()))
    assert "current_version_id" in ddl
    col = OrganizationModel.__table__.c.current_version_id
    assert col.nullable is False
