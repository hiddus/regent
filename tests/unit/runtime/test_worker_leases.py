from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import WorkerLeaseModel
from regent.runtime.worker_leases import WorkerLeaseService
from sqlalchemy.ext.asyncio import AsyncSession


def make_service(current: WorkerLeaseModel | None) -> tuple[WorkerLeaseService, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = current
    session.scalar.return_value = datetime(2026, 7, 16, tzinfo=UTC)

    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)
    return WorkerLeaseService(factory, lease_seconds=30), session  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_acquire_registers_new_worker_with_database_time() -> None:
    service, session = make_service(None)
    lease = await service.acquire("worker-1", metadata={"host": "test"})
    assert lease.worker_id == "worker-1"
    assert lease.expires_at == datetime(2026, 7, 16, tzinfo=UTC) + timedelta(seconds=30)
    model = session.add.call_args.args[0]
    assert isinstance(model, WorkerLeaseModel)
    assert model.lease_token == lease.token


@pytest.mark.asyncio
async def test_live_worker_id_cannot_be_taken_over() -> None:
    current = WorkerLeaseModel(
        worker_id="worker-1",
        lease_token=__import__("uuid").uuid4(),
        started_at=datetime(2026, 7, 16, tzinfo=UTC),
        heartbeat_at=datetime(2026, 7, 16, tzinfo=UTC),
        expires_at=datetime(2026, 7, 16, tzinfo=UTC) + timedelta(seconds=10),
        metadata_json={},
    )
    service, _ = make_service(current)
    with pytest.raises(DomainError) as raised:
        await service.acquire("worker-1")
    assert raised.value.code == ErrorCode.LEASE_CONFLICT
