"""O3: content-based turn checkpoint for Primary Agent undo/redo.

Captures pre-images of files mutating tools touch; undo restores only that
turn's files. Diverged/conflicted paths fail closed (oh-my-cli turn-checkpoint).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from regent.application.agent_loop_exit import utc_now_iso

TURN_CHECKPOINT_SCHEMA = "regent.turn-checkpoint"
TURN_CHECKPOINT_VERSION = 1
META_TURN_LOG = "turn_checkpoint_log"

_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class TurnImageCollector:
    """Accumulate first-touch pre-images for one agent turn."""

    images: dict[str, dict[str, Any]] = field(default_factory=dict)

    def capture(self, abs_path: Path) -> None:
        key = str(abs_path.resolve())
        if key in self.images:
            return
        self.images[key] = _read_image(abs_path)

    def size(self) -> int:
        return len(self.images)


def _read_image(abs_path: Path) -> dict[str, Any]:
    try:
        data = abs_path.read_bytes()
        # Prefer text for undo restore; fall back to base64-ish hex marker for binary.
        try:
            text = data.decode("utf-8")
            return {
                "exists": True,
                "sha256": _sha256_text(text),
                "content": text,
                "encoding": "utf-8",
            }
        except UnicodeDecodeError:
            return {
                "exists": True,
                "sha256": _sha256_bytes(data),
                "content": None,
                "encoding": "binary",
                "size": len(data),
            }
    except FileNotFoundError:
        return {"exists": False, "sha256": None, "content": None, "encoding": None}
    except OSError as exc:
        raise RuntimeError(f"cannot read {abs_path}: {exc}") from exc


def _same_image(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(a.get("exists") == b.get("exists") and a.get("sha256") == b.get("sha256"))


def _conflicted(image: dict[str, Any]) -> bool:
    content = image.get("content")
    if not image.get("exists") or not isinstance(content, str):
        return False
    return any(m in content for m in _CONFLICT_MARKERS)


def build_turn_checkpoint(
    collector: TurnImageCollector,
    *,
    workspace_root: Path,
    session_id: str,
    turn_index: int,
    message_count_before: int = 0,
    message_count_after: int | None = None,
) -> dict[str, Any] | None:
    root = workspace_root.resolve()
    files: list[dict[str, Any]] = []
    for abs_key, before in collector.images.items():
        path = Path(abs_key)
        after = _read_image(path)
        if _same_image(before, after):
            continue
        try:
            rel = str(path.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            continue  # outside workspace — skip
        files.append(
            {
                "path": rel,
                "before": {
                    "exists": before.get("exists"),
                    "sha256": before.get("sha256"),
                    "content": before.get("content"),
                    "encoding": before.get("encoding"),
                },
                "after": {
                    "exists": after.get("exists"),
                    "sha256": after.get("sha256"),
                    "content": after.get("content"),
                    "encoding": after.get("encoding"),
                },
            }
        )
    files.sort(key=lambda f: str(f["path"]))
    if not files:
        return None
    after_count = (
        int(message_count_after)
        if message_count_after is not None
        else int(message_count_before)
    )
    checkpoint: dict[str, Any] = {
        "schema": TURN_CHECKPOINT_SCHEMA,
        "v": TURN_CHECKPOINT_VERSION,
        "session_id": str(session_id),
        "turn_index": int(turn_index),
        "message_count_before": int(message_count_before),
        "message_count_after": after_count,
        "files": files,
        "digest": "",
        "at": utc_now_iso(),
    }
    checkpoint["digest"] = _checkpoint_digest(checkpoint)
    return checkpoint


def _checkpoint_digest(checkpoint: dict[str, Any]) -> str:
    manifest = {
        "schema": checkpoint.get("schema"),
        "v": checkpoint.get("v"),
        "session_id": checkpoint.get("session_id"),
        "turn_index": checkpoint.get("turn_index"),
        "files": [
            {
                "path": f.get("path"),
                "before": {"exists": f["before"].get("exists"), "sha256": f["before"].get("sha256")},
                "after": {"exists": f["after"].get("exists"), "sha256": f["after"].get("sha256")},
            }
            for f in checkpoint.get("files") or []
        ],
    }
    return _sha256_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False))


def append_checkpoint_to_metadata(
    metadata: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    meta = dict(metadata or {})
    log = dict(meta.get(META_TURN_LOG) or {})
    checkpoints = list(log.get("checkpoints") or [])
    checkpoints.append(checkpoint)
    log = {
        "schema": TURN_CHECKPOINT_SCHEMA,
        "v": TURN_CHECKPOINT_VERSION,
        "session_id": checkpoint.get("session_id"),
        "checkpoints": checkpoints[-40:],
        "undone_turn_index": None,
        "receipts": list(log.get("receipts") or [])[-40:],
    }
    meta[META_TURN_LOG] = log
    return meta


def plan_undo(
    metadata: dict[str, Any] | None,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    return _plan(metadata, workspace_root=workspace_root, op="undo")


def plan_redo(
    metadata: dict[str, Any] | None,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    return _plan(metadata, workspace_root=workspace_root, op="redo")


def _plan(
    metadata: dict[str, Any] | None,
    *,
    workspace_root: Path,
    op: Literal["undo", "redo"],
) -> dict[str, Any]:
    log = dict(dict(metadata or {}).get(META_TURN_LOG) or {})
    checkpoints = list(log.get("checkpoints") or [])
    if not checkpoints:
        return {"ok": False, "op": op, "reason": f"no turn to {op}", "file_ops": []}
    cp = dict(checkpoints[-1])
    undone = log.get("undone_turn_index")
    if op == "undo" and undone == cp.get("turn_index"):
        return {"ok": False, "op": op, "reason": "the latest turn is already undone", "file_ops": [], "checkpoint": cp}
    if op == "redo" and undone != cp.get("turn_index"):
        return {"ok": False, "op": op, "reason": "the latest turn is not undone", "file_ops": [], "checkpoint": cp}
    root = workspace_root.resolve()
    file_ops: list[dict[str, Any]] = []
    for f in cp.get("files") or []:
        rel = str(f.get("path") or "")
        abs_path = (root / rel).resolve()
        try:
            abs_path.relative_to(root)
        except ValueError:
            return {"ok": False, "op": op, "reason": f"path escapes workspace: {rel}", "file_ops": [], "checkpoint": cp}
        current = _read_image(abs_path)
        expect = f.get("after") if op == "undo" else f.get("before")
        target = f.get("before") if op == "undo" else f.get("after")
        if not _same_image(current, dict(expect or {})):
            return {
                "ok": False,
                "op": op,
                "reason": f"workspace diverged: {rel} changed since the turn",
                "file_ops": [],
                "checkpoint": cp,
            }
        if _conflicted(current):
            return {"ok": False, "op": op, "reason": f"{rel} is conflicted", "file_ops": [], "checkpoint": cp}
        if target and target.get("encoding") == "binary":
            return {
                "ok": False,
                "op": op,
                "reason": f"binary file not supported for {op}: {rel}",
                "file_ops": [],
                "checkpoint": cp,
            }
        if target and target.get("exists"):
            file_ops.append(
                {
                    "path": rel,
                    "action": "restore",
                    "content": target.get("content") or "",
                }
            )
        else:
            file_ops.append({"path": rel, "action": "delete", "content": None})
    return {
        "ok": True,
        "op": op,
        "file_ops": file_ops,
        "checkpoint": cp,
        "digest": cp.get("digest"),
        "turn_index": cp.get("turn_index"),
    }


def apply_plan(
    metadata: dict[str, Any],
    plan: dict[str, Any],
    *,
    workspace_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a successful undo/redo plan. Returns (updated_metadata, receipt)."""
    if not plan.get("ok"):
        raise ValueError(plan.get("reason") or "plan not ok")
    root = workspace_root.resolve()
    # Restores first, then deletes.
    for op in plan.get("file_ops") or []:
        if op.get("action") != "restore":
            continue
        abs_path = (root / str(op["path"])).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = abs_path.with_suffix(abs_path.suffix + ".turn.tmp")
        tmp.write_text(str(op.get("content") or ""), encoding="utf-8")
        tmp.replace(abs_path)
    for op in plan.get("file_ops") or []:
        if op.get("action") == "delete":
            abs_path = (root / str(op["path"])).resolve()
            if abs_path.exists():
                abs_path.unlink()
    meta = dict(metadata or {})
    log = dict(meta.get(META_TURN_LOG) or {})
    cp = dict(plan.get("checkpoint") or {})
    op_name = str(plan.get("op") or "undo")
    if op_name == "undo":
        log["undone_turn_index"] = cp.get("turn_index")
    else:
        log["undone_turn_index"] = None
    receipt = {
        "turn_index": cp.get("turn_index"),
        "op": op_name,
        "digest": cp.get("digest"),
        "at": utc_now_iso(),
        "files": len(plan.get("file_ops") or []),
    }
    receipts = list(log.get("receipts") or [])
    receipts.append(receipt)
    log["receipts"] = receipts[-40:]
    meta[META_TURN_LOG] = log
    return meta, receipt


def format_turn_plan(plan: dict[str, Any]) -> str:
    if not plan.get("ok"):
        return f"Cannot {plan.get('op')}: {plan.get('reason') or 'unknown'}"
    lines = [
        f"{str(plan.get('op')).title()} turn #{plan.get('turn_index')} "
        f"({str(plan.get('digest') or '')[:12]}…)"
    ]
    ops = list(plan.get("file_ops") or [])
    if not ops:
        lines.append(" Files: (none)")
    else:
        lines.append(" Files:")
        for op in ops:
            lines.append(f"  - {op.get('action')} {op.get('path')}")
    return "\n".join(lines)
