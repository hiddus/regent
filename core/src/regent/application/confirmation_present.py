"""Helpers that attach ConfirmationRequest envelopes to Console-facing events."""

from __future__ import annotations

from typing import Any, Mapping

from regent.application.confirmation import (
    ConfirmationRequest,
    DecisionPreference,
    RiskLevel,
    build_confirmation,
    preference_timeout_default,
)
from regent.application.decision_policy import ACTION_RISK, load_decision_policy_from_config


def _preference_and_timeout() -> tuple[DecisionPreference, int]:
    try:
        from regent.config import get_settings

        settings = get_settings()
        pref = DecisionPreference(settings.decision_preference)
        timeout = int(settings.confirmation_timeout_seconds)
        return pref, timeout
    except Exception:
        return DecisionPreference.BALANCED, 300


# CD-3.5: default handoff options attached to every human-task confirmation so the
# Console TaskCard can render "bring back an answer" style choices instead of a
# bare allow/deny. Kept intentionally generic (goal-agnostic); callers may extend.
DEFAULT_HANDOFF_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "narrow_scope",
        "label": "缩小范围",
        "cost_hint": "减少本轮改动范围，更快见效但覆盖更少",
    },
    {
        "id": "keep_trying",
        "label": "继续尝试",
        "cost_hint": "保持当前方案继续自动重试，可能消耗更多轮次/时间",
    },
    {
        "id": "stop",
        "label": "停止",
        "cost_hint": "停止自动推进，保持当前状态等待你的下一步指示",
    },
)


def default_handoff_options() -> list[dict[str, str]]:
    return [dict(option) for option in DEFAULT_HANDOFF_OPTIONS]


def default_recovery_options(*, primary_failure: str | None = None) -> list[dict[str, str]]:
    """M4-4: actionable recoveries bound to backend actions."""
    code = (primary_failure or "").upper()
    options = [
        {
            "id": "retry_repair",
            "label": "基于当前草稿继续修复",
            "action": "retry_repair",
        },
        {
            "id": "revise_from_accepted",
            "label": "从已验收快照增量修订",
            "action": "revise_from_accepted_snapshot",
        },
        {
            "id": "stop_and_review",
            "label": "停止并查看诊断产物",
            "action": "open_diagnostics",
        },
    ]
    if code in {"BUDGET_EXHAUSTED", "MODEL_TRUNCATED"}:
        options[0] = {
            "id": "retry_with_budget",
            "label": "提高预算后重试（不晋级当前草稿）",
            "action": "retry_with_budget",
        }
    return options


def action_key_for_task_type(task_type: str) -> str:
    """Map a HumanTask.task_type to the decision-policy action vocabulary.

    Shared by ``confirmation_for_human_task`` (confirmation envelope) and the
    ``/human-tasks/{id}/complete`` route (CD-3.5 always_allow persistence) so the
    two stay in sync.
    """
    action = task_type.lower()
    if action == "release_approval":
        return "release_approval"
    if action == "quality_approval":
        return "quality_approval"
    return "delivery_gap_intervene"


