"""命令 Runtime（Tech-Spec §3.4）。

Runtime 是唯一能把命令变成状态迁移的地方。它校验命令白名单、输入版本、
角色权限、预算、最大步数和依赖；模型输出未经校验不得改变任何状态。

Runtime 不做创作判断，也不发起模型调用：它只回答“这条命令现在是否允许，
以及允许之后场景处于哪个阶段”。
"""

# Chinese docstrings and messages deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from regent.novel.domain.commands import COMMAND_VERSION, CommandKind, DirectorCommand
from regent.novel.domain.errors import CommandRejected
from regent.novel.domain.states import (
    SceneArtifact,
    SceneRunState,
    assert_scene_run_transition,
)

# 尚无场景时的哨兵状态：只允许 PLAN_SCENE。
NO_SCENE = "NONE"
_ANY_NON_TERMINAL = "ANY"


@dataclass(frozen=True)
class RuntimeLimits:
    """有界探索的边界。次数管行为，金额管钱，两者互不替代。"""

    max_calls: int = 80
    max_takes: int = 3
    max_turns: int = 4
    max_revisions: int = 2
    max_cost_minor: int = 20_000


@dataclass(frozen=True)
class RuntimeState:
    """执行期只读快照。Runtime 只依据它做判断，不回写生产状态。"""

    scene_state: str = SceneRunState.BRIEFED.value
    artifact: str = ""
    input_version: int = 1
    turn: int = 0
    takes_used: int = 1
    revisions: int = 0
    calls_used: int = 0
    reserved_minor: int = 0
    cast: frozenset[str] = frozenset()
    has_rule_issues: bool = False
    has_hard_failure: bool = False
    has_visible_events: bool = False
    # scene/take 绑定：命令必须作用在它自己被提出的那一场、那一次 take 上
    scene_index: int = 0
    take_no: int = 1
    # 产物绑定：接受/成文必须基于已存在的稿件，不能凭空通过
    has_prose: bool = False


