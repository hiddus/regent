"""O4: extension readiness declared | ready | isolated (capability/MCP contract)."""

from __future__ import annotations

from typing import Any, Literal

ExtensionState = Literal["declared", "ready", "isolated"]

EXTENSION_READINESS_SCHEMA = "regent.extension-readiness"
EXTENSION_READINESS_VERSION = 1


def classify_extension(
    *,
    name: str,
    certified: bool = False,
    available: bool = False,
    isolated: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    if isolated or (certified and not available):
        state: ExtensionState = "isolated"
    elif certified and available:
        state = "ready"
    else:
        state = "declared"
    return {
        "name": str(name),
        "state": state,
        "certified": bool(certified),
        "available": bool(available),
        "reason": (reason or None),
    }


def build_extension_readiness(
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [classify_extension(**_norm(e)) for e in (extensions or [])]
    ready = [r["name"] for r in rows if r["state"] == "ready"]
    isolated = [r["name"] for r in rows if r["state"] == "isolated"]
    declared = [r["name"] for r in rows if r["state"] == "declared"]
    return {
        "schema": EXTENSION_READINESS_SCHEMA,
        "v": EXTENSION_READINESS_VERSION,
        "extensions": rows,
        "ready": ready,
        "isolated": isolated,
        "declared": declared,
        "runner_may_invoke": ready,
    }


def runner_may_invoke(readiness: dict[str, Any], name: str) -> bool:
    return str(name) in set(readiness.get("runner_may_invoke") or [])


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(row.get("name") or row.get("id") or "unknown"),
        "certified": bool(row.get("certified", row.get("state") == "CERTIFIED")),
        "available": bool(row.get("available", row.get("ready", False))),
        "isolated": bool(row.get("isolated", False)),
        "reason": row.get("reason"),
    }
