import hashlib
import json
import os
import shutil
import stat
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from regent.application.p1_contracts import FileChangeSet, FileMode, FileOperation


class WorkspaceError(RuntimeError):
    pass


class WorkspaceConflictError(WorkspaceError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceLimits:
    max_files: int = 500
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 25_000_000


@dataclass(frozen=True, slots=True)
class WorkspaceCommit:
    workspace_path: Path
    manifest_path: Path
    manifest_hash: str
    source_archive_path: Path
    source_hash: str
    file_count: int
    total_bytes: int


ContentResolver = Callable[[str], bytes]


class WorkspaceWriter:
    def __init__(
        self, root: Path, resolver: ContentResolver, limits: WorkspaceLimits | None = None
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._resolver = resolver
        self._limits = limits or WorkspaceLimits()

    def apply(
        self,
        snapshot_key: str,
        changes: FileChangeSet,
        *,
        base_workspace: Path | None = None,
    ) -> WorkspaceCommit:
        if not snapshot_key or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in snapshot_key
        ):
            raise ValueError("snapshot key contains unsupported characters")
        target = self._root / snapshot_key
        stage = self._root / f".{snapshot_key}.{uuid.uuid4().hex}.tmp"
        try:
            if base_workspace is not None:
                base = self._validated_base(base_workspace)
                self._reject_links(base)
                shutil.copytree(base, stage, symlinks=False)
            else:
                stage.mkdir()
            self._reject_links(stage)
            for change in changes.changes:
                destination = self._destination(stage, change.relative_path)
                self._reject_parent_links(stage, destination)
                if change.operation is FileOperation.CREATE:
                    if destination.exists():
                        raise WorkspaceConflictError(
                            f"CREATE target exists: {change.relative_path}"
                        )
                    self._write(
                        destination, change.content_artifact_uri, change.content_hash, change.mode
                    )
                elif change.operation is FileOperation.REPLACE:
                    self._verify_previous(destination, change.expected_previous_hash)
                    self._write(
                        destination, change.content_artifact_uri, change.content_hash, change.mode
                    )
                else:
                    self._verify_previous(destination, change.expected_previous_hash)
                    destination.unlink()
            self._reject_links(stage)
            manifest, file_count, total_bytes = self._manifest(stage)
            manifest_bytes = json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_path = stage / ".regent-manifest.json"
            manifest_path.write_bytes(manifest_bytes)
            archive_path = stage / ".regent-source.zip"
            self._archive(stage, archive_path)
            source_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            self._sync_tree(stage)
            if target.exists():
                existing = target / ".regent-manifest.json"
                if (
                    existing.is_file()
                    and hashlib.sha256(existing.read_bytes()).hexdigest() == manifest_hash
                ):
                    return self._existing_commit(target, manifest_hash)
                raise WorkspaceConflictError(f"immutable snapshot already exists: {snapshot_key}")
            self._atomic_directory_commit(stage, target)
            self._sync_directory(self._root)
            return WorkspaceCommit(
                target,
                target / manifest_path.name,
                manifest_hash,
                target / archive_path.name,
                source_hash,
                file_count,
                total_bytes,
            )
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def _validated_base(self, base_workspace: Path) -> Path:
        base = base_workspace.resolve()
        if base == self._root or self._root not in base.parents or not base.is_dir():
            raise WorkspaceError("base workspace must be a committed workspace under root")
        return base

    @staticmethod
    def _destination(stage: Path, relative_path: str) -> Path:
        destination = (stage / relative_path).resolve()
        if stage != destination and stage not in destination.parents:
            raise WorkspaceError("path escapes workspace")
        if destination == stage:
            raise WorkspaceError("path must name a file")
        return destination

    @staticmethod
    def _is_link(path: Path) -> bool:
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)

    def _reject_links(self, root: Path) -> None:
        for path in root.rglob("*"):
            if self._is_link(path):
                raise WorkspaceError(f"links and reparse points are forbidden: {path.name}")

    def _reject_parent_links(self, stage: Path, destination: Path) -> None:
        current = destination.parent
        while current != stage:
            if current.exists() and self._is_link(current):
                raise WorkspaceError("parent path is a link or reparse point")
            current = current.parent

    def _write(
        self, destination: Path, uri: str | None, expected_hash: str | None, mode: FileMode
    ) -> None:
        if uri is None or expected_hash is None:
            raise WorkspaceError("content reference is required")
        content = self._resolver(uri)
        if len(content) > self._limits.max_file_bytes:
            raise WorkspaceError("single file quota exceeded")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise WorkspaceError("content hash mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        if mode is FileMode.EXECUTABLE:
            temporary.chmod(0o755)
        os.replace(temporary, destination)

    @staticmethod
    def _verify_previous(destination: Path, expected_hash: str | None) -> None:
        if not destination.is_file() or WorkspaceWriter._is_link(destination):
            raise WorkspaceConflictError("previous regular file does not exist")
        if expected_hash is None:
            raise WorkspaceConflictError("incremental operation requires expected_previous_hash")
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected_hash:
            raise WorkspaceConflictError("previous file hash mismatch")

    def _manifest(self, stage: Path) -> tuple[dict[str, object], int, int]:
        files: list[dict[str, object]] = []
        total = 0
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = path.relative_to(stage).as_posix()
            if relative.startswith(".regent-"):
                continue
            size = path.stat().st_size
            if size > self._limits.max_file_bytes:
                raise WorkspaceError("single file quota exceeded")
            total += size
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": size,
                    "executable": bool(path.stat().st_mode & stat.S_IXUSR),
                }
            )
        if len(files) > self._limits.max_files:
            raise WorkspaceError("file count quota exceeded")
        if total > self._limits.max_total_bytes:
            raise WorkspaceError("total byte quota exceeded")
        return {"schema_version": "workspace-manifest-v1", "files": files}, len(files), total

    @staticmethod
    def _archive(stage: Path, destination: Path) -> None:
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(
                item for item in stage.rglob("*") if item.is_file() and item != destination
            ):
                relative = path.relative_to(stage).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                archive.writestr(info, path.read_bytes())

    @staticmethod
    def _atomic_directory_commit(stage: Path, target: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(stage, target)
                return
            except PermissionError:
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))

    @staticmethod
    def _sync_tree(stage: Path) -> None:
        for path in (item for item in stage.rglob("*") if item.is_file()):
            with path.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        WorkspaceWriter._sync_directory(stage)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _existing_commit(self, target: Path, manifest_hash: str) -> WorkspaceCommit:
        manifest = json.loads((target / ".regent-manifest.json").read_bytes())
        files = manifest["files"]
        archive = target / ".regent-source.zip"
        return WorkspaceCommit(
            target,
            target / ".regent-manifest.json",
            manifest_hash,
            archive,
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            len(files),
            sum(int(item["size"]) for item in files),
        )