def confirmation_for_human_task(
    *,
    task_type: str,
    summary: str,
    rationale: str | None = None,
    detail: str | None = None,
    prompt: str | None = None,
    extra_rules: list[str] | None = None,
    primary_failure_code: str | None = None,
    budget_summary: str | None = None,
    diagnostic_artifact_uri: str | None = None,
) -> dict[str, Any]:
    """Build confirmation metadata for HUMAN_TASK_REQUIRED / TaskCard."""
    action_key = action_key_for_task_type(task_type)

    pref, timeout = _preference_and_timeout()
    risk = ACTION_RISK.get(action_key, RiskLevel.MEDIUM)
    rules = list(extra_rules or [])
    rules.append(f"task_type:{task_type}")

    # RELEASE_APPROVAL historically uses 24h due_at; keep longer timeout for that type.
    # DELIVERY_GAP_INTERVENE is "need your direction" after long work — never a 5-minute
    # auto-reject trap (balanced preference defaults to deny on short timeouts).
    if action_key == "release_approval":
        timeout = max(timeout, 24 * 3600)
    elif action_key == "delivery_gap_intervene":
        timeout = max(timeout, 7 * 24 * 3600)

    req = build_confirmation(
        action=action_key,
        summary=summary,
        risk_level=risk,
        rationale=rationale
        or "需要你确认后才能继续。",
        on_allow="继续",
        on_deny="停止",
        preference=pref,
        rules_applied=rules,
        timeout_seconds=timeout,
        detail=detail or prompt,
    )
    payload = req.as_dict()
    if action_key == "delivery_gap_intervene":
        # cancel = do not auto-resolve; worker extends due instead of REJECT.
        payload["default_on_timeout"] = "cancel"
        payload["timeout_seconds"] = timeout
    # CD-3.2/CD-3.5: attach default handoff options (narrow_scope/keep_trying/stop)
    # so Console can render option buttons instead of plain allow/deny.
    payload["handoff_options"] = default_handoff_options()
    if primary_failure_code:
        payload["primary_failure_code"] = primary_failure_code
        payload["recovery_options"] = default_recovery_options(
            primary_failure=primary_failure_code
        )
    if budget_summary:
        payload["budget_summary"] = budget_summary
    if diagnostic_artifact_uri:
        payload["diagnostic_artifact_uri"] = diagnostic_artifact_uri
    return payload


def enrich_halt_extra(
    event_type: str,
    stage: str,
    message: str,
    extra: Mapping[str, object] | None,
) -> dict[str, object]:
    """Attach confirmation envelope when surfacing human-required halts."""
    payload: dict[str, object] = dict(extra or {})
    if event_type != "HUMAN_TASK_REQUIRED" and "confirmation" not in payload:
        # Still fold raw errors into detail for other waiting stages when present.
        if payload.get("error") or payload.get("gap_reasons"):
            raw = payload.get("error") or payload.get("gap_reasons")
            payload.setdefault("detail_raw", raw)
        return payload

    if "confirmation" in payload:
        return payload

    task_type = str(payload.get("task_type") or "DELIVERY_GAP_INTERVENE")
    detail_parts: list[str] = []
    if payload.get("error"):
        detail_parts.append(str(payload["error"])[:800])
    if payload.get("gap_reasons"):
        detail_parts.append(f"gap_reasons={payload['gap_reasons']!r}"[:800])
    if payload.get("prompt"):
        detail_parts.append(str(payload["prompt"])[:400])

    payload["confirmation"] = confirmation_for_human_task(
        task_type=task_type,
        summary=_friendly_summary(stage, message),
        rationale=f"阶段 {stage} 需要人工确认",
        detail="; ".join(detail_parts) if detail_parts else message[:500],
        prompt=str(payload.get("prompt") or "") or None,
        extra_rules=[f"stage:{stage}"],
    )
    # Prefer user-facing summary; keep raw under confirmation.detail only.
    payload.pop("error", None)
    return payload


def _friendly_summary(stage: str, message: str) -> str:
    stage_labels = {
        "RELEASE_APPROVAL_REQUIRED": "预览发布需要你批准",
        "DELIVERY_GAP_EXHAUSTED": "自动修复已用尽，需要你介入",
        "BUILD_DELIVERY_GAP_EXHAUSTED": "构建修复已用尽，需要你介入",
        "RESEARCH_MORE_NEEDS_HUMAN": "调研取证已用尽，需要你补充方向",
        "DISCOVERY_NO_SELECT_NEEDS_HUMAN": "发现阶段未选定假设，需要你介入",
    }
    if stage in stage_labels:
        return stage_labels[stage]
    text = (message or "").strip()
    if len(text) > 80:
        return text[:79] + "…"
    return text or f"需要确认：{stage}"


def confirmation_from_policy_or_default(action: str) -> ConfirmationRequest:
    policy = load_decision_policy_from_config()
    pref, timeout = _preference_and_timeout()
    risk = ACTION_RISK.get(action, RiskLevel.MEDIUM)
    return build_confirmation(
        action=action,
        summary=action,
        risk_level=risk,
        rationale="按会话决策偏好生成",
        on_allow="允许",
        on_deny="拒绝",
        preference=policy.preference if policy else pref,
        timeout_seconds=timeout,
        rules_applied=[f"default_on_timeout:{preference_timeout_default(pref).value}"],
    )
