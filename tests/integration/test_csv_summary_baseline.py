import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest
from regent.application.csv_summary import execute_csv_summary
from regent.infrastructure.artifact_store import (
    ArtifactConflictError,
    FileArtifactStore,
)

FIXTURE = Path("tests/fixtures/csv_summary_baseline/orders.csv")
EXPECTED = Path("tests/fixtures/csv_summary_baseline/expected.json")


def test_csv_summary_baseline_is_exact_hashed_and_idempotent(tmp_path: Path) -> None:
    input_path = tmp_path / "fixtures" / "orders.csv"
    input_path.parent.mkdir()
    shutil.copyfile(FIXTURE, input_path)
    original = input_path.read_bytes()
    goal_id = uuid.uuid4()
    store = FileArtifactStore(tmp_path / "artifacts")

    first = execute_csv_summary(
        goal_id=goal_id,
        input_path=input_path,
        artifacts=store,
    )
    second = execute_csv_summary(
        goal_id=goal_id,
        input_path=input_path,
        artifacts=store,
    )

    output = store.read(goal_id, "output/summary.json")
    assert json.loads(output) == json.loads(EXPECTED.read_bytes())
    assert input_path.read_bytes() == original
    assert first.input_hash == hashlib.sha256(original).hexdigest()
    assert first.output_hash == second.output_hash == hashlib.sha256(output).hexdigest()
    assert first.row_count == 4
    assert first.valid_count == 3
    assert first.invalid_count == 1
    assert first.total_amount == "30.00"
    assert len(list((tmp_path / "artifacts" / str(goal_id) / "output").iterdir())) == 1


def test_artifact_store_rejects_path_escape(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="escapes"):
        store.put(uuid.uuid4(), "../outside.txt", b"no")


def test_artifact_store_rejects_mutating_existing_artifact(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    goal_id = uuid.uuid4()
    store.put(goal_id, "output/result.json", b"first")
    with pytest.raises(ArtifactConflictError, match="immutable"):
        store.put(goal_id, "output/result.json", b"second")
