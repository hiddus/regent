import hashlib
import os
from pathlib import Path

import pytest
from regent.application.p1_contracts import FileChange, FileChangeSet, FileOperation
from regent.infrastructure.workspace_writer import (
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceLimits,
    WorkspaceWriter,
)


def change(
    path: str,
    content: bytes,
    operation: FileOperation = FileOperation.CREATE,
    previous: str | None = None,
) -> tuple[FileChange, dict[str, bytes]]:
    uri = f"memory://{path}"
    return (
        FileChange(
            relative_path=path,
            operation=operation,
            content_artifact_uri=uri,
            content_hash=hashlib.sha256(content).hexdigest(),
            expected_previous_hash=previous,
            rationale="test",
        ),
        {uri: content},
    )


def test_create_commit_and_idempotent_replay(tmp_path: Path) -> None:
    item, content = change("app/main.py", b"print('ok')\n")
    writer = WorkspaceWriter(tmp_path, content.__getitem__)
    changes = FileChangeSet(changes=[item], generator_ref="fake", prompt_version="v1")
    first = writer.apply("snapshot-1", changes)
    replay = writer.apply("snapshot-1", changes)
    assert (first.workspace_path / "app/main.py").read_bytes() == b"print('ok')\n"
    assert first.manifest_hash == replay.manifest_hash
    assert first.source_hash == replay.source_hash
    assert first.file_count == 1


def test_incremental_replace_checks_previous_hash(tmp_path: Path) -> None:
    first, first_content = change("main.py", b"one")
    writer = WorkspaceWriter(tmp_path, lambda uri: {**first_content, "memory://new": b"two"}[uri])
    base = writer.apply(
        "base", FileChangeSet(changes=[first], generator_ref="g", prompt_version="v1")
    )
    replacement = FileChange(
        relative_path="main.py",
        operation=FileOperation.REPLACE,
        content_artifact_uri="memory://new",
        content_hash=hashlib.sha256(b"two").hexdigest(),
        expected_previous_hash=hashlib.sha256(b"one").hexdigest(),
        rationale="replace",
    )
    result = writer.apply(
        "next",
        FileChangeSet(changes=[replacement], generator_ref="g", prompt_version="v1"),
        base_workspace=base.workspace_path,
    )
    assert (result.workspace_path / "main.py").read_bytes() == b"two"
    bad = replacement.model_copy(update={"expected_previous_hash": "0" * 64})
    with pytest.raises(WorkspaceConflictError):
        writer.apply(
            "bad",
            FileChangeSet(changes=[bad], generator_ref="g", prompt_version="v1"),
            base_workspace=base.workspace_path,
        )


def test_hash_and_quota_fail_without_committing(tmp_path: Path) -> None:
    item, content = change("large.txt", b"large")
    wrong = item.model_copy(update={"content_hash": "0" * 64})
    writer = WorkspaceWriter(tmp_path, content.__getitem__, WorkspaceLimits(max_file_bytes=3))
    with pytest.raises(WorkspaceError):
        writer.apply(
            "failed", FileChangeSet(changes=[wrong], generator_ref="g", prompt_version="v1")
        )
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_immutable_snapshot_rejects_different_manifest(tmp_path: Path) -> None:
    first, first_content = change("one.txt", b"one")
    second, second_content = change("two.txt", b"two")
    content = {**first_content, **second_content}
    writer = WorkspaceWriter(tmp_path, content.__getitem__)
    writer.apply("same", FileChangeSet(changes=[first], generator_ref="g", prompt_version="v1"))
    with pytest.raises(WorkspaceConflictError):
        writer.apply(
            "same", FileChangeSet(changes=[second], generator_ref="g", prompt_version="v1")
        )


def test_symlink_in_base_is_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    base = root / "base"
    base.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    try:
        os.symlink(outside, base / "link.txt")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    item, content = change("safe.txt", b"safe")
    writer = WorkspaceWriter(root, content.__getitem__)
    with pytest.raises(WorkspaceError):
        writer.apply(
            "next",
            FileChangeSet(changes=[item], generator_ref="g", prompt_version="v1"),
            base_workspace=base,
        )
