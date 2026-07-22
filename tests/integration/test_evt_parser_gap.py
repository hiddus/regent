import uuid
import zlib
from pathlib import Path

from regent.application.evt_summary import execute_evt_summary
from regent.infrastructure.artifact_store import FileArtifactStore


def evt_line(timestamp: str, category: str, value: str, *, valid: bool = True) -> str:
    payload = f"{timestamp}|{category}|{value}"
    crc = f"{zlib.crc32(payload.encode()) & 0xFFFFFFFF:08x}"
    if not valid:
        crc = "00000000" if crc != "00000000" else "ffffffff"
    return f"{payload}|{crc}"


def test_evt_parser_public_sample(tmp_path: Path) -> None:
    rows = [
        evt_line("2026-01-01T00:00:00Z", "alpha", "10"),
        evt_line("2026-01-01T00:00:01Z", "beta", "20"),
        evt_line("2026-01-01T00:00:02Z", "alpha", "30"),
        evt_line("2026-01-01T00:00:03Z", "gamma", "40", valid=False),
        evt_line("2026-01-01T00:00:04Z", "beta", "50"),
        evt_line("2026-01-01T00:00:05Z", "alpha", "60"),
    ]
    result = execute_evt_summary(
        goal_id=uuid.uuid4(),
        input_text="\n".join(rows),
        artifacts=FileArtifactStore(tmp_path),
    )
    assert (result.valid_count, result.invalid_count) == (5, 1)


def test_evt_parser_hidden_variants_and_malformed_rows(tmp_path: Path) -> None:
    hidden_rows = [
        evt_line("2030-12-31T23:59:59+08:00", "类别", "-1.25"),
        "malformed",
        evt_line("", "empty-time", "0"),
        evt_line("x", "y", "z", valid=False),
    ]
    result = execute_evt_summary(
        goal_id=uuid.uuid4(),
        input_text="\n".join(hidden_rows),
        artifacts=FileArtifactStore(tmp_path),
    )
    assert (result.valid_count, result.invalid_count) == (2, 2)
