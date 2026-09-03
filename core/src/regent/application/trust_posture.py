"""O1: trust posture — compose sandbox × approval × Ask/Act × capability readiness."""

from __future__ import annotations

from typing import Any, Literal

from regent.application.agent_control import (
    get_execution_mode,
    session_always_tools,
)
from regent.application.agent_loop_exit import utc_now_iso

TrustLevel = Literal["restricted", "standard", "elevated"]

TRUST_POSTURE_SCHEMA = "regent.trust-posture"
TRUST_POSTURE_VERSION = 1


def permission_impact(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured impact preview for permission cards (oh-my-cli permission-impact)."""
    args = dict(arguments or {})
    paths: list[str] = []
    for key in ("path", "file", "target", "filename", "file_path"):
        raw = args.get(key)
        if raw:
            paths.append(str(raw)[:240])
    if isinstance(args.get("paths"), list):
        paths.extend(str(p)[:240] for p in args["paths"][:12] if p)
    command = str(args.get("command") or args.get("cmd") or "")[:400]
    name = str(tool_name or "")
    if name in {"write_file", "edit_file"}:
        effect_class = "workspace_mutate"
        command_class = "file_write"
    elif name == "run_command":
        effect_class = "shell_side_effect"
        command_class = _classify_shell(command)
    else:
        effect_class = "other"
        command_class = "tool"
    return {
        "tool": name,
        "paths": paths[:12],
        "command": command or None,
        "command_class": command_class,
        "effect_class": effect_class,
        "network": "none",
    }


def _classify_shell(command: str) -> str:
    c = (command or "").strip().lower()
    if not c:
        return "empty"
    if any(x in c for x in ("rm ", "del ", "rmdir", "format ", "mkfs")):
        return "destructive"
    if any(x in c for x in ("curl ", "wget ", "ssh ", "scp ", "nc ")):
        return "egress"
    if any(x in c for x in ("pytest", "npm test", "vitest", "cargo test")):
        return "test"
    if any(x in c for x in ("npm ", "pip ", "uv ", "pnpm ", "yarn ")):
        return "package"
    return "shell"


def build_trust_posture(
    metadata: dict[str, Any] | None,
    *,
    sandbox_enforced: bool | None = None,
    workspace_trusted: bool | None = None,
    capability_ready: list[str] | None = None,
    capability_isolated: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only redacted posture audit for a Goal/Session run."""
    meta = dict(metadata or {})
    mode = get_execution_mode(meta)
    always = sorted(session_always_tools(meta))
    quarantine = bool(meta.get("quarantine_active"))
    sandbox = True if sandbox_enforced is None else bool(sandbox_enforced)
    trusted = True if workspace_trusted is None else bool(workspace_trusted)
    ready = list(capability_ready or [])[:20]
    isolated = list(capability_isolated or [])[:20]

    if quarantine or not trusted or not sandbox:
        level: TrustLevel = "restricted"
    elif mode == "act" and always:
        level = "elevated"
    elif mode == "act":
        level = "elevated"
    else:
        level = "standard"

    # Act cannot widen past untrusted/unsandboxed (oh-my-cli: yolo ⊄ trust).
    act_widens = mode == "act" and trusted and sandbox and not quarantine

    return {
        "schema": TRUST_POSTURE_SCHEMA,
        "v": TRUST_POSTURE_VERSION,
        "at": utc_now_iso(),
        "level": level,
        "execution_mode": mode,
        "workspace_trusted": trusted,
        "sandbox_enforced": sandbox,
        "quarantine_active": quarantine,
        "session_always_tools": always,
        "act_may_skip_tool_prompt": act_widens,
        "capability_ready": ready,
        "capability_isolated": isolated,
        "summary": _summary(level, mode, trusted, sandbox, quarantine),
    }


def _summary(
    level: TrustLevel,
    mode: str,
    trusted: bool,
    sandbox: bool,
    quarantine: bool,
) -> str:
    parts = [f"level={level}", f"mode={mode}"]
    if not trusted:
        parts.append("workspace_untrusted")
    if not sandbox:
        parts.append("sandbox_off")
    if quarantine:
        parts.append("quarantined")
    return "; ".join(parts)