# 命令 → (允许的前置状态集合, 目标状态)。DIRECTOR_VIEW 用 artifact 区分两次观看。
_TRANSITIONS: dict[CommandKind, tuple[frozenset[tuple[str, str]], tuple[str, str]]] = {
    CommandKind.PLAN_SCENE: (
        frozenset({(NO_SCENE, ""), (SceneRunState.ACCEPTED.value, "")}),
        (SceneRunState.BRIEFED.value, ""),
    ),
    CommandKind.REQUEST_PERFORMANCE: (
        frozenset({(SceneRunState.BRIEFED.value, ""), (SceneRunState.PERFORMING.value, "")}),
        (SceneRunState.PERFORMING.value, ""),
    ),
    CommandKind.CONTINUE_SCENE: (
        frozenset({(SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value)}),
        (SceneRunState.PERFORMING.value, ""),
    ),
    CommandKind.RETAKE_SCENE: (
        frozenset(
            {
                (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value),
                (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
            }
        ),
        # 重演 fork 新 take：新 take 回到已下达 brief，旧 take 保留为 REJECTED。
        (SceneRunState.BRIEFED.value, ""),
    ),
    CommandKind.RENDER_SCENE: (
        frozenset({(SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value)}),
        (SceneRunState.RENDERING.value, ""),
    ),
    CommandKind.REWRITE_PROSE: (
        frozenset({(SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value)}),
        (SceneRunState.RENDERING.value, ""),
    ),
    CommandKind.ACCEPT_SCENE: (
        frozenset({(SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value)}),
        (SceneRunState.VALIDATING.value, ""),
    ),
    CommandKind.ASSEMBLE_CHAPTER: (
        frozenset({(SceneRunState.ACCEPTED.value, "")}),
        (SceneRunState.ACCEPTED.value, ""),
    ),
    CommandKind.REQUEST_USER_DECISION: (
        frozenset({(_ANY_NON_TERMINAL, "")}),
        (_ANY_NON_TERMINAL, ""),
    ),
    CommandKind.FINISH_CHAPTER: (
        frozenset({(SceneRunState.ACCEPTED.value, "")}),
        (SceneRunState.ACCEPTED.value, ""),
    ),
}


def personas_in(payload: dict[str, Any]) -> set[str]:
    """从命令载荷里取出涉及的人物，供角色权限校验。"""
    found: set[str] = set()
    if isinstance(payload.get("persona"), str):
        found.add(payload["persona"])
    actors = payload.get("actors")
    if isinstance(actors, list):
        for item in actors:
            if isinstance(item, dict) and isinstance(item.get("persona"), str):
                found.add(item["persona"])
            elif isinstance(item, str):
                found.add(item)
    elif isinstance(actors, dict):
        found |= {name for name in actors if isinstance(name, str)}
    voices = payload.get("voices")
    if isinstance(voices, dict):
        found |= {name for name in voices if isinstance(name, str)}
    brief = payload.get("revised_brief")
    if isinstance(brief, dict):
        found |= personas_in(brief)
    return found


class CommandRuntime:
    """校验导演命令并给出目标场景阶段。"""

    def __init__(self, limits: RuntimeLimits | None = None) -> None:
        self.limits = limits or RuntimeLimits()

    def validate(self, command: DirectorCommand, state: RuntimeState) -> tuple[str, str]:
        """校验通过返回目标 (state, artifact)；否则抛 CommandRejected。"""
        kind = command.kind
        fingerprint = command.fingerprint()

        def reject(reason: str) -> CommandRejected:
            return CommandRejected(str(kind), reason, fingerprint)

        if kind not in _TRANSITIONS:
            raise reject("命令不在白名单内")
        if command.version != COMMAND_VERSION:
            raise reject(f"命令版本不匹配：{command.version} != {COMMAND_VERSION}")
        if command.input_version != state.input_version:
            # 用户改意后的旧命令不得再作用在新输入上（Tech-Spec §5）。
            raise reject(
                f"命令输入版本过期：{command.input_version} != {state.input_version}"
            )

        allowed, target = _TRANSITIONS[kind]
        current = (state.scene_state, state.artifact)
        if (_ANY_NON_TERMINAL, "") in allowed:
            if state.scene_state == SceneRunState.ACCEPTED.value:
                raise reject("终态场景不再接受该命令")
            target = current
        elif current not in allowed:
            raise reject(f"当前阶段 {state.scene_state}/{state.artifact or '-'} 不允许该命令")

        if state.calls_used >= self.limits.max_calls:
            raise reject("调用次数预算已耗尽")
        if state.reserved_minor >= self.limits.max_cost_minor:
            raise reject("货币预算已耗尽")

        if kind is CommandKind.CONTINUE_SCENE and state.turn + 1 >= self.limits.max_turns:
            raise reject("节拍数已达上限")
        if kind is CommandKind.RETAKE_SCENE and state.takes_used >= self.limits.max_takes:
            raise reject("重演次数已达上限")
        if kind is CommandKind.REWRITE_PROSE and state.revisions >= self.limits.max_revisions:
            raise reject("呈现修订次数已达上限")

        unknown = personas_in(command.payload) - state.cast
        if unknown:
            raise reject(f"命令涉及未定义的人物：{sorted(unknown)}")

        # 绑定校验：命令不能漂移到别的场景或别的 take 上（P1-1）
        if command.scene_index != state.scene_index:
            raise reject(
                f"命令场景绑定不符：{command.scene_index} != {state.scene_index}"
            )
        if command.take_no != state.take_no:
            raise reject(f"命令 take 绑定不符：{command.take_no} != {state.take_no}")

        if kind is CommandKind.RETAKE_SCENE:
            if not command.evidence:
                raise reject("重演必须给出失败证据")
            if not command.payload.get("revised_brief"):
                raise reject("重演必须给出修改后的场景指令")
        if (
            state.has_rule_issues
            and kind not in (CommandKind.RETAKE_SCENE, CommandKind.REQUEST_USER_DECISION)
        ):
            # 规则冲突只能重演，不得继续或呈现（Tech-Spec §4.2）。
            raise reject("存在场景规则冲突，只能重演")
        if kind is CommandKind.RENDER_SCENE and not state.has_visible_events:
            raise reject("没有可呈现给读者的事件")
        if kind is CommandKind.ACCEPT_SCENE and state.has_hard_failure:
            # 导演不得覆盖硬失败（G-04）。硬失败优先于稿件校验：先说清楚为什么不能过。
            raise reject("存在硬失败，导演不能接受该场景")
        # 产物绑定：接受/组章必须基于已存在的稿件，不得凭空通过（P1-1）
        if kind is CommandKind.ACCEPT_SCENE and not state.has_prose:
            raise reject("没有稿件可以接受")
        if kind is CommandKind.ASSEMBLE_CHAPTER and not state.has_prose:
            raise reject("没有场景正文可以组章")

        return target

    def automatic(self, state: RuntimeState, target: tuple[str, str]) -> RuntimeState:
        """非命令驱动的自动迁移：结算与“观看表演”由 Runtime 推进，不由模型提出。

        这一条保证 PERFORMING → RESOLVING → DIRECTOR_VIEW 的次序不受模型影响。
        """
        assert_scene_run_transition(
            state.scene_state, target[0], state.artifact, target[1]
        )
        return self.next_state(state, target)

    def next_state(self, state: RuntimeState, target: tuple[str, str]) -> RuntimeState:
        """按校验结果推进只读快照，供调用方据此更新生产状态。"""
        scene_state, artifact = target
        if scene_state == SceneRunState.BRIEFED.value and state.scene_state != (
            SceneRunState.BRIEFED.value
        ):
            # 新的 take：节拍、修订、轮次与呈现都从头开始，旧的保留为历史。
            return replace(
                state,
                scene_state=scene_state,
                artifact=artifact,
                turn=0,
                revisions=0,
                takes_used=state.takes_used + 1,
                has_rule_issues=False,
                has_visible_events=False,
            )
        return replace(state, scene_state=scene_state, artifact=artifact)
