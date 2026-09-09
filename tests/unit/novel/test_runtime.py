# Chinese fixtures deliberately use full-width punctuation.
# ruff: noqa: RUF001

"""命令 Runtime 的行为测试（Tech-Spec §3.4、G-04、G-05）。

Runtime 是唯一能把命令变成状态迁移的地方：模型输出越界必须被拒绝，
而不是被静默执行。
"""

import pytest
from regent.novel.application.runtime import (
    NO_SCENE,
    CommandRejected,
    CommandRuntime,
    RuntimeLimits,
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind, command
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.states import SceneArtifact, SceneRunState

PERFORMANCE = (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value)
PROSE = (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value)


def runtime(**limits):
    return CommandRuntime(RuntimeLimits(**limits))


def brief_state(**overrides):
    base = dict(
        scene_state=SceneRunState.BRIEFED.value,
        cast=frozenset({"主角"}),
        has_visible_events=True,
    )
    return RuntimeState(**{**base, **overrides})


def test_command_rejected_is_production_stopped():
    """拒绝与循环停止对调用方是同一类结果，草稿都保留。"""
    assert issubclass(CommandRejected, ProductionStopped)


def test_plan_then_performance_follows_the_scene_machine():
    rt = runtime()
    state = RuntimeState(scene_state=NO_SCENE, cast=frozenset({"主角"}))
    target = rt.validate(
        command(CommandKind.PLAN_SCENE, command_id="p1", actors=[{"persona": "主角"}]), state
    )
    assert target == (SceneRunState.BRIEFED.value, "")
    state = rt.next_state(state, target)
    assert rt.validate(command(CommandKind.REQUEST_PERFORMANCE, command_id="a1"), state) == (
        SceneRunState.PERFORMING.value,
        "",
    )


def test_command_outside_the_allowlist_is_rejected():
    rt = runtime()
    forged = command(CommandKind.REQUEST_PERFORMANCE, command_id="x")
    forged.kind = "DELETE_CANON"
    with pytest.raises(CommandRejected, match="白名单"):
        rt.validate(forged, brief_state())


def test_wrong_phase_is_rejected():
    rt = runtime()
    with pytest.raises(CommandRejected, match="当前阶段"):
        rt.validate(command(CommandKind.ACCEPT_SCENE, command_id="x"), brief_state())


def test_stale_input_version_cannot_move_the_new_input():
    rt = runtime()
    state = brief_state(input_version=2)
    with pytest.raises(CommandRejected, match="输入版本"):
        rt.validate(
            command(CommandKind.REQUEST_PERFORMANCE, command_id="x", input_version=1), state
        )


def test_undefined_persona_cannot_be_directed():
    rt = runtime()
    with pytest.raises(CommandRejected, match="未定义的人物"):
        rt.validate(
            command(CommandKind.REQUEST_PERFORMANCE, command_id="x", persona="路人"),
            brief_state(),
        )


@pytest.mark.parametrize(
    "overrides",
    [{"calls_used": 80}, {"reserved_minor": 20_000}],
    ids=["calls", "cost"],
)
def test_budget_exhausted_stops_before_another_command(overrides):
    rt = runtime()
    with pytest.raises(CommandRejected, match="预算"):
        rt.validate(
            command(CommandKind.REQUEST_PERFORMANCE, command_id="x"), brief_state(**overrides)
        )


def test_continue_stops_at_beat_limit():
    rt = runtime(max_turns=4)
    state = brief_state(scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1], turn=3)
    with pytest.raises(CommandRejected, match="节拍数"):
        rt.validate(command(CommandKind.CONTINUE_SCENE, command_id="x"), state)


def test_retake_needs_evidence_and_a_changed_brief():
    rt = runtime()
    state = brief_state(scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1])
    with pytest.raises(CommandRejected, match="失败证据"):
        rt.validate(command(CommandKind.RETAKE_SCENE, command_id="x", revised_brief={}), state)
    with pytest.raises(CommandRejected, match="修改后的场景指令"):
        rt.validate(
            command(CommandKind.RETAKE_SCENE, command_id="x", evidence=["他后退一步。"]), state
        )


def test_retake_forks_a_new_take_and_resets_the_beat():
    rt = runtime()
    state = brief_state(
        scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1], turn=2, revisions=1
    )
    target = rt.validate(
        command(
            CommandKind.RETAKE_SCENE,
            command_id="x",
            evidence=["他后退一步。"],
            revised_brief={"actors": [{"persona": "主角"}]},
        ),
        state,
    )
    moved = rt.next_state(state, target)
    assert moved.scene_state == SceneRunState.BRIEFED.value
    assert moved.takes_used == state.takes_used + 1
    # 新 take 从头开始：节拍与修订都不继承被拒绝的那一版。
    assert moved.turn == 0 and moved.revisions == 0


def test_retake_limit_is_enforced_by_the_runtime():
    rt = runtime(max_takes=3)
    state = brief_state(scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1], takes_used=3)
    with pytest.raises(CommandRejected, match="重演次数"):
        rt.validate(
            command(
                CommandKind.RETAKE_SCENE,
                command_id="x",
                evidence=["他后退一步。"],
                revised_brief={"actors": [{"persona": "主角"}]},
            ),
            state,
        )


def test_director_cannot_accept_over_a_hard_failure():
    rt = runtime()
    state = brief_state(scene_state=PROSE[0], artifact=PROSE[1], has_hard_failure=True)
    with pytest.raises(CommandRejected, match="不能接受"):
        rt.validate(command(CommandKind.ACCEPT_SCENE, command_id="x"), state)


def test_rule_issues_can_only_be_answered_by_a_retake():
    rt = runtime()
    state = brief_state(
        scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1], has_rule_issues=True
    )
    with pytest.raises(CommandRejected, match="只能重演"):
        rt.validate(command(CommandKind.RENDER_SCENE, command_id="x"), state)
    with pytest.raises(CommandRejected, match="只能重演"):
        rt.validate(command(CommandKind.CONTINUE_SCENE, command_id="x"), state)


def test_render_requires_something_the_reader_can_see():
    rt = runtime()
    state = brief_state(
        scene_state=PERFORMANCE[0], artifact=PERFORMANCE[1], has_visible_events=False
    )
    with pytest.raises(CommandRejected, match="可呈现"):
        rt.validate(command(CommandKind.RENDER_SCENE, command_id="x"), state)


def test_automatic_transitions_are_validated_too():
    rt = runtime()
    state = brief_state(scene_state=SceneRunState.PERFORMING.value)
    moved = rt.automatic(state, (SceneRunState.RESOLVING.value, ""))
    assert moved.scene_state == SceneRunState.RESOLVING.value
    with pytest.raises(Exception, match="invalid scene_run transition"):
        rt.automatic(state, (SceneRunState.RENDERING.value, ""))


def test_accepted_scene_is_terminal():
    rt = runtime()
    state = brief_state(scene_state=SceneRunState.ACCEPTED.value)
    with pytest.raises(CommandRejected, match="终态"):
        rt.validate(command(CommandKind.REQUEST_USER_DECISION, command_id="x"), state)


def test_command_fingerprint_changes_with_input_version():
    first = command(CommandKind.RENDER_SCENE, command_id="c1", input_version=1)
    second = command(CommandKind.RENDER_SCENE, command_id="c1", input_version=2)
    assert first.fingerprint() != second.fingerprint()
    assert first.fingerprint() == command(
        CommandKind.RENDER_SCENE, command_id="c1", input_version=1
    ).fingerprint()
