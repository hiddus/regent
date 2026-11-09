"""Accepted workspace snapshot pointer (M2 addendum / M4-2).

On successful verification, atomically write an immutable snapshot and record
its URI + content hash. REVISE clones from this pointer — not from failure drafts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from regent.agent.file_manifest import build_workspace_manifest


@dataclass(frozen=True, slots=True)
class AcceptedWorkspaceSnapshot:
    snapshot_id: str
    uri: str
    content_hash: str
    manifest_hash: str
    profile_hash: str
    verification_hash: str
    created_at: str
    root: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "manifest_hash": self.manifest_hash,
            "profile_hash": self.profile_hash,
            "verification_hash": self.verification_hash,
            "created_at": self.created_at,
        }


def write_accepted_workspace_snapshot(
    workspace: Path,
    dest_root: Path,
    *,
    profile_hash: str,
    verification_hash: str,
) -> AcceptedWorkspaceSnapshot:
    """Copy workspace into an immutable snapshot directory (fail if dest exists)."""
    workspace = workspace.resolve()
    dest_root = dest_root.resolve()
    snapshot_id = str(uuid.uuid4())
    target = dest_root / "accepted_workspace_snapshots" / snapshot_id
    if target.exists():
        raise FileExistsError(f"snapshot already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    # Copy tree excluding agent diagnostics.
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(workspace).as_posix()
        if rel.startswith(".regent"):
            continue
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)

    manifest = build_workspace_manifest(target)
    if not manifest.integrity_ok:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("accepted snapshot rejected: manifest integrity failed")

    meta = {
        "snapshot_id": snapshot_id,
        "manifest": manifest.as_dict(),
        "manifest_hash": manifest.content_hash,
        "profile_hash": profile_hash,
        "verification_hash": verification_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (target / ".regent_accepted_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    content_hash = hashlib.sha256(
        json.dumps(
            {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sorted(manifest.files.items())},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return AcceptedWorkspaceSnapshot(
        snapshot_id=snapshot_id,
        uri=target.resolve().as_uri(),
        content_hash=content_hash,
        manifest_hash=manifest.content_hash,
        profile_hash=profile_hash,
        verification_hash=verification_hash,
        created_at=str(meta["created_at"]),
        root=target,
    )


def write_recoverable_workspace_snapshot(
    workspace: Path,
    dest_root: Path,
    *,
    reason: str = "generation_failed",
    include_diagnostics: bool = False,
) -> str:
    """Best-effort copy of a failed/partial workspace for REVISE warm-start.

    Unlike accepted snapshots, integrity failures are tolerated — recoverable
    drafts are better than cold starts even when incomplete.

    When ``include_diagnostics`` is True, copy ``.regent_*`` sidecars
    (budget/ledger/transcript) so DiagnosticDelivery can hand them to the user.
    """
    workspace = workspace.resolve()
    dest_root = dest_root.resolve()
    snapshot_id = str(uuid.uuid4())
    target = dest_root / "recoverable_workspace_snapshots" / snapshot_id
    target.mkdir(parents=True, exist_ok=False)
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(workspace).as_posix()
        if rel.startswith(".regent") and not include_diagnostics:
            continue
        # Always skip nested accepted/recoverable meta noise.
        if path.name in {
            ".regent_accepted_meta.json",
            ".regent_recoverable_meta.json",
        }:
            continue
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
    meta = {
        "snapshot_id": snapshot_id,
        "reason": reason,
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(workspace),
    }
    (target / ".regent_recoverable_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target.resolve().as_uri()


def clone_accepted_snapshot(snapshot_uri: str, dest: Path) -> Path:
    """Clone an accepted snapshot into dest for REVISE / incremental generation."""
    import os
    from urllib.parse import unquote, urlparse

    raw = str(snapshot_uri).strip()
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        # Windows: file:///C:/... → /C:/... must drop the leading slash.
        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        elif os.name == "nt" and parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        src = Path(path)
    else:
        src = Path(raw)
    if not src.is_dir():
        raise FileNotFoundError(f"accepted snapshot not found: {snapshot_uri}")
    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            ".regent_accepted_meta.json",
            ".regent_recoverable_meta.json",
        ),
    )
    return dest


def verify_promotion_hashes(
    *,
    manifest_hash: str,
    profile_hash: str,
    verification_hash: str,
    preview_deployment_hash: str,
    expected: dict[str, str],
) -> list[str]:
    """M4-3: reject promotion when any hash diverges."""
    errors: list[str] = []
    mapping = {
        "manifest_hash": manifest_hash,
        "profile_hash": profile_hash,
        "verification_hash": verification_hash,
        "preview_deployment_hash": preview_deployment_hash,
    }
    for key, actual in mapping.items():
        want = str(expected.get(key) or "")
        if not want or want != actual:
            errors.append(f"{key}_mismatch: expected={want!r} actual={actual!r}")
    return errors
