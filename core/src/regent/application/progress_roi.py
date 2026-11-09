"""Progress ROI gate: every token spend must buy measurable delivery progress.

Cross-resume evaluation table + authorize ladder:
  stagnant_streak 0 → allow continue (baseline)
  1 → force self_repair (no empty continue_fix)
  2 → force replan_global
  ≥3 → STOP burn unless human gives substantive new direction

Persisted under goal.metadata_json["progress_roi"].
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

META_PROGRESS_ROI = "progress_roi"
META_PROGRESS_ROI_HISTORY = "progress_roi_history"

Verdict = Literal["progressed", "stagnant", "baseline", "insufficient_data"]
NextAction = Literal["continue_fix", "self_repair", "replan_global", "stop"]

DEFAULT_MIN_TOKENS = 2000
DEFAULT_STAGNANT_STOP = 3
# Consecutive insufficient_data verdicts before fail-closed stop.
DEFAULT_INSUFFICIENT_DATA_STOP = 3

_DELIVERY_GLOBS = (
    "templates/**/*",
    "src/**/*",
    "static/**/*",
    "app/**/*",
    "pages/**/*",
    "public/**/*",
    "*.html",
    "*.css",
    "*.js",
    "*.py",
    "requirements.txt",
    "package.json",
)

_SKIP_NAME_PARTS = {
    ".preview-venv",
    "__pycache__",
    "node_modules",
    ".git",
    ".regent_agent_transcript.json",
}

_GENERIC_CONTINUE_MARKERS = (
    "continue_fix",
    "继续修复",
    "继续",
    "resume",
    "同 session 续跑",
    "同一 agent session",
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stable_hash(parts: list[str]) -> str:
    blob = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()[:16]


def normalize_gap_reasons(reasons: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(reasons or []):
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:240])
    return out[:24]


def gap_set_hash(reasons: list[str] | None) -> str:
    items = sorted(normalize_gap_reasons(reasons))
    return _stable_hash(items) if items else "empty"


def blocking_gap_codes(reasons: list[str] | None) -> list[str]:
    """Extract short stable codes from gap_reasons for table diffs."""
    codes: list[str] = []
    seen: set[str] = set()
    for reason in normalize_gap_reasons(reasons):
        lower = reason.lower()
        code = ""
        for prefix in (
            "preview_product_qa_failed:",
            "delivery-",
            "preview-",
            "forbid-",
            "policy_denied:",
            "tool_call_invalid",
            "budget_exhausted",
        ):
            if prefix in lower:
                # Prefer the delivery-/preview- token when present.
                m = re.search(
                    r"(delivery-[\w-]+|preview-[\w-]+|forbid-[\w-]+|policy_denied:[^|;]+|tool_call_invalid|budget_exhausted)",
                    lower,
                )
                if m:
                    code = m.group(1).strip()[:80]
                break
        if not code:
            # Swarm / outline style free text → first meaningful token
            if "delivery-role-swarm" in lower:
                code = "delivery-role-swarm"
            elif "outline" in lower:
                code = "delivery-product-outline"
            elif "ux-surface" in lower or "ux surface" in lower:
                code = "delivery-ux-surface"
            else:
                code = reason.split(":")[0].strip()[:64].lower()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes[:16]


def compute_workspace_hash(root: str | Path | None) -> str | None:
    """Stable content fingerprint of delivery-facing files under workspace root."""
    if root is None:
        return None
    path = Path(str(root))
    if str(root).startswith("file://"):
        parsed = urlparse(str(root))
        path = Path(parsed.path)
        # Windows file:///C:/... → /C:/...
        if path.as_posix().startswith("/") and len(path.parts) > 1 and path.parts[0] == "/":
            maybe = Path("".join(path.parts[1:2]) + "".join(f"/{p}" for p in path.parts[2:]))
            if maybe.exists() or path.exists():
                path = path if path.exists() else maybe
    if not path.exists() or not path.is_dir():
        return None

    rows: list[str] = []
    files: list[Path] = []
    for pattern in _DELIVERY_GLOBS:
        files.extend(path.glob(pattern))
    # Dedup + stable order
    uniq: dict[str, Path] = {}
    for f in files:
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(path).as_posix()
        except ValueError:
            continue
        if any(part in _SKIP_NAME_PARTS or part.startswith(".") for part in f.parts):
            # allow root-level .files skip; still skip venv/cache
            if any(s in f.parts for s in _SKIP_NAME_PARTS):
                continue
        if rel.startswith(".regent") or "/.preview" in f.as_posix():
            continue
        uniq[rel] = f
    for rel in sorted(uniq):
        f = uniq[rel]
        try:
            data = f.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest()[:12]
        rows.append(f"{rel}:{len(data)}:{digest}")
    if not rows:
        return None
    return _stable_hash(rows)


def load_ledger_from_workspace(root: str | Path | None) -> dict[str, Any]:
    if root is None:
        return {}
    path = Path(str(root))
    if str(root).startswith("file://"):
        path = Path(urlparse(str(root)).path)
    ledger_path = path / ".regent_run_ledger.json"
    if not ledger_path.is_file():
        return {}
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _workspace_root_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in (
        "project_agent_session_workspace_uri",
        "last_recoverable_workspace_uri",
        "workspace_uri",
    ):
        val = str(metadata.get(key) or "").strip()
        if val:
            if val.startswith("file://"):
                return urlparse(val).path or val
            return val
    return None


def build_progress_snapshot(
    metadata: dict[str, Any] | None,
    *,
    workspace_hash: str | None = None,
    ledger: dict[str, Any] | None = None,
    gap_reasons: list[str] | None = None,
    gap_kind: str | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    reasons = normalize_gap_reasons(
        gap_reasons if gap_reasons is not None else list(meta.get("delivery_gap_reasons") or [])
    )
    kind = str(gap_kind or meta.get("delivery_gap_kind") or "")
    led = dict(ledger or {})
    input_tokens = int(led.get("input_tokens") or 0)
    output_tokens = int(led.get("output_tokens") or 0)
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        # Prefer last stamped cycle ledger on metadata if workspace ledger missing.
        prior = dict(meta.get(META_PROGRESS_ROI) or {})
        last_led = dict(prior.get("last_ledger") or {})
        total_tokens = int(last_led.get("total_tokens") or 0)
        input_tokens = int(last_led.get("input_tokens") or input_tokens)
        output_tokens = int(last_led.get("output_tokens") or output_tokens)

    qa_failures = list(meta.get("live_preview_qa_failures") or [])
    swarm_gaps = list(meta.get("delivery_swarm_gaps") or meta.get("swarm_gap_codes") or [])
    preview_chars = meta.get("preview_chars")
    home_visible = meta.get("home_visible_chars") or meta.get("preview_home_visible_chars")

    ws_hash = workspace_hash
    if not ws_hash:
        ws_hash = str(meta.get("agent_loop_workspace_hash") or "") or None
    if not ws_hash:
        root = _workspace_root_from_metadata(meta)
        ws_hash = compute_workspace_hash(root)

    return {
        "at": utc_now_iso(),
        "gap_kind": kind,
        "gap_reasons": reasons,
        "gap_set_hash": gap_set_hash(reasons),
        "blocking_gaps": blocking_gap_codes(reasons),
        "workspace_hash": ws_hash,
        "preview_ready": bool(meta.get("preview_ready")),
        "product_surface_ready": bool(meta.get("product_surface_ready")),
        "preview_chars": int(preview_chars) if preview_chars is not None else None,
        "home_visible_chars": int(home_visible) if home_visible is not None else None,
        "qa_failure_count": len([x for x in qa_failures if str(x).strip()]),
        "swarm_gap_count": len([x for x in swarm_gaps if str(x).strip()]),
        "session_resume_attempts": int(meta.get("session_resume_attempts") or 0),
        "tokens_spent": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "turns": int(led.get("turns") or 0),
    }


def _row(
    dimension: str,
    before: Any,
    after: Any,
    *,
    progressed: bool,
    delta: str | None = None,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "before": before,
        "after": after,
        "delta": delta if delta is not None else ("changed" if before != after else "same"),
        "progressed": bool(progressed),
    }


def evaluate_cycle_roi(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> dict[str, Any]:
    """Compare cycle start vs exit snapshot → verdict + evaluation table."""
    after_s = dict(after or {})
    table: list[dict[str, Any]] = []
    if not before:
        return {
            "verdict": "baseline",
            "progressed": False,
            "had_spend": False,
            "table": table,
            "gains": ["first_cycle_baseline"],
            "misses": [],
            "summary": "首轮基线：尚无对照周期，不判定 stagnant。",
        }

    before_s = dict(before)
    gains: list[str] = []
    misses: list[str] = []

    before_gaps = set(before_s.get("blocking_gaps") or [])
    after_gaps = set(after_s.get("blocking_gaps") or [])
    removed = sorted(before_gaps - after_gaps)
    added = sorted(after_gaps - before_gaps)
    gap_progress = bool(removed) and len(after_gaps) <= len(before_gaps)
    table.append(
        _row(
            "blocking_gaps",
            sorted(before_gaps),
            sorted(after_gaps),
            progressed=gap_progress,
            delta=f"removed={removed};added={added}" if (removed or added) else "same",
        )
    )
    if gap_progress:
        gains.append(f"blocking_gaps_removed:{','.join(removed[:6])}")
    elif before_gaps and before_gaps == after_gaps:
        misses.append("blocking_gaps_unchanged")

    before_hash = str(before_s.get("gap_set_hash") or "")
    after_hash = str(after_s.get("gap_set_hash") or "")
    reasons_shrunk = len(after_s.get("gap_reasons") or []) < len(before_s.get("gap_reasons") or [])
    gap_set_progress = (before_hash != after_hash and reasons_shrunk) or gap_progress
    table.append(
        _row(
            "gap_set_hash",
            before_hash,
            after_hash,
            progressed=gap_set_progress,
        )
    )
    if gap_set_progress:
        gains.append("gap_set_improved")
    elif before_hash and before_hash == after_hash:
        misses.append("gap_set_identical")

    before_kind = str(before_s.get("gap_kind") or "")
    after_kind = str(after_s.get("gap_kind") or "")
    # Kind flip alone is weak; only count if gaps also moved.
    kind_progress = bool(before_kind and after_kind and before_kind != after_kind and gap_set_progress)
    table.append(
        _row(
            "gap_kind",
            before_kind,
            after_kind,
            progressed=kind_progress,
        )
    )
    if kind_progress:
        gains.append(f"gap_kind:{before_kind}->{after_kind}")

    before_ws = str(before_s.get("workspace_hash") or "")
    after_ws = str(after_s.get("workspace_hash") or "")
    ws_changed = bool(before_ws and after_ws and before_ws != after_ws)
    # Workspace change alone is not enough if gaps identical (identical rewrite).
    ws_progress = ws_changed and (gap_set_progress or bool(removed))
    table.append(
        _row(
            "workspace_hash",
            before_ws or None,
            after_ws or None,
            progressed=ws_progress,
            delta="changed" if ws_changed else "same",
        )
    )
    if ws_progress:
        gains.append("workspace_changed_with_gap_relief")
    elif before_ws and after_ws and before_ws == after_ws:
        misses.append("workspace_unchanged")

    for flag in ("preview_ready", "product_surface_ready"):
        b = bool(before_s.get(flag))
        a = bool(after_s.get(flag))
        progressed = (not b) and a
        table.append(_row(flag, b, a, progressed=progressed))
        if progressed:
            gains.append(f"{flag}:false->true")

    for metric in ("qa_failure_count", "swarm_gap_count"):
        b = before_s.get(metric)
        a = after_s.get(metric)
        progressed = isinstance(b, int) and isinstance(a, int) and a < b
        table.append(
            _row(
                metric,
                b,
                a,
                progressed=progressed,
                delta=(f"{b}->{a}" if b is not None and a is not None else "n/a"),
            )
        )
        if progressed:
            gains.append(f"{metric}_reduced")

    for metric in ("home_visible_chars", "preview_chars"):
        b = before_s.get(metric)
        a = after_s.get(metric)
        progressed = isinstance(b, int) and isinstance(a, int) and a > b
        table.append(
            _row(
                metric,
                b,
                a,
                progressed=progressed,
                delta=(f"{b}->{a}" if b is not None and a is not None else "n/a"),
            )
        )
        if progressed:
            gains.append(f"{metric}_increased")

    tokens = int(after_s.get("tokens_spent") or 0)
    # Also count a completed resume lease with zero ledger as spend if resumes advanced.
    resumes_before = int(before_s.get("session_resume_attempts") or 0)
    resumes_after = int(after_s.get("session_resume_attempts") or 0)
    resume_advanced = resumes_after > resumes_before
    had_spend = tokens >= max(1, int(min_tokens)) or (
        resume_advanced and tokens >= max(1, int(min_tokens) // 4)
    )
    # If ledger missing but we clearly resumed and exited again, treat as spend once
    # workspace/gap comparison is available (avoid infinite baseline).
    if not had_spend and resume_advanced and (before_ws or before_hash):
        had_spend = True
        tokens = max(tokens, 1)
    table.append(
        _row(
            "tokens_spent",
            int(before_s.get("tokens_spent") or 0),
            tokens,
            progressed=False,
            delta=str(tokens),
        )
    )

    progressed = bool(gains)
    if not had_spend:
        verdict: Verdict = "insufficient_data"
        summary = "本轮无明显消耗记录，暂不判定 stagnant（避免误伤冷启动）。"
    elif progressed:
        verdict = "progressed"
        summary = "本轮消耗换来可观测进步：" + "；".join(gains[:5])
    else:
        verdict = "stagnant"
        summary = (
            "本轮有消耗但无交付进步（缺口/工作区/预览门禁未改善）。"
            + (" 未过项：" + "；".join(misses[:5]) if misses else "")
        )

    return {
        "verdict": verdict,
        "progressed": progressed and had_spend,
        "had_spend": had_spend,
        "table": table,
        "gains": gains,
        "misses": misses,
        "summary": summary,
        "tokens_spent": tokens,
    }


def next_action_for_streak(
    stagnant_streak: int,
    *,
    stop_at: int = DEFAULT_STAGNANT_STOP,
    insufficient_data_streak: int = 0,
    insufficient_data_stop: int = DEFAULT_INSUFFICIENT_DATA_STOP,
) -> NextAction:
    """Determine next action from stagnant streak AND insufficient_data streak.

    Either streak reaching its threshold triggers stop.
    """
    n = max(0, int(stagnant_streak))
    id_n = max(0, int(insufficient_data_streak))
    # insufficient_data fail-closed: token data missing repeatedly → stop.
    if id_n >= max(1, int(insufficient_data_stop)):
        return "stop"
    if n <= 0:
        return "continue_fix"
    if n == 1:
        return "self_repair"
    if n == 2:
        return "replan_global"
    if n >= max(1, int(stop_at)):
        return "stop"
    return "replan_global"


def format_roi_table_text(table: list[dict[str, Any]], *, limit: int = 8) -> str:
    lines = ["维度 | 基线 | 本轮 | Δ | 进步"]
    for row in list(table or [])[:limit]:
        lines.append(
            f"{row.get('dimension')} | {row.get('before')!s} | {row.get('after')!s} | "
            f"{row.get('delta')} | {'Y' if row.get('progressed') else 'N'}"
        )
    return "\n".join(lines)


def roi_ask_options(next_action: NextAction) -> list[dict[str, str]]:
    if next_action == "stop":
        return [
            {"id": "stop", "label": "停止本轮（不再空转烧额度）"},
            {
                "id": "replan_global",
                "label": "提供全新方向后全局重规划（须写清卡点假设）",
            },
        ]
    return [
        {"id": "self_repair", "label": "定向自修复（按评估表未过项改交付面）"},
        {"id": "replan_global", "label": "全局重分析卡点并重规划"},
        {"id": "stop", "label": "停止本轮"},
    ]


def targeted_repair_constraints(
    *,
    gap_reasons: list[str] | None,
    table: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Concrete must-fix lines derived from ROI misses / gap codes."""
    reasons = normalize_gap_reasons(gap_reasons)
    joined = " ".join(r.lower() for r in reasons)
    codes = blocking_gap_codes(reasons)
    constraints: list[str] = [
        "ROI gate: do not resume with identical approach; change the failing surface.",
        "Every write this cycle must shrink blocking_gaps or raise home-visible product text.",
    ]
    if (
        "delivery-product-outline" in codes
        or "delivery-ux-surface" in codes
        or "outline" in joined
        or "min-visible" in joined
        or "visible" in joined
    ):
        constraints.append(
            "MUST thicken the home/entry page visible text (templates/index.html or "
            "equivalent entry template): put product explanation + sample result sections "
            "directly on the homepage — do not only thicken detail pages."
        )
        constraints.append(
            "Do not spend the cycle only reading files or rewriting identical content."
        )
    if "policy_denied" in joined or "outside frozen plan" in joined:
        constraints.append(
            "Do not create paths outside the frozen generation plan; edit in-plan files only."
        )
        constraints.append(
            "If the needed file is out of plan, choose replan_global instead of inventing paths."
        )
    if "preview-internal-nav" in codes or "404" in joined:
        constraints.append(
            "Fix broken in-app navigation first: every probed href must return HTML 2xx."
        )
    if "delivery-role-swarm" in codes:
        constraints.append(
            "Address Delivery Role Swarm rejects (product/ux): ship a complete journey, not outline-only."
        )
    # Highlight table misses
    for row in list(table or []):
        if row.get("progressed"):
            continue
        dim = str(row.get("dimension") or "")
        if dim in {"blocking_gaps", "gap_set_hash", "workspace_hash", "home_visible_chars"}:
            constraints.append(
                f"ROI miss on {dim}: before={row.get('before')!s} after={row.get('after')!s}."
            )
    # de-dupe
    out: list[str] = []
    seen: set[str] = set()
    for c in constraints:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:16]


