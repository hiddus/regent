import asyncio
from unittest.mock import AsyncMock

from regent.runtime.worker_leases import WorkerLease
from regent.worker.main import Worker


def test_worker_stops_and_releases_lease() -> None:
    async def scenario() -> None:
        leases = AsyncMock()
        leases.acquire.return_value = WorkerLease(
            worker_id="worker-test",
            token=__import__("uuid").uuid4(),
            expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        dispatcher = AsyncMock()
        worker = Worker(
            worker_id="worker-test",
            dispatcher=dispatcher,
            leases=leases,
            poll_seconds=60,
            heartbeat_seconds=10,
        )
        task = asyncio.create_task(worker.serve())
        await asyncio.sleep(0)
        worker.stop()
        await asyncio.wait_for(task, timeout=1)
        leases.acquire.assert_awaited_once()
        leases.release.assert_awaited_once()
        dispatcher.dispatch_once.assert_awaited_once_with("worker-test")

    asyncio.run(scenario())
