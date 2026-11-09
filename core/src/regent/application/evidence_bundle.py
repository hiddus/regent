"""O4: portable COMPLETE/STOP evidence bundle (digest-only; optional verify)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from regent.application.agent_loop_exit import get_exit, utc_now_iso

EVIDENCE_BUNDLE_SCHEMA = "regent.evidence-bundle"
EVIDENCE_BUNDLE_VERSION = 1


def build_evidence_bundle(
    metadata: dict[str, Any] | None,
    *,
    goal_id: str | None = None,
    review_checks: list[dict[str, Any]] | None = None,
    extra_artifact_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    exit_row = get_exit(meta) or {}
    bundle = dict(exit_row.get("result_bundle") or {})
    ask = dict(exit_row.get("ask_envelope") or meta.get("pending_agent_loop_ask") or {})
    artifacts = {
        "draft_uri": exit_row.get("draft_uri") or meta.get("last_good_draft_uri"),
        "preview_url": bundle.get("preview_url") or meta.get("last_preview_endpoint"),
        "artifact_uri": bundle.get("artifact_uri"),
    }
    hashes = dict(extra_artifact_hashes or {})
    for k, v in list(artifacts.items()):
        if v and k not in hashes:
            hashes[k] = _sha256_text(str(v))
    payload = {
        "schema": EVIDENCE_BUNDLE_SCHEMA,
        "v": EVIDENCE_BUNDLE_VERSION,
        "at": utc_now_iso(),
        "goal_id": goal_id,
        "exit_kind": exit_row.get("exit_kind"),
        "stop_reason": exit_row.get("stop_reason"),
        "summary": bundle.get("summary") or ask.get("question"),
        "open_items": list(bundle.get("open_items") or [])[:12],
        "change_points": list(bundle.get("change_points") or [])[:8],
        "evidence_summary": bundle.get("evidence_summary"),
        "review_checks": list(review_checks or [])[:40],
        "artifacts": artifacts,
        "artifact_hashes": hashes,
        "trust_level": dict(meta.get("last_trust_posture") or {}).get("level"),
        "quarantine_active": bool(meta.get("quarantine_active")),
    }
    payload["digest"] = _bundle_digest(payload)
    return payload


def verify_evidence_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    row = dict(bundle or {})
    claimed = str(row.get("digest") or "")
    recomputed = _bundle_digest(row)
    ok = bool(claimed) and claimed == recomputed
    return {
        "ok": ok,
        "claimed": claimed or None,
        "recomputed": recomputed,
        "schema": row.get("schema"),
        "v": row.get("v"),
        "exit_kind": row.get("exit_kind"),
    }


def _bundle_digest(bundle: dict[str, Any]) -> str:
    manifest = {k: v for k, v in bundle.items() if k != "digest"}
    return _sha256_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False, default=str))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