def apply_roi_on_exit(
    metadata: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    min_tokens: int = DEFAULT_MIN_TOKENS,
    stagnant_stop: int = DEFAULT_STAGNANT_STOP,
    enforced: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stamp ROI evaluation on gap exit. Returns (metadata, roi_state)."""
    meta = dict(metadata or {})
    prior = dict(meta.get(META_PROGRESS_ROI) or {})
    cycle_start = dict(prior.get("cycle_start") or {}) or None
    evaluation = evaluate_cycle_roi(cycle_start, snapshot, min_tokens=min_tokens)

    streak = int(prior.get("stagnant_streak") or 0)
    insufficient_data_streak = int(prior.get("insufficient_data_streak") or 0)
    if evaluation["verdict"] == "stagnant":
        streak += 1
        insufficient_data_streak = 0  # reset insufficient_data on real stagnant
    elif evaluation["verdict"] == "progressed":
        streak = 0
        insufficient_data_streak = 0
    elif evaluation["verdict"] == "insufficient_data":
        insufficient_data_streak += 1
        # Do NOT reset stagnant_streak — preserve cross-phase pressure.
    # baseline: keep both streaks (don't reset, don't increment)

    next_action = next_action_for_streak(
        streak,
        stop_at=stagnant_stop,
        insufficient_data_streak=insufficient_data_streak,
    ) if enforced else "continue_fix"
    if not enforced:
        next_action = "continue_fix"

    history = list(meta.get(META_PROGRESS_ROI_HISTORY) or [])
    history.append(
        {
            "at": utc_now_iso(),
            "verdict": evaluation["verdict"],
            "stagnant_streak": streak,
            "next_action": next_action,
            "summary": evaluation["summary"],
            "tokens_spent": evaluation.get("tokens_spent") or snapshot.get("tokens_spent"),
            "gains": evaluation.get("gains") or [],
            "misses": evaluation.get("misses") or [],
            "gap_set_hash": snapshot.get("gap_set_hash"),
            "workspace_hash": snapshot.get("workspace_hash"),
        }
    )

    roi = {
        "schema": "regent-progress-roi/v1",
        "updated_at": utc_now_iso(),
        "enforced": bool(enforced),
        "stagnant_streak": streak,
        "insufficient_data_streak": insufficient_data_streak,
        "next_action": next_action,
        "verdict": evaluation["verdict"],
        "summary": evaluation["summary"],
        "table": evaluation.get("table") or [],
        "gains": evaluation.get("gains") or [],
        "misses": evaluation.get("misses") or [],
        "had_spend": bool(evaluation.get("had_spend")),
        "tokens_spent": evaluation.get("tokens_spent") or snapshot.get("tokens_spent"),
        "cycle_start": cycle_start,
        "last_exit_snapshot": snapshot,
        "last_ledger": {
            "input_tokens": snapshot.get("input_tokens") or 0,
            "output_tokens": snapshot.get("output_tokens") or 0,
            "total_tokens": snapshot.get("tokens_spent") or 0,
            "turns": snapshot.get("turns") or 0,
        },
        "repair_constraints": targeted_repair_constraints(
            gap_reasons=list(snapshot.get("gap_reasons") or []),
            table=list(evaluation.get("table") or []),
        ),
    }
    meta[META_PROGRESS_ROI] = roi
    meta[META_PROGRESS_ROI_HISTORY] = history[-12:]
    if snapshot.get("workspace_hash"):
        meta["agent_loop_workspace_hash"] = snapshot["workspace_hash"]
    return meta, roi


def stamp_cycle_start(metadata: dict[str, Any], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark the baseline for the next spend cycle (call after authorized resume)."""
    meta = dict(metadata or {})
    roi = dict(meta.get(META_PROGRESS_ROI) or {})
    snap = dict(snapshot or roi.get("last_exit_snapshot") or build_progress_snapshot(meta))
    # Reset per-cycle token counter baseline in snapshot copy.
    snap = dict(snap)
    snap["tokens_spent"] = 0
    snap["input_tokens"] = 0
    snap["output_tokens"] = 0
    snap["at"] = utc_now_iso()
    roi["cycle_start"] = snap
    roi["cycle_started_at"] = snap["at"]
    roi["updated_at"] = utc_now_iso()
    meta[META_PROGRESS_ROI] = roi
    if snap.get("workspace_hash"):
        meta["agent_loop_workspace_hash"] = snap["workspace_hash"]
    return meta


def human_message_is_substantive(message: str | None) -> bool:
    text = (message or "").strip()
    if len(text) < 24:
        return False
    lowered = text.lower()
    # Strip generic continue boilerplate; require leftover substance.
    residual = lowered
    for marker in _GENERIC_CONTINUE_MARKERS:
        residual = residual.replace(marker.lower(), " ")
    residual = re.sub(r"\s+", " ", residual).strip()
    if len(residual) < 16:
        return False
    # Need at least one concrete hint (path, gap code, or explicit instruction verb).
    concrete = (
        "templates/" in residual
        or "index.html" in residual
        or "replan" in residual
        or "重规划" in text
        or "可见" in text
        or "首页" in text
        or "outline" in residual
        or "ux" in residual
        or "nav" in residual
        or "必须" in text
        or "改" in text
        or "fix" in residual
    )
    return concrete


def authorize_resume_by_roi(
    metadata: dict[str, Any],
    *,
    option_id: str | None,
    human_message: str | None = None,
    enforced: bool = True,
    stagnant_stop: int = DEFAULT_STAGNANT_STOP,
) -> dict[str, Any]:
    """Gate resume authorization.

    Returns dict with:
      allowed: bool
      option_id: effective option
      reason: str
      inject_constraints: list[str]
      reset_streak: bool
      work_plan_replan: bool
      metadata_patch hints via updated fields on caller use of inject_*
    """
    meta = dict(metadata or {})
    roi = dict(meta.get(META_PROGRESS_ROI) or {})
    requested = (option_id or "").strip() or "continue_fix"
    if requested in {"allow_always_session", "allow_once", "approve_plan", "deny"}:
        # Permission / plan flows are not ROI-gated.
        return {
            "allowed": True,
            "option_id": requested,
            "reason": "non_roi_option",
            "inject_constraints": [],
            "reset_streak": False,
            "work_plan_replan": False,
            "force_stop": False,
        }

    if not enforced:
        return {
            "allowed": True,
            "option_id": requested,
            "reason": "roi_not_enforced",
            "inject_constraints": [],
            "reset_streak": False,
            "work_plan_replan": requested == "replan_global",
            "force_stop": False,
        }

    streak = int(roi.get("stagnant_streak") or 0)
    id_streak = int(roi.get("insufficient_data_streak") or 0)
    next_action = str(roi.get("next_action") or next_action_for_streak(
        streak, stop_at=stagnant_stop, insufficient_data_streak=id_streak,
    ))
    constraints = list(roi.get("repair_constraints") or [])
    substantive = human_message_is_substantive(human_message)

    if requested == "stop":
        return {
            "allowed": True,
            "option_id": "stop",
            "reason": "human_stop",
            "inject_constraints": [],
            "reset_streak": False,
            "work_plan_replan": False,
            "force_stop": True,
        }

    if next_action == "stop" and streak >= max(1, int(stagnant_stop)):
        if substantive and requested in {"replan_global", "self_repair", "continue_fix"}:
            return {
                "allowed": True,
                "option_id": "replan_global",
                "reason": "substantive_direction_after_stop",
                "inject_constraints": constraints,
                "reset_streak": True,
                "work_plan_replan": True,
                "force_stop": False,
            }
        return {
            "allowed": False,
            "option_id": "stop",
            "reason": "roi_stop_no_progress",
            "inject_constraints": constraints,
            "reset_streak": False,
            "work_plan_replan": False,
            "force_stop": True,
            "message": (
                "Progress ROI：连续无进步已达停烧阈值。"
                "请提供实质性新方向（具体改哪个缺口/文件）后再选全局重规划；"
                "禁止空 continue_fix。"
            ),
        }

    # Upgrade empty continue_fix according to ladder.
    effective = requested
    if requested == "continue_fix":
        if streak >= 1 or next_action in {"self_repair", "replan_global", "stop"}:
            if substantive:
                # Human gave real direction — allow as self_repair and reset pressure.
                effective = "self_repair" if next_action != "replan_global" else "replan_global"
            else:
                effective = next_action if next_action != "continue_fix" else "self_repair"

    if effective == "stop":
        return {
            "allowed": False,
            "option_id": "stop",
            "reason": "roi_upgraded_to_stop",
            "inject_constraints": constraints,
            "reset_streak": False,
            "work_plan_replan": False,
            "force_stop": True,
            "message": str(roi.get("summary") or "无进步，已停烧。"),
        }

    if effective == "replan_global":
        return {
            "allowed": True,
            "option_id": "replan_global",
            "reason": "roi_replan_global",
            "inject_constraints": constraints
            + [
                "ROI: globally re-analyze the blocker; revise the work plan before more writes.",
                "Do not repeat the previous frozen-plan edit pattern that failed ROI.",
            ],
            "reset_streak": False,
            "work_plan_replan": True,
            "force_stop": False,
        }

    if effective == "self_repair":
        return {
            "allowed": True,
            "option_id": "self_repair",
            "reason": "roi_self_repair",
            "inject_constraints": constraints,
            "reset_streak": False,
            "work_plan_replan": False,
            "force_stop": False,
        }

    return {
        "allowed": True,
        "option_id": effective or "continue_fix",
        "reason": "roi_allow",
        "inject_constraints": [],
        "reset_streak": bool(substantive and streak > 0),
        "work_plan_replan": False,
        "force_stop": False,
    }


def enrich_ask_with_roi(
    ask: dict[str, Any] | None,
    roi: dict[str, Any],
    *,
    enforced: bool = True,
) -> dict[str, Any] | None:
    if ask is None:
        return None
    if not enforced:
        return ask
    out = dict(ask)
    next_action = str(roi.get("next_action") or "continue_fix")
    streak = int(roi.get("stagnant_streak") or 0)
    if streak >= 1 or next_action != "continue_fix":
        out["options"] = roi_ask_options(next_action)  # type: ignore[assignment]
        out["suggested"] = "stop" if next_action == "stop" else (
            next_action if next_action in {"self_repair", "replan_global"} else "self_repair"
        )
        table_txt = format_roi_table_text(list(roi.get("table") or []))
        why = str(out.get("why_blocked") or "")
        summary = str(roi.get("summary") or "")
        out["why_blocked"] = (
            f"{why} Progress ROI streak={streak} next={next_action}. {summary}"
        )[:400]
        q = str(out.get("question") or "")
        out["question"] = (
            f"{q}\n\n【消耗→进步评估】streak={streak} → {next_action}\n{summary}\n{table_txt}"
        )[:800]
        out["ask_type"] = "progress_roi" if streak >= 1 else out.get("ask_type") or "delivery_gap"
    out["progress_roi"] = {
        "stagnant_streak": streak,
        "next_action": next_action,
        "verdict": roi.get("verdict"),
        "tokens_spent": roi.get("tokens_spent"),
    }
    return out
