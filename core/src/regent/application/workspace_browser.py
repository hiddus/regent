"""Console-facing workspace tree / file / diff helpers."""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.config import get_settings
from regent.infrastructure.models import GoalModel

_MAX_FILE_BYTES = 200_000
_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".regent",
    ".pytest_cache",
}


def _safe_rel(path: str) -> str:
    cleaned = path.replace("\\", "/").lstrip("/")
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("path traversal denied")
    return "/".join(parts)


def _path_from_uri_or_raw(uri: str) -> Path | None:
    raw = str(uri or "").strip()
    if not raw:
        return None
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        elif os.name == "nt" and parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)
    return Path(raw)


async def resolve_project_workspace(
    sessions: async_sessionmaker[AsyncSession],
    project_id,
) -> Path | None:
    """Resolve a project-owned workspace. Never fall back to global agentic sandboxes."""
    settings = get_settings()
    root = Path(settings.workspace_root)
    candidates: list[Path] = []
    snapshot_id: str | None = None

    async with sessions() as session:
        goal = await session.scalar(
            select(GoalModel)
            .where(GoalModel.app_project_id == project_id)
            .order_by(GoalModel.created_at.desc())
        )
        if goal is not None and isinstance(goal.metadata_json, dict):
            meta = goal.metadata_json
            # 1) Accepted snapshot
            accepted = meta.get("last_accepted_workspace")
            if isinstance(accepted, dict):
                uri = str(accepted.get("uri") or "")
                p = _path_from_uri_or_raw(uri)
                if p is not None:
                    candidates.append(p)
                snap = accepted.get("snapshot_id")
                if snap:
                    snapshot_id = str(snap)
                    candidates.append(root / "accepted_workspace_snapshots" / str(snap))
            # 2) Recoverable / diagnostic snapshot (failure handoff)
            recoverable = meta.get("last_recoverable_workspace")
            if isinstance(recoverable, dict) and recoverable.get("snapshot_id"):
                snapshot_id = str(recoverable["snapshot_id"])
                candidates.append(
                    root / "recoverable_workspace_snapshots" / snapshot_id
                )
            diag = meta.get("diagnostic_delivery")
            if isinstance(diag, dict):
                resume = diag.get("resume") if isinstance(diag.get("resume"), dict) else {}
                base = resume.get("base_snapshot_id")
                if base:
                    snapshot_id = str(base)
                    candidates.append(
                        root / "recoverable_workspace_snapshots" / str(base)
                    )
            draft = meta.get("last_recoverable_workspace_uri") or meta.get(
                "last_good_draft_uri"
            )
            if isinstance(draft, str) and draft.strip():
                p = _path_from_uri_or_raw(draft)
                if p is not None:
                    candidates.append(p)
            # 3) Preview workspaces
            endpoint = meta.get("last_preview_endpoint")
            if isinstance(endpoint, str):
                match = re.search(
                    r"/preview/([0-9a-fA-F-]{36})/([0-9a-fA-F-]{36})",
                    endpoint,
                )
                if match:
                    candidates.append(root / "previews" / match.group(1) / match.group(2))
                    candidates.append(root / "previews" / match.group(1))

    candidates.extend(
        [
            root / "previews" / str(project_id),
            root / str(project_id),
        ]
    )
    # Intentionally NO global agentic/* mtime fallback (cross-project leak risk).

    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and any(resolved.iterdir()):
            return resolved
    return None


def list_tree(root: Path, *, limit: int = 400) -> list[dict[str, Any]]:
    root = root.resolve()
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(entries) >= limit:
            break
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _SKIP_DIR_NAMES for part in rel.parts):
            continue
        if any(part.startswith(".regent") for part in rel.parts):
            continue
        if path.is_dir():
            entries.append(
                {
                    "path": rel.as_posix(),
                    "name": path.name,
                    "kind": "dir",
                }
            )
        elif path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            entries.append(
                {
                    "path": rel.as_posix(),
                    "name": path.name,
                    "kind": "file",
                    "size": size,
                }
            )
    return entries


def read_text_file(root: Path, rel_path: str) -> dict[str, Any]:
    root = root.resolve()
    rel = _safe_rel(rel_path)
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("path traversal denied")
    if not target.is_file():
        raise FileNotFoundError(rel)
    data = target.read_bytes()
    truncated = False
    if len(data) > _MAX_FILE_BYTES:
        data = data[:_MAX_FILE_BYTES]
        truncated = True
    if b"\x00" in data[:4096]:
        raise ValueError("binary file rejected")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("non-utf8 file rejected") from exc
    return {
        "path": rel,
        "content": content,
        "truncated": truncated,
        "size": target.stat().st_size,
    }


def diff_trees(from_root: Path, to_root: Path, *, max_files: int = 40) -> str:
    from_root = from_root.resolve()
    to_root = to_root.resolve()
    from_files: dict[str, Path] = {}
    to_files: dict[str, Path] = {}
    for p in from_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(from_root).as_posix()
        if any(part.startswith(".regent") for part in rel.split("/")):
            continue
        from_files[rel] = p
    for p in to_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(to_root).as_posix()
        if any(part.startswith(".regent") for part in rel.split("/")):
            continue
        to_files[rel] = p
    keys = sorted(set(from_files) | set(to_files))[:max_files]
    chunks: list[str] = []
    for key in keys:
        a = from_files.get(key)
        b = to_files.get(key)
        a_lines = (
            a.read_text(encoding="utf-8", errors="replace").splitlines() if a else []
        )
        b_lines = (
            b.read_text(encoding="utf-8", errors="replace").splitlines() if b else []
        )
        diff = list(
            difflib.unified_diff(
                a_lines, b_lines, fromfile=f"a/{key}", tofile=f"b/{key}", lineterm=""
            )
        )
        if diff:
            chunks.extend(diff)
            chunks.append("")
    return "\n".join(chunks)[:80_000]
