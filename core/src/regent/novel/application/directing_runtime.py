"""Director command runtime."""

# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy
from typing import Any

from regent.novel.application.directing_budget import (
    MAX_CALLS,
    MAX_COST_MINOR,
)
from regent.novel.application.directing_budget import (
    effective_call_cap as _budget_call_cap,
)
from regent.novel.application.directing_budget import (
    effective_cost_cap as _budget_cost_cap,
)
from regent.novel.application.directing_budget import (
    remaining_calls as _budget_remaining_calls,
)
from regent.novel.application.directing_protocol import (
    PROTOCOL_BEAT,
    production_protocol,
)
from regent.novel.application.directing_types import (
    MAX_TAKES,
)
from regent.novel.application.runtime import (
    CommandRuntime,
    RuntimeLimits,
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.errors import (
    CommandRejected,
    ProductionStopped,
)
from regent.novel.domain.states import SceneRunState

_effective_call_cap = _budget_call_cap
_effective_cost_cap = _budget_cost_cap
_remaining_calls = _budget_remaining_calls
from regent.novel.application.directing_calls import (
    _command_id,
    _input_version,
)
from regent.novel.application.directing_cast import (
    _brief_issues,
)
from regent.novel.application.directing_contracts import (
    SceneBrief,
)
from regent.novel.application.directing_issue_ledger import (
    _rewrite_blocked_by_ledger,
)

MAX_TURNS = 4
MAX_REVISIONS = 1
MAX_VALIDATION_REPAIRS = 1
VALIDATE_RESERVE_REWRITE = 4
VALIDATE_RESERVE_RENDER = 3
VALIDATE_RESERVE_CALLS = VALIDATE_RESERVE_REWRITE

_RUNTIME = CommandRuntime(
    RuntimeLimits(
        max_calls=MAX_CALLS,
        max_takes=MAX_TAKES,
        max_turns=MAX_TURNS,
        max_revisions=MAX_REVISIONS,
        max_cost_minor=MAX_COST_MINOR,
    )
)
_TAKE_COMMANDS = {
    "CONTINUE": CommandKind.CONTINUE_SCENE,
    "RETAKE": CommandKind.RETAKE_SCENE,
    "RENDER": CommandKind.RENDER_SCENE,
}
_PROSE_COMMANDS = {
    "RETAKE": CommandKind.RETAKE_SCENE,
    "REWRITE": CommandKind.REWRITE_PROSE,
    "ACCEPT": CommandKind.ACCEPT_SCENE,
}


def _validate_reserve_for(action: str) -> int:
    name = str(action or "").upper()
    if "REWRITE" in name:
        return VALIDATE_RESERVE_REWRITE
    if "RENDER" in name:
        return VALIDATE_RESERVE_RENDER
    return VALIDATE_RESERVE_CALLS


def _ensure_validate_call_reserve(production: dict[str, Any], *, action: str) -> None:
    need = _validate_reserve_for(action)
    remaining = _remaining_calls(production)
    if remaining < need:
        raise CommandRejected(
            action,
            f"剩余调用 {remaining} 次不足预留后续额度 {need}；请 RETAKE、请求裁决或等待授权后续作",
        )


def _extend_events(take: dict[str, Any], events: list[Any]) -> None:
    """把本轮事件并入 take；**同一句陈述**已入场的不再重复入场。

    结算请求会把已有事件一并给模型看（它必须基于既有事实判定结果），模型于是
    把看过的事件原样回吐，``extend`` 就把它们重复累加：真机样本里一个 6 事件的
    场景跑完 3 个节拍变成 22 条，其中三组完全相同。重复事件会喂出重复正文、
    触发规则冲突、迫使导演重演，最后卡死整章——而根因只是一句 ``extend``。
    """
    seen = {str(e.get("statement", "")) for e in take["events"]}
    for event in events:
        dumped = event.model_dump(mode="json")
        key = str(dumped.get("statement", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        take["events"].append(dumped)


def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:
    """校验角色引用；违规即判死（调用方负责在判死前给过自修机会）。"""
    issues = _brief_issues(brief, cast)
    if issues:
        raise ProductionStopped("场景包含重复或未定义的角色：" + "；".join(issues))


def _runtime_state(production: dict[str, Any], run: Any, take: dict[str, Any]) -> RuntimeState:
    """从生产状态构造只读快照，供 Runtime 校验命令。"""
    return RuntimeState(
        scene_state=take.get("scene_state") or SceneRunState.BRIEFED.value,
        artifact=take.get("artifact", ""),
        input_version=_input_version(run),
        turn=take["turn"],
        takes_used=_live_takes_for_scene(production, int(production.get("scene_index", 0) or 0)),
        revisions=take["revisions"],
        calls_used=production.get("call_count", 0),
        reserved_minor=production.get("reserved_minor", 0),
        cast=frozenset(production["cast"]),
        has_rule_issues=bool(take.get("rule_issues")),
        has_hard_failure=take.get("validation", {}).get("passed") is False,
        has_visible_events=any(e.get("reader_visible") for e in take["events"]),
        scene_index=int(production.get("scene_index", 0) or 0),
        take_no=int(take.get("take_no", 1) or 1),
        has_prose=bool(take.get("content")),
        call_cap=_effective_call_cap(production),
        cost_cap=_effective_cost_cap(production),
    )


def _legal_action_report(
    production: dict[str, Any],
    run: Any,
    take: dict[str, Any],
    phase: str,
    result: Any,
    commands: dict[str, Any],
    actors: list[dict[str, Any]],
) -> list[str]:
    """枚举每个动作：直接可执行 / 补参后可执行 / 禁止。

    缺 revised_brief 的 RETAKE 属于「补参后可执行」，不能与 takes 用尽等硬禁止混为一谈。
    """
    takes_used = _live_takes_for_scene(production, int(production.get("scene_index", 0) or 0))
    lines: list[str] = [
        (
            f"剩余预算：revisions={MAX_REVISIONS - int(take.get('revisions', 0))}；"
            f"takes={MAX_TAKES - takes_used}；"
            f"calls≈{_remaining_calls(production)}；"
            f"cost_minor_cap={_effective_cost_cap(production)}"
        )
    ]
    hard = take.get("validation") or {}
    if hard.get("passed") is False:
        missing = [
            f"{item.get('requirement_id')}:{item.get('status')}"
            for item in (hard.get("requirements") or [])
            if item.get("status") in {"missing", "contradicted"}
        ]
        if missing:
            lines.append("当前硬失败项：" + "；".join(missing[:12]))
        elif hard.get("issues"):
            lines.append("当前硬失败项：" + "；".join(str(x) for x in hard["issues"][:8]))
    must_retake = any(
        "须 RETAKE" in str(x)
        for x in list(take.get("revision_conflict_notices") or [])
        + list((hard.get("issues") if isinstance(hard, dict) else None) or [])
    )
    if must_retake:
        lines.append("裁决约束：已结算事件冲突，只能 RETAKE（禁止 ACCEPT / REWRITE）")

    for action in commands:
        probe = result.model_copy(update={"action": action})
        try:
            _validate_action(production, run, take, phase, probe, commands, actors)
        except ProductionStopped as exc:
            if action == "RETAKE" and _retake_would_pass_with_brief(
                production, run, take, phase, result, commands, actors
            ):
                lines.append(
                    f"- {action}：补参后可执行（须提供与现行 brief 不同的 revised_brief；{exc}）"
                )
            else:
                lines.append(f"- {action}：禁止（{exc}）")
        else:
            lines.append(f"- {action}：直接可执行")
    return lines


def _retake_would_pass_with_brief(
    production: dict[str, Any],
    run: Any,
    take: dict[str, Any],
    phase: str,
    result: Any,
    commands: dict[str, Any],
    actors: list[dict[str, Any]],
) -> bool:
    """合成一份与现行 brief 不同的 revised_brief，探测 RETAKE 是否仅缺参数。"""
    current = dict(take.get("brief") or {})
    if not current:
        current = {
            "purpose": "重演探测",
            "setting": "场景",
            "conflict": "冲突",
            "exit_condition": "出口",
            "actors": actors or [{"persona": "主角", "objective": "推进", "instruction": "行动"}],
            "narrative": {
                "viewpoint": "主角",
                "distance": "近",
                "style": "克制",
                "reader_effect": "关注",
                "disclosure_rule": "不泄密",
            },
        }
    altered = dict(current)
    altered["purpose"] = str(current.get("purpose") or "") + "·重演修正"
    # SceneBrief 校验需要 actors 为对象；用 model 构造
    try:
        brief = SceneBrief.model_validate(altered)
    except Exception:
        return False
    probe = result.model_copy(update={"action": "RETAKE", "revised_brief": brief})
    try:
        _validate_action(production, run, take, phase, probe, commands, actors)
    except ProductionStopped:
        return False
    return True


def _validate_action(
    production: dict[str, Any],
    run: Any,
    take: dict[str, Any],
    phase: str,
    result: Any,
    commands: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[RuntimeState, tuple[str, str]]:
    """构造并执行一次命令校验；被拒时抛 ``CommandRejected``。

    WATCH_TAKE 与 WATCH_PROSE 的命令载荷结构完全一致，所以只留一份：两边都
    要能**在自修循环里**反复校验，不能只在落库后校验一次。
    """
    state = _runtime_state(production, run, take)
    command = director_command(
        commands[result.action],
        command_id=_command_id(production, run, phase),
        input_version=_input_version(run),
        scene_index=production["scene_index"],
        take_no=take["take_no"],
        evidence=result.evidence,
        actors=actors,
        revised_brief=(
            result.revised_brief.model_dump(mode="json")
            if result.revised_brief is not None
            else None
        ),
    )
    target = _RUNTIME.validate(command, state)
    kind = commands[result.action]
    # BQ-2：无进展的同类 REWRITE 止损；进入 RENDER/REWRITE 前预留 VALIDATE。
    if kind is CommandKind.REWRITE_PROSE:
        must_retake = any(
            "须 RETAKE" in str(x)
            for x in list(take.get("revision_conflict_notices") or [])
            + list((take.get("validation") or {}).get("issues") or [])
        )
        if must_retake:
            raise CommandRejected(
                str(kind.value),
                "已结算事件冲突：改变已结算内容须 RETAKE，REWRITE 不可执行",
                command.fingerprint(),
            )
        blocked = _rewrite_blocked_by_ledger(take)
        if blocked:
            raise CommandRejected(str(kind.value), blocked, command.fingerprint())
        _ensure_validate_call_reserve(production, action=str(kind.value))
    elif kind is CommandKind.RENDER_SCENE:
        _ensure_validate_call_reserve(production, action=str(kind.value))
    # RETAKE 必须给出**确实修改**的场景指令，否则等于重演白演。真机死法：
    # 给出 revised_brief 但字段全和现行 brief 一样，被 _retake 直接判死——而
    # 那条检查不在自修通道里，模型拿不到反馈。提到这里走同一条 command_check
    # 通道，模型能看到"哪些字段没改"。
    if (
        kind is CommandKind.RETAKE_SCENE
        and result.revised_brief is not None
        and take.get("brief") is not None
    ):
        proposed = command.payload.get("revised_brief") or {}
        current = take["brief"]
        same = sorted(
            k
            for k in proposed
            if isinstance(proposed.get(k), (str, list, dict)) and proposed.get(k) == current.get(k)
        )
        if len(same) == len(proposed):
            # 全部字段都等于现行 brief——"未改变"才成立；只改了一项就不算。
            raise CommandRejected(
                str(kind.value),
                "重演未改变场景指令；与现行 brief 相同的字段：" + "、".join(same),
                command.fingerprint(),
            )
    return state, target


def _coerce_illegal_action(
    phase: str, result: Any, take: dict[str, Any], production: dict[str, Any] | None = None
) -> list[str]:
    """把**注定被 Runtime 拒绝**的动作换成唯一还能执行的那个，并返回留痕说明。

    真机死法：节拍用尽时导演仍选 CONTINUE。这是**可判定**的非法——不必再问模型
    一次（temperature=0 的入参不变重试会以近乎确定的方式产出同一个非法动作），
    而节拍用尽后 RENDER 是唯一还能执行的路径。换的是**路径**不是**内容**：留下的
    instruction 仍然是导演自己给的，且换动作会留痕入库，不静默。

    另一类——「存在硬失败时导演仍选 ACCEPT」——**故意不在这里收束**。
    ``test_director_cannot_override_failed_independent_validation`` 编码了一条安全
    属性：独立核验没过的东西，导演不能绕过去。把它悄悄改成 REWRITE 等于替产品
    做了个决定，而这个决定该由人拍：是让章直接停，还是允许重写。见归档文档。
    """
    notes: list[str] = []
    if phase == "WATCH_TAKE" and result.action == "CONTINUE":
        if MAX_TURNS - int(take.get("turn", 0)) - 1 <= 0:
            result.action = "RENDER"
            notes.append("节拍已用尽，CONTINUE 不可执行，按 RENDER 收束本场")
    if production is not None and phase == "WATCH_PROSE" and result.action == "RETAKE":
        scene = int(take.get("scene_index", production.get("scene_index", 0)) or 0)
        if _live_takes_for_scene(production, scene) >= MAX_TAKES:
            if int(take.get("revisions", 0) or 0) < MAX_REVISIONS:
                result.action = "REWRITE"
                notes.append("重演次数已满，RETAKE 不可执行，改选 REWRITE")
            else:
                # 修订也用尽：继续 RETAKE 只会撞墙；交给后续自修/停机路径。
                pass
    return notes


def _apply_state(
    runtime: CommandRuntime, take: dict[str, Any], state: RuntimeState, target: tuple[str, str]
) -> None:
    """把 Runtime 校验通过的目标阶段写回 take。"""
    moved = runtime.next_state(state, target)
    take["scene_state"] = moved.scene_state
    take["artifact"] = moved.artifact


def _record_manifest(take: dict[str, Any], compiled: Any) -> None:
    """上下文留痕：manifest 不进入模型输入，只用于证明可复现与定位泄露。"""
    take.setdefault("manifests", []).append(
        {
            "audience": compiled.audience,
            "persona": compiled.persona,
            "manifest_hash": compiled.manifest_hash,
            "manifest_version": compiled.manifest.version,
            # 完整绑定：只存 hash 无法回答"这次装配读的是哪一场的哪些材料"
            "binding": dict(compiled.manifest.binding),
            "projection_hash": compiled.manifest.projection_hash,
            "fingerprint": compiled.manifest.fingerprint(),
            "sources": [item.model_dump(mode="json") for item in compiled.manifest.sources],
        }
    )


def _live_takes_for_scene(production: dict[str, Any], scene_index: int) -> int:
    """统计某场仍占用重演预算的 take 数。

    ``SUPERSEDED`` 已被整章审校回退作废，不得继续占满 ``MAX_TAKES``——否则回退到
    前面的场次修好后，无法再为后续场开新 take（run5：scene1 三个旧 take 卡死整章）。
    """
    return sum(
        1
        for take in production.get("takes") or []
        if take.get("scene_index") == scene_index and take.get("status") != "SUPERSEDED"
    )


def _new_take(
    production: dict[str, Any],
    brief: dict[str, Any],
    scene_state: str = SceneRunState.BRIEFED.value,
    artifact: str = "",
) -> None:
    scene = production["scene_index"]
    count = _live_takes_for_scene(production, scene)
    if count >= MAX_TAKES:
        raise ProductionStopped("场景重演次数已达上限，未放行失败场景")
    production["takes"].append(
        {
            "scene_index": scene,
            "take_no": count + 1,
            "brief": brief,
            "state_before": deepcopy(production["working_state"]),
            "turn": 0,
            "performances": [],
            "round_actions": [],
            "events": [],
            "revisions": 0,
            "prose_versions": [],
            "status": "DRAFT",
            "scene_state": scene_state,
            "artifact": artifact,
        }
    )
    # 场景协议跳过逐节拍编排；对照臂仍从 ACT 起。
    if production_protocol(production) == PROTOCOL_BEAT:
        production["phase"] = "ACT"
    else:
        production["phase"] = "SCENE"
