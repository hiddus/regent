"""Sandbox workspace tools for the agentic generation loop."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from regent.agent.file_manifest import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    WorkspaceManifest,
    build_workspace_manifest,
)
from regent.agent.types import ToolCall, ToolSpec

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_files",
        description="List files under the sandbox workspace (relative paths).",
        parameters={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Relative directory to list (default '.').",
                }
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="glob",
        description="Glob files under the workspace (e.g. '**/*.py', 'tests/**/*.ts').",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern relative to root."},
                "limit": {"type": "integer", "description": "Max matches (default 200)."},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="grep",
        description="Search file contents with a regex; returns matching lines.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "description": "Optional file glob filter."},
                "limit": {"type": "integer"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="read_file",
        description="Read a text file from the sandbox workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="write_file",
        description="Create or overwrite a text file in the sandbox workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="edit_file",
        description=(
            "Exact text replacement in a file. old_text must match uniquely, "
            "or provide expected_sha256 of the current file contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="read_artifact",
        description="Read an offloaded artifact by URI or workspace-relative ref (hash verified).",
        parameters={
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "path": {"type": "string", "description": "Relative path under .regent/artifacts/"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="run_command",
        description=(
            "Run a shell command inside the isolated sandbox (pip install, pytest, "
            "python -m compileall, curl smoke checks). Working directory is the workspace root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="todo_write",
        description=(
            "Step 0 / work plan: create or replace the durable checklist BEFORE "
            "write_file/edit_file/run_command on multi-step work. "
            "Prefer ≥3 concrete steps; keep at most one status=in_progress. "
            "Alias of plan upsert (persists to ExecutionPlan)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "cancelled"],
                            },
                            "owner_agent_id": {
                                "type": "string",
                                "description": "Optional owner; use subagent-<key> when delegating.",
                            },
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="plan_list",
        description="Read the current in-session work plan checklist (prevents forgetting steps).",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="plan_update",
        description=(
            "Update one work-plan item status (pending/in_progress/completed/cancelled). "
            "Use after finishing a step; keep a single in_progress item."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                },
                "content": {"type": "string"},
                "owner_agent_id": {"type": "string"},
            },
            "required": ["id", "status"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="delegate_plan_item",
        description=(
            "Delegate one work-plan item to an isolated sub-agent. "
            "Requires an existing todo id; marks owner + in_progress, then runs the subagent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Todo / plan item id"},
                "acceptance_notes": {"type": "string"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="ask_user_question",
        description=(
            "Ask the human a structured question and wait for an answer before continuing. "
            "Use when requirements are ambiguous, a risky choice is needed, or plan direction "
            "must be confirmed. Do not guess — call this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["id", "label"],
                    },
                },
                "suggested": {"type": "string"},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="submit",
        description=(
            "Declare the workspace ready for independent verification. "
            "Required before any ReleaseCandidate; stopping without submit is incomplete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What was delivered."},
            },
            "additionalProperties": False,
        },
    ),
]


class CommandSandbox(Protocol):
    async def exec_in_workspace(
        self,
        workspace: Path,
        shell_command: str,
        *,
        timeout_seconds: int = 60,
        allow_network: bool = False,
    ) -> str: ...


_NETWORK_PREFIXES: tuple[str, ...] = ("pip ", "curl ")


@dataclass(slots=True)
class SnapshotFilesReport:
    files: dict[str, str]
    skipped: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    considered: int = 0
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_FILE_BYTES
    integrity_ok: bool = True
    manifest: WorkspaceManifest | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_count": len(self.files),
            "files": sorted(self.files.keys()),
            "skipped": list(self.skipped),
            "truncated": self.truncated,
            "considered": self.considered,
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "integrity_ok": self.integrity_ok,
            "manifest": self.manifest.as_dict() if self.manifest else None,
        }


class WorkspaceToolkit:
    """Bounded file/command tools confined to a sandbox workspace root."""

    def __init__(
        self,
        root: Path,
        *,
        allowed_commands_prefix: tuple[str, ...] | None = None,
        command_sandbox: CommandSandbox | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.todos: list[dict[str, str]] = []
        self.recent_writes: list[str] = []
        self.last_snapshot_report: SnapshotFilesReport | None = None
        self.submitted: bool = False
        self.submit_summary: str = ""
        self.artifact_index: dict[str, dict[str, Any]] = {}
        self._command_sandbox = command_sandbox
        self._allowed = allowed_commands_prefix or (
            "pip ",
            "python ",
            "python3 ",
            "pytest",
            "curl ",
            "ls",
            "dir",
            "type ",
            "cat ",
            "wc ",
            "head ",
            "tail ",
            "find ",
            "rg ",
            "grep ",
        )

    def resolve(self, relative: str) -> Path:
        normalized = relative.replace("\\", "/").lstrip("/")
        if not normalized or normalized in {".", "./"}:
            return self.root
        parts = Path(normalized).parts
        if any(p in {"", ".", ".."} for p in parts) or parts[0] in {".git"}:
            raise ValueError(f"illegal path: {relative}")
        path = (self.root / normalized).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"path escapes workspace: {relative}")
        return path

    def list_tree(self, directory: str = ".", *, limit: int = 200) -> list[str]:
        base = self.resolve(directory)
        if not base.exists():
            return []
        entries: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                continue
            rel = path.relative_to(self.root).as_posix()
            entries.append(rel + ("/" if path.is_dir() else ""))
            if len(entries) >= limit:
                break
        return entries

    def glob_files(self, pattern: str, *, limit: int = 200) -> list[str]:
        matches: list[str] = []
        for path in sorted(self.root.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                matches.append(path.relative_to(self.root).as_posix())
            if len(matches) >= limit:
                break
        return matches

    def grep_files(
        self, pattern: str, *, file_glob: str = "**/*", limit: int = 50
    ) -> list[dict[str, Any]]:
        rx = re.compile(pattern)
        hits: list[dict[str, Any]] = []
        for path in sorted(self.root.glob(file_glob)):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    hits.append(
                        {
                            "path": path.relative_to(self.root).as_posix(),
                            "line": lineno,
                            "text": line[:240],
                        }
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    def read_text(self, relative: str, *, max_chars: int = 40_000) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"
        return text

    def write_text(self, relative: str, content: str) -> str:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(self.root).as_posix()
        if rel not in self.recent_writes:
            self.recent_writes.append(rel)
        return rel

    def edit_file(
        self,
        relative: str,
        *,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        current = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if expected_sha256 and expected_sha256 != digest:
            raise ValueError(
                f"edit conflict: expected_sha256 mismatch for {relative}"
            )
        count = current.count(old_text)
        if count == 0:
            raise ValueError(f"edit failed: old_text not found in {relative}")
        if count > 1:
            raise ValueError(
                f"edit failed: old_text matches {count} times in {relative}; must be unique"
            )
        updated = current.replace(old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        rel = path.relative_to(self.root).as_posix()
        if rel not in self.recent_writes:
            self.recent_writes.append(rel)
        return f"edited {rel}"

    def register_artifact(
        self, *, ref: str, text: str, sha256: str | None = None
    ) -> dict[str, Any]:
        digest = sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
        art_dir = self.root / ".regent" / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", ref)[:80]
        path = art_dir / f"{safe}-{digest[:12]}.txt"
        path.write_text(text, encoding="utf-8")
        meta = {
            "ref": ref,
            "path": path.relative_to(self.root).as_posix(),
            "sha256": digest,
            "bytes": len(text.encode("utf-8")),
            "retention": "run",
        }
        self.artifact_index[ref] = meta
        self.artifact_index[meta["path"]] = meta
        (art_dir / "index.json").write_text(
            json.dumps(self.artifact_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return meta

    def read_artifact(self, *, uri: str | None = None, path: str | None = None) -> str:
        key = (uri or path or "").strip()
        if not key:
            raise ValueError("read_artifact requires uri or path")
        meta = self.artifact_index.get(key)
        if meta is None and path:
            meta = self.artifact_index.get(path)
        rel = str((meta or {}).get("path") or path or "")
        if not rel:
            raise FileNotFoundError(f"dangling artifact ref: {key}")
        text = self.read_text(rel, max_chars=200_000)
        if meta and meta.get("sha256"):
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest != meta["sha256"]:
                raise ValueError(f"artifact hash mismatch for {rel}")
        return text

    def snapshot_files(
        self,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> dict[str, str]:
        return self.snapshot_files_report(max_files=max_files, max_bytes=max_bytes).files

    def snapshot_files_report(
        self,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> SnapshotFilesReport:
        manifest = build_workspace_manifest(
            self.root,
            max_files=max_files,
            max_file_bytes=max_bytes,
            max_total_bytes=max_total_bytes,
        )
        skipped = [
            {"path": e.path, "reason": e.reason, "bytes": e.bytes}
            for e in manifest.entries
            if not e.included and e.reason
        ]
        report = SnapshotFilesReport(
            files=dict(manifest.files),
            skipped=skipped,
            truncated=manifest.truncated,
            considered=len(manifest.entries),
            max_files=max_files,
            max_bytes=max_bytes,
            integrity_ok=manifest.integrity_ok,
            manifest=manifest,
        )
        self.last_snapshot_report = report
        return report

    async def run_command(self, command: str, *, timeout_seconds: int = 60) -> str:
        cmd = command.strip()
        if not cmd:
            raise ValueError("empty command")
        if not any(cmd == p.strip() or cmd.startswith(p) for p in self._allowed):
            raise ValueError(
                f"command not allowed: {cmd!r}. Allowed prefixes: {', '.join(self._allowed)}"
            )
        if self._command_sandbox is None:
            raise RuntimeError(
                "WorkspaceToolkit has no command_sandbox; refusing host subprocess "
                "(Tech-Spec §13.8 / CD-0.1)"
            )
        allow_network = any(cmd.startswith(p) for p in _NETWORK_PREFIXES)
        return await self._command_sandbox.exec_in_workspace(
            self.root,
            cmd,
            timeout_seconds=timeout_seconds,
            allow_network=allow_network,
        )

    async def execute(self, call: ToolCall) -> str:
        try:
            if call.name == "list_files":
                directory = str(call.arguments.get("directory") or ".")
                return json.dumps(self.list_tree(directory), ensure_ascii=False)
            if call.name == "glob":
                pattern = str(call.arguments["pattern"])
                limit = int(call.arguments.get("limit") or 200)
                return json.dumps(self.glob_files(pattern, limit=limit), ensure_ascii=False)
            if call.name == "grep":
                return json.dumps(
                    self.grep_files(
                        str(call.arguments["pattern"]),
                        file_glob=str(call.arguments.get("glob") or "**/*"),
                        limit=int(call.arguments.get("limit") or 50),
                    ),
                    ensure_ascii=False,
                )
            if call.name == "read_file":
                return self.read_text(str(call.arguments["path"]))
            if call.name == "write_file":
                rel_path = str(call.arguments["path"])
                content = str(call.arguments.get("content") or "")
                if rel_path.endswith(".py"):
                    try:
                        compile(content, rel_path, "exec")
                    except SyntaxError as exc:
                        return (
                            f"ERROR: SyntaxError in {rel_path} line {exc.lineno}: "
                            f"{exc.msg}. Fix quotes/escaping (e.g. embed data via "
                            f"json.loads/json.dumps) and rewrite the file."
                        )
                rel = self.write_text(rel_path, content)
                return f"wrote {rel}"
            if call.name == "edit_file":
                return self.edit_file(
                    str(call.arguments["path"]),
                    old_text=str(call.arguments["old_text"]),
                    new_text=str(call.arguments["new_text"]),
                    expected_sha256=(
                        str(call.arguments["expected_sha256"])
                        if call.arguments.get("expected_sha256")
                        else None
                    ),
                )
            if call.name == "read_artifact":
                return self.read_artifact(
                    uri=str(call.arguments["uri"]) if call.arguments.get("uri") else None,
                    path=str(call.arguments["path"]) if call.arguments.get("path") else None,
                )
            if call.name == "run_command":
                return await self.run_command(
                    str(call.arguments["command"]),
                    timeout_seconds=int(call.arguments.get("timeout_seconds") or 60),
                )
            if call.name == "todo_write":
                from regent.application.work_plan import normalize_single_in_progress

                todos = call.arguments.get("todos") or []
                if not isinstance(todos, list):
                    raise ValueError("todos must be a list")
                raw = [
                    {
                        "id": str(item.get("id") or ""),
                        "content": str(item.get("content") or ""),
                        "status": str(item.get("status") or "pending"),
                        **(
                            {"owner_agent_id": str(item["owner_agent_id"])}
                            if item.get("owner_agent_id")
                            else {}
                        ),
                        **(
                            {"dependencies": list(item.get("dependencies") or [])}
                            if item.get("dependencies")
                            else {}
                        ),
                    }
                    for item in todos
                    if isinstance(item, dict)
                ]
                self.todos = normalize_single_in_progress(raw)
                return json.dumps(self.todos, ensure_ascii=False)
            if call.name == "plan_list":
                return json.dumps(self.todos, ensure_ascii=False)
            if call.name == "plan_update":
                from regent.application.work_plan import normalize_single_in_progress

                item_id = str(call.arguments.get("id") or "")
                if not item_id:
                    raise ValueError("id is required")
                found = False
                updated: list[dict[str, Any]] = []
                for item in self.todos:
                    row = dict(item)
                    if str(row.get("id") or "") == item_id:
                        found = True
                        row["status"] = str(call.arguments.get("status") or row.get("status"))
                        if call.arguments.get("content"):
                            row["content"] = str(call.arguments["content"])
                        if call.arguments.get("owner_agent_id") is not None:
                            owner = call.arguments.get("owner_agent_id")
                            if owner:
                                row["owner_agent_id"] = str(owner)
                            else:
                                row.pop("owner_agent_id", None)
                    updated.append(row)
                if not found:
                    raise ValueError(f"plan item not found: {item_id}")
                self.todos = normalize_single_in_progress(updated)
                return json.dumps(self.todos, ensure_ascii=False)
            if call.name == "delegate_plan_item":
                # Handled by AgentRunner (needs ChatProvider); toolkit only validates id.
                item_id = str(call.arguments.get("id") or "")
                if not item_id:
                    raise ValueError("id is required")
                match = next(
                    (t for t in self.todos if str(t.get("id") or "") == item_id),
                    None,
                )
                if match is None:
                    raise ValueError(f"plan item not found: {item_id}")
                return json.dumps(
                    {
                        "delegated": True,
                        "id": item_id,
                        "content": match.get("content"),
                        "acceptance_notes": call.arguments.get("acceptance_notes") or "",
                    },
                    ensure_ascii=False,
                )
            if call.name == "ask_user_question":
                from regent.application.agent_control import AskUserRequiredError
                from regent.application.agent_loop_exit import build_ask_envelope

                question = str(call.arguments.get("question") or "").strip()
                if not question:
                    raise ValueError("question is required")
                raw_opts = call.arguments.get("options") or []
                options: list[dict[str, str]] = []
                if isinstance(raw_opts, list):
                    for item in raw_opts:
                        if isinstance(item, dict) and item.get("id"):
                            options.append(
                                {
                                    "id": str(item["id"]),
                                    "label": str(item.get("label") or item["id"]),
                                }
                            )
                suggested = str(call.arguments.get("suggested") or "") or (
                    options[0]["id"] if options else "continue_fix"
                )
                raise AskUserRequiredError(
                    question,
                    options=options,
                    envelope=build_ask_envelope(
                        question=question,
                        why_blocked="Agent 通过 ask_user_question 请求确认。",
                        options=options or None,
                        suggested=suggested,
                        ask_type="ask_user",
                    ),
                )
            if call.name == "submit":
                self.submitted = True
                self.submit_summary = str(call.arguments.get("summary") or "")
                return json.dumps(
                    {"submitted": True, "summary": self.submit_summary},
                    ensure_ascii=False,
                )
            return f"unknown tool: {call.name}"
        except Exception as exc:  # noqa: BLE001 — tool errors become model-visible results
            return f"ERROR: {type(exc).__name__}: {exc}"
