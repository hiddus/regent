import pytest

from regent.api.transient_progress import TransientProgressRegistry


@pytest.mark.asyncio
async def test_progress_is_ordered_scoped_and_non_persistent() -> None:
    registry = TransientProgressRegistry()
    await registry.publish("p1", "r1", "received")
    await registry.publish("p1", "r1", "interpreting")
    await registry.publish("p2", "r2", "received")
    items = await registry.since("p1", 0)
    assert [item["stage"] for item in items] == ["received", "interpreting"]
    assert all(item["request_id"] == "r1" for item in items)
    assert all("_expires" not in item for item in items)


@pytest.mark.asyncio
async def test_terminal_event_is_available_then_expires() -> None:
    registry = TransientProgressRegistry(terminal_ttl=0)
    await registry.publish("p", "r", "final", terminal=True)
    assert await registry.since("p", 0) == []


@pytest.mark.asyncio
async def test_sequence_cursor_prevents_duplicate_sse_delivery() -> None:
    registry = TransientProgressRegistry()
    first = await registry.publish("p", "r", "received")
    await registry.publish("p", "r", "interpreting")
    items = await registry.since("p", int(first["sequence"]))
    assert [item["stage"] for item in items] == ["interpreting"]
