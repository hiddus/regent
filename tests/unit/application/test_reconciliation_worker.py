"""Tests for P0-A: ExternalOperation reconciliation and stale detection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.external_operation_service import (
    ExternalOperationService,
    request_digest,
)
from regent.application.provider_capability import (
    PROVIDER_CAPABILITY_MATRIX,
    ProviderCapability,
    ProviderCapabilityProfile,
    require_auto_irreversible,
)
from regent.application.reconciliation_worker import ReconciliationWorker
from regent.domain.errors import DomainError
from regent.infrastructure.models import ExternalOperationModel

# ---------------------------------------------------------------------------
# Helpers — mock session factory
# ---------------------------------------------------------------------------


def _mock_sessions():
    """Create a mock async session factory for testing."""
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
    status: str = "PREPARED",
    updated_at: datetime | None = None,
    reconcile_attempts: dict | None = None,
    reconcile_deadline: datetime | None = None,
) -> ExternalOperationModel:
    """Create a minimal ExternalOperationModel for testing."""
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
        dispatch_generation=1 if status != "PREPARED" else 0,
        result_summary={},
        reconcile_attempts=reconcile_attempts or {},
        reconcile_deadline=reconcile_deadline,
        created_at=now,
        updated_at=updated_at or now,
    )


# ---------------------------------------------------------------------------
# Test: request_digest
# ---------------------------------------------------------------------------


class TestRequestDigest:
    def test_deterministic(self) -> None:
        payload = {"action": "deploy", "target": "preview"}
        d1 = request_digest(payload)
        d2 = request_digest(payload)
        assert d1 == d2
        assert len(d1) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Test: query_provider
# ---------------------------------------------------------------------------


class TestQueryProvider:
    def test_registered_provider(self) -> None:
        factory, _ = _mock_sessions()
        service = ExternalOperationService(factory)
        profile = service.query_provider("allowlisted-http-source-v1")
        assert profile is not None
        assert profile.name == "allowlisted-http-source-v1"
        assert ProviderCapability.IDEMPOTENT_REPLAY in profile.capabilities

    def test_unregistered_provider(self) -> None:
        factory, _ = _mock_sessions()
        service = ExternalOperationService(factory)
        profile = service.query_provider("unknown-provider-xyz")
        assert profile is None

    def test_require_auto_irreversible_registered(self) -> None:
        profile = require_auto_irreversible("allowlisted-http-source-v1")
        assert profile.allows_auto_irreversible()

    def test_require_auto_irreversible_unknown(self) -> None:
        with pytest.raises(PermissionError, match="not registered"):
            require_auto_irreversible("unknown-provider-xyz")


# ---------------------------------------------------------------------------
# Test: ReconciliationWorker
# ---------------------------------------------------------------------------


class TestReconciliationWorker:
    def test_init_default_timeout(self) -> None:
        factory, _ = _mock_sessions()
        worker = ReconciliationWorker(factory)
        assert worker._timeout_minutes == 15

    def test_custom_timeout(self) -> None:
        factory, _ = _mock_sessions()
        worker = ReconciliationWorker(factory, timeout_minutes=30)
        assert worker._timeout_minutes == 30

    @pytest.mark.asyncio
    async def test_tick_no_stale(self) -> None:
        factory, session = _mock_sessions()
        # scalars returns empty result
        mock_result = MagicMock()
        mock_result.all.return_value = []
        session.scalars = AsyncMock(return_value=mock_result)
        worker = ReconciliationWorker(factory)
        reconciled = await worker.tick()
        assert reconciled == []

    @pytest.mark.asyncio
    async def test_tick_with_stale_dispatching(self) -> None:
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
        assert reconciled[0] == stale_eo.id
        assert stale_eo.status == "RECONCILING"
        assert stale_eo.reconcile_deadline is not None

    @pytest.mark.asyncio
    async def test_tick_with_stale_unknown(self) -> None:
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

    @pytest.mark.asyncio
    async def test_tick_skips_fresh_eo(self) -> None:
        """Fresh EOs (updated_at >= cutoff) are not returned by the query."""
        factory, session = _mock_sessions()
        # Simulate DB query returning empty (fresh EOs filtered out by WHERE clause)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        session.scalars = AsyncMock(return_value=mock_result)

        worker = ReconciliationWorker(factory)
        custom_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        reconciled = await worker.tick(now=custom_now)

        assert len(reconciled) == 0

    @pytest.mark.asyncio
    async def test_reconcile_specific_requires_existing(self) -> None:
        factory, session = _mock_sessions()
        session.get = AsyncMock(return_value=None)
        worker = ReconciliationWorker(factory)
        fake_id = uuid.uuid4()
        with pytest.raises(DomainError):
            await worker.reconcile_specific(
                fake_id, resolved_status="SUCCEEDED"
            )


# ---------------------------------------------------------------------------
# Test: Provider Capability Matrix
# ---------------------------------------------------------------------------


class TestProviderCapabilityMatrix:
    def test_all_registered_providers_have_capabilities(self) -> None:
        for name, profile in PROVIDER_CAPABILITY_MATRIX.items():
            assert profile.name == name
            assert len(profile.capabilities) > 0

    def test_idempotent_replay_allows_irreversible(self) -> None:
        profile = ProviderCapabilityProfile(
            name="test",
            capabilities=frozenset({ProviderCapability.IDEMPOTENT_REPLAY}),
            irreversible=True,
        )
        assert profile.allows_auto_irreversible()

    def test_no_capabilities_blocks_irreversible(self) -> None:
        profile = ProviderCapabilityProfile(
            name="test",
            capabilities=frozenset(),
            irreversible=True,
        )
        assert not profile.allows_auto_irreversible()

    def test_reversible_allows_auto(self) -> None:
        profile = ProviderCapabilityProfile(
            name="test",
            capabilities=frozenset(),
            irreversible=False,
        )
        assert profile.allows_auto_irreversible()

    def test_query_capability_allows_irreversible(self) -> None:
        profile = ProviderCapabilityProfile(
            name="test",
            capabilities=frozenset(
                {ProviderCapability.QUERY_BY_OPERATION_KEY}
            ),
            irreversible=True,
        )
        assert profile.allows_auto_irreversible()
