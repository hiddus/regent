import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    uri: str
    content_hash: str
    size: int


class ArtifactConflictError(RuntimeError):
    pass


def local_artifact_path(uri: str) -> Path:
    """Resolve and validate an immutable local file artifact URI."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("sandbox accepts local immutable artifacts only")
    raw = parsed.path[1:] if len(parsed.path) > 2 and parsed.path[2] == ":" else parsed.path
    path = Path(raw).resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("sandbox input artifact is invalid")
    return path


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, goal_id: uuid.UUID, relative_path: str, content: bytes) -> StoredArtifact:
        destination = self._destination(goal_id, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()

        if destination.exists():
            existing = destination.read_bytes()
            existing_digest = hashlib.sha256(existing).hexdigest()
            if existing_digest != digest:
                raise ArtifactConflictError(f"immutable artifact already exists at {relative_path}")
            return StoredArtifact(
                uri=destination.as_uri(),
                content_hash=digest,
                size=len(existing),
            )

        temporary = destination.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredArtifact(
            uri=destination.as_uri(),
            content_hash=digest,
            size=len(content),
        )

    def read(self, goal_id: uuid.UUID, relative_path: str) -> bytes:
        return self._destination(goal_id, relative_path).read_bytes()

    def find(self, relative_path: str) -> bytes | None:
        """Read the first matching artifact across scopes without exposing storage layout."""
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError("artifact path must be a safe relative file path")
        for scope_root in self.root.iterdir():
            if not scope_root.is_dir() or scope_root.is_symlink():
                continue
            candidate = (scope_root / relative).resolve()
            if scope_root.resolve() not in candidate.parents:
                continue
            if candidate.is_file() and not candidate.is_symlink():
                return candidate.read_bytes()
        return None

    def _destination(self, goal_id: uuid.UUID, relative_path: str) -> Path:
        goal_root = (self.root / str(goal_id)).resolve()
        destination = (goal_root / relative_path).resolve()
        if destination != goal_root and goal_root not in destination.parents:
            raise ValueError("artifact path escapes goal workspace")
        if destination == goal_root:
            raise ValueError("artifact path must name a file")
        return destination
