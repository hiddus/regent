"""Sandbox workspace tools for the agentic generation loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

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
        name="run_command",
        description=(
            "Run a shell command inside the isolated sandbox (pip install, pytest, "
            "python -m compileall, curl smoke checks). Working directory is the workspace root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60, max 120).",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="todo_write",
        description="Update the in-session todo list for long-running delivery work.",
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
                        },
                        "required": ["id", "content", "status"],
                    },
                }
            },
            "required": ["todos"],
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


# CD-7.5 / N-4: pip/curl may request network, but DockerSandboxDriver fail-closes
# without REGENT_DEPENDENCY_EGRESS_PROXY (no bare bridge).
_NETWORK_PREFIXES: tuple[str, ...] = ("pip ", "curl ")


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
        if any(p in {"", ".", ".."} for p in parts) or parts[0] in {".git", ".regent"}:
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

    def snapshot_files(self, *, max_files: int = 80, max_bytes: int = 200_000) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in {
                ".py",
                ".html",
                ".htm",
                ".css",
                ".js",
                ".json",
                ".md",
                ".txt",
                ".toml",
                ".yml",
                ".yaml",
            } and path.name.lower() != "requirements.txt":
                continue
            rel = path.relative_to(self.root).as_posix()
            raw = path.read_bytes()
            if len(raw) > max_bytes:
                continue
            try:
                files[rel] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(files) >= max_files:
                break
        return files

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
                entries = self.list_tree(directory)
                return json.dumps(entries, ensure_ascii=False)
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
            if call.name == "run_command":
                return await self.run_command(
                    str(call.arguments["command"]),
                    timeout_seconds=int(call.arguments.get("timeout_seconds") or 60),
                )
            if call.name == "todo_write":
                todos = call.arguments.get("todos") or []
                if not isinstance(todos, list):
                    raise ValueError("todos must be a list")
                self.todos = [
                    {
                        "id": str(item.get("id") or ""),
                        "content": str(item.get("content") or ""),
                        "status": str(item.get("status") or "pending"),
                    }
                    for item in todos
                    if isinstance(item, dict)
                ]
                return json.dumps(self.todos, ensure_ascii=False)
            return f"unknown tool: {call.name}"
        except Exception as exc:  # noqa: BLE001 — tool errors become model-visible results
            return f"ERROR: {type(exc).__name__}: {exc}"
