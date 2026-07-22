import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from regent.domain.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class PublishedStaticApp:
    root: Path
    source_hash: str
    manifest: dict[str, str]
    checks: list[dict[str, object]]


class StaticAppPublisher:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        project_id: uuid.UUID,
        release_id: uuid.UUID,
        files: dict[str, str],
    ) -> PublishedStaticApp:
        allowed = {"index.html", "styles.css", "app.js"}
        if set(files) != allowed:
            raise DomainError(
                ErrorCode.POLICY_DENIED, "static app must contain the frozen file set"
            )
        total = sum(len(value.encode("utf-8")) for value in files.values())
        if total > 750_000:
            raise DomainError(ErrorCode.POLICY_DENIED, "static app exceeds preview size limit")
        combined = "\n".join(files.values()).lower()
        if "http://" in combined or "https://" in combined or "//cdn." in combined:
            raise DomainError(ErrorCode.POLICY_DENIED, "preview cannot load external resources")
        checks: list[dict[str, object]] = [
            {"name": "frozen-file-set", "passed": True},
            {"name": "size-limit", "passed": True, "bytes": total},
            {"name": "external-network-references", "passed": True},
            {
                "name": "semantic-main",
                "passed": "<main" in files["index.html"].lower(),
            },
            {
                "name": "observation-hook",
                "passed": "data-regent-event" in files["index.html"],
            },
        ]
        if not all(bool(item["passed"]) for item in checks):
            raise DomainError(ErrorCode.INVALID_STATE, "generated preview failed verification")
        manifest = {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in sorted(files.items())
        }
        source_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        project_root = (self.root / str(project_id)).resolve()
        target = (project_root / str(release_id)).resolve()
        if self.root not in target.parents:
            raise DomainError(ErrorCode.POLICY_DENIED, "preview path escaped its root")
        project_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{release_id}-", dir=project_root))
        try:
            for name, content in files.items():
                (temporary / name).write_text(content, encoding="utf-8")
            if target.exists():
                current = {
                    name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                    for name in sorted(allowed)
                }
                if current != manifest:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "preview release is immutable")
                shutil.rmtree(temporary)
            else:
                os.replace(temporary, target)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return PublishedStaticApp(target, source_hash, manifest, checks)
