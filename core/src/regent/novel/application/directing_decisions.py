"""Director decision bridge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.directing_calls import _command_id, _input_version, _save
from regent.novel.application.directing_contracts import DecisionRequestSpec

# Runtime is injected by the caller module after its authority module is loaded.
from regent.novel.application.directing_runtime import _RUNTIME, _runtime_state
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel

DECISION_DEADLINE_BY_IMPACT = {
    "LOW": timedelta(hours=6),
    "MEDIUM": timedelta(hours=24),
    "HIGH": timedelta(hours=72),
}
DECISION_DEADLINE_DEFAULT = timedelta(hours=24)


def _pending_user_decision(run: ChapterRunModel) -> dict[str, Any] | None:
    """取回尚未被导演消费的用户裁决（A-02）。

    选择只在上下文里躺着不算「改变了创作」：导演必须真的读到它、据此行动，
    然后把它标记为已消费。返回完整语义，不是 option_id。
    """
    resolutions = (run.generation_context or {}).get("decision_resolutions") or []
    for item in reversed(resolutions):
        if not item.get("consumed"):
            return dict(item)
    return None


def _consume_user_decision(run: ChapterRunModel, production: dict[str, Any]) -> None:
    """标记裁决已被导演执行，并清除 pending 挂起。

    消费后同一个裁决不会第二次进入导演上下文——否则导演会反复「执行」同一个
    决定，或就同一件事再问用户一次。
    """
    context = dict(run.generation_context or {})
    resolutions = list(context.get("decision_resolutions") or [])
    for item in reversed(resolutions):
        if not item.get("consumed"):
            item["consumed"] = True
            break
    context["decision_resolutions"] = resolutions
    run.generation_context = context
    production.pop("pending_decision", None)


async def _request_user_decision(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    spec: DecisionRequestSpec,
    phase: str,
) -> None:
    """导演请求用户裁决：命令校验通过后落持久化请求并让章节等待（P1-2）。"""
    from regent.novel.application import decisions as decision_svc

    take = production["takes"][-1]
    _RUNTIME.validate(
        director_command(
            CommandKind.REQUEST_USER_DECISION,
            command_id=_command_id(production, run, phase),
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            evidence=[spec.trigger_summary],
            payload={
                "why_human": spec.why_human,
                "options": [o.option_id for o in spec.options],
                "default_option_id": spec.default_option_id,
            },
        ),
        _runtime_state(production, run, take),
    )
    node_id = spec.node_id or str(
        (run.generation_context.get("target_node") or {}).get("node_id", "")
    )
    await decision_svc.create_decision(
        session,
        owner_id=work.owner_id,
        work_id=work.id,
        chapter_no=run.chapter_no,
        run_id=run.id,
        node_id=node_id,
        trigger_summary=spec.trigger_summary,
        why_human=spec.why_human,
        options=[option.model_dump(mode="json") for option in spec.options],
        default_option_id=spec.default_option_id,
        impact_level=spec.impact_level,
        impact_horizon_chapters=spec.impact_horizon_chapters,
        deadline=datetime.now(UTC)
        + DECISION_DEADLINE_BY_IMPACT.get(spec.impact_level.upper(), DECISION_DEADLINE_DEFAULT),
    )
    production["pending_decision"] = {
        "phase": phase,
        "scene_index": production["scene_index"],
        "take_no": take["take_no"],
        "trigger_summary": spec.trigger_summary,
    }
    _save(run, production)
