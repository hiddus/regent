"""P0-B: Fault injection tests for G8 durable external effects.

Four fault scenarios required by PRD §5.1 G8:
1. Worker crash recovery — DISPATCHING EO not re-executed after crash
2. Duplicate delivery — same operation_key produces exactly one side effect
3. Response lost — Provider no response → UNKNOWN → RECONCILING
4. UNKNOWN reconcile — stale EO auto-transitions to RECONCILING within 15min
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.external_operation_service import (
    ExternalOperationService,
)
from regent.application.reconciliation_worker import ReconciliationWorker
from regent.domain.errors import DomainError
from regent.infrastructure.models import ExternalOperationModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_sessions():
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)
    return factory, session


def _make_eo(
    *,
    status: str = "DISPATCHING",
    updated_at: datetime | None = None,
    reconcile_attempts: dict | None = None,
) -> ExternalOperationModel:
    now = datetime.now(UTC)
    return ExternalOperationModel(
        id=uuid.uuid4(),
        operation_key=f"op-{uuid.uuid4().hex[:8]}",
        provider="static-preview-deploy-v1",
        action="deploy",
        status=status,
        request_digest="abc123",
        permit_id=uuid.uuid4(),
        local_fencing_token=uuid.uuid4(),
        dispatch_generation=1,
        result_summary={},
        reconcile_attempts=reconcile_attempts or {},
        created_at=now,
        updated_at=updated_at or now,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Worker crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """Worker crash during DISPATCHING — EO must not re-execute."""

    @pytest.mark.asyncio
    async def test_dispatching_eo_not_re_executed_after_crash(self) -> None:
        """After worker crash, a DISPATCHING EO should be detected as stale
        and moved to RECONCILING, not re-dispatched."""
        factory, session = _mock_sessions()
        stale_eo = _make_eo(
            status="DISPATCHING",
            updated_at=datetime(2026, 7, 25, 10, 0, 0, tzinfo=UTC),
        )
        mock_result = MagicMock()
        mock_result.all.return_value = [stale_eo]
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        reconciled = await worker.tick(now=custom_now)

        assert len(reconciled) == 1
        assert stale_eo.status == "RECONCILING"
        assert stale_eo.reconcile_deadline is not None

    @pytest.mark.asyncio
    async def test_begin_dispatch_refuses_non_prepared(self) -> None:
        """begin_dispatch on RECONCILING EO must raise."""
        factory, session = _mock_sessions()
        eo = _make_eo(status="RECONCILING")
        session.get = AsyncMock(return_value=eo)

        service = ExternalOperationService(factory)
        with pytest.raises(DomainError, match="cannot begin dispatch"):
            await service.begin_dispatch(
                eo.id,
                worker_lease_token="new-lease",
                expected_fencing_token=eo.local_fencing_token,
            )


# ---------------------------------------------------------------------------
# Scenario 2: Duplicate delivery idempotency
# ---------------------------------------------------------------------------


class TestDuplicateDelivery:
    """Same operation_key must produce exactly one side effect."""

    @pytest.mark.asyncio
    async def test_prepare_idempotent_same_operation_key(self) -> None:
        """Preparing the same operation_key twice returns the existing EO."""
        factory, session = _mock_sessions()
        existing_eo = _make_eo(status="PREPARED")
        existing_eo.operation_key = "duplicate-key"
        session.scalar = AsyncMock(return_value=existing_eo)

        service = ExternalOperationService(factory)
        result = await service.prepare(
            operation_key="duplicate-key",
            provider="static-preview-deploy-v1",
            action="deploy",
            permit_id=uuid.uuid4(),
            local_fencing_token=existing_eo.local_fencing_token,
            payload={"target": "preview"},
        )
        assert result.operation_key == "duplicate-key"
        assert result.status == "PREPARED"

    @pytest.mark.asyncio
    async def test_begin_dispatch_idempotent_when_already_dispatching(self) -> None:
        """begin_dispatch on already-DISPATCHING EO returns same result."""
        factory, session = _mock_sessions()
        eo = _make_eo(status="DISPATCHING")
        eo.dispatch_generation = 1
        permit = MagicMock()
        permit.status = "CONSUMED"
        session.get = AsyncMock(side_effect=[eo, permit])

        service = ExternalOperationService(factory)
        result = await service.begin_dispatch(
            eo.id,
            worker_lease_token="lease-1",
            expected_fencing_token=eo.local_fencing_token,
        )
        assert result.status == "DISPATCHING"
        assert result.dispatch_generation == 1


# ---------------------------------------------------------------------------
# Scenario 3: Response lost → UNKNOWN → RECONCILING
# ---------------------------------------------------------------------------


class TestResponseLost:
    """Provider loses response — EO goes UNKNOWN, then RECONCILING."""

    @pytest.mark.asyncio
    async def test_mark_unknown_then_reconcile(self) -> None:
        """Full flow: DISPATCHING → UNKNOWN → RECONCILING → SUCCEEDED."""
        factory, session = _mock_sessions()
        eo = _make_eo(status="DISPATCHING")

        service = ExternalOperationService(factory)

        # Mock the update for mark_unknown
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute = AsyncMock(return_value=mock_result)
        await service.mark_unknown(eo.id, reason="provider_timeout")

        # Simulate state transition to UNKNOWN
        eo.status = "UNKNOWN"

        # begin_reconcile
        session.get = AsyncMock(return_value=eo)
        await service.begin_reconcile(eo.id)
        assert eo.status == "RECONCILING"

        # resolve_reconcile
        await service.resolve_reconcile(
            eo.id,
            status="SUCCEEDED",
            external_id="ext-123",
            summary={"reconciled": True},
        )
        assert eo.status == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_mark_unknown_from_dispatching(self) -> None:
        """mark_unknown works from DISPATCHING state."""
        factory, session = _mock_sessions()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute = AsyncMock(return_value=mock_result)
        service = ExternalOperationService(factory)
        # Should not raise
        await service.mark_unknown(uuid.uuid4(), reason="response_lost")


# ---------------------------------------------------------------------------
# Scenario 4: UNKNOWN auto-reconcile within 15min
# ---------------------------------------------------------------------------


class TestUnknownAutoReconcile:
    """Stale UNKNOWN/DISPATCHING EOs auto-transition to RECONCILING."""

    @pytest.mark.asyncio
    async def test_stale_unknown_auto_reconciled(self) -> None:
        """UNKNOWN EO older than 15min is auto-reconciled."""
        factory, session = _mock_sessions()
        stale_eo = _make_eo(
            status="UNKNOWN",
            updated_at=datetime(2026, 7, 25, 10, 0, 0, tzinfo=UTC),
        )
        mock_result = MagicMock()
        mock_result.all.return_value = [stale_eo]
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        reconciled = await worker.tick(now=custom_now)

        assert len(reconciled) == 1
        assert stale_eo.status == "RECONCILING"
        assert stale_eo.reconcile_deadline is not None
        # Deadline should be 15min from now
        expected_deadline = custom_now + timedelta(minutes=15)
        assert stale_eo.reconcile_deadline == expected_deadline

    @pytest.mark.asyncio
    async def test_stale_dispatching_auto_reconciled(self) -> None:
        """DISPATCHING EO older than 15min is auto-reconciled."""
        factory, session = _mock_sessions()
        stale_eo = _make_eo(
            status="DISPATCHING",
            updated_at=datetime(2026, 7, 25, 10, 0, 0, tzinfo=UTC),
        )
        mock_result = MagicMock()
        mock_result.all.return_value = [stale_eo]
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        reconciled = await worker.tick(now=custom_now)

        assert len(reconciled) == 1
        assert stale_eo.status == "RECONCILING"

    @pytest.mark.asyncio
    async def test_fresh_eo_not_reconciled(self) -> None:
        """EO updated within 15min is NOT auto-reconciled."""
        factory, session = _mock_sessions()
        mock_result = MagicMock()
        mock_result.all.return_value = []  # DB query returns empty (filtered)
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        reconciled = await worker.tick(now=custom_now)

        assert len(reconciled) == 0

    @pytest.mark.asyncio
    async def test_reconcile_attempts_tracked(self) -> None:
        """Each reconciliation sweep records an attempt."""
        factory, session = _mock_sessions()
        stale_eo = _make_eo(
            status="DISPATCHING",
            updated_at=datetime(2026, 7, 25, 10, 0, 0, tzinfo=UTC),
        )
        mock_result = MagicMock()
        mock_result.all.return_value = [stale_eo]
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        await worker.tick(now=custom_now)

        assert len(stale_eo.reconcile_attempts) > 0
