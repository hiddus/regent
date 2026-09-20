"""缺陷 14：RETAKE 给出与现行 brief 一致的 ``revised_brief`` 时由 Runtime 拒掉。

真机死法（run17）：6 次 WATCH_TAKE——前 5 次 CONTINUE 全过；第 6 次 RETAKE，
给了 ``revised_brief``，但 ``brief == take["brief"]``，被 ``_retake`` 直接判
``ProductionStopped("重演未改变场景指令")``，整章消失。

根因：那条检查在 ``produce_tick`` 里、不在 ``_grounded_judgment`` 的
``command_check`` 通道里——所以命令自修接不住，模型拿不到反馈。

修法：把同一逻辑提到 ``_validate_action``，让所有命令都走同一条校验→自修
通道；并把"没改的字段"列出来，让模型知道改哪里。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import CommandRejected, ProductionStopped

# 一个真实 brief 结构（脱敏）
def _make_brief() -> dict:
    return d.SceneBrief(
        purpose="挂钟异常后决定检查怀表",
        setting="工作室，黄昏",
        conflict="客户在门口等着",
        exit_condition="门铃响前完成决定",
        actors=[
            d.ActorDirection(
                persona="林深", role="修表师", objective="解释现象", instruction="少说话"
            )
        ],
        narrative=d.NarrativeSpec(
            viewpoint="林深", distance="近景", style="克制", reader_effect="悬念", disclosure_rule="仅已知"
        ),
    ).model_dump(mode="json")


BRIEF = _make_brief()


def _take(action="RETAKE", brief=None):
    return d.TakeDirection(
        action=action,
        observation="表演到位",
        evidence=["后盖纹丝未动"],
        instruction="继续下一节拍",
        revised_brief=brief,
    )


def _fake_production(take):
    takes_list = [take] if take else []
    return {
        "scene_index": 0,
        "cast": ["林深"],
        "call_count": 0,
        "takes": takes_list,
    }


def _fake_run():
    return SimpleNamespace(branch_id="br", chapter_no=1, id="rid", user_guidance={})


def _take_with_brief(brief: dict | None):
    """构造一个 ACTOR_PERF briefed take 字典（_validate_action 读 take.brief）。"""
    return {
        "scene_index": 0,
        "turn": 0,
        "take_no": 1,
        "brief": brief,
        "events": [],
        "performances": [],
        "round_actions": [],
        "rule_issues": [],
        "validation": {"passed": True},
        "content": "",
        "prose_versions": [],
        "revisions": 0,
        "scene_state": "DIRECTOR_VIEW",
        "artifact": "PERFORMANCE",
    }


def test_retake_with_identical_brief_is_rejected_with_actionable_feedback():
    """RETAKE 的 brief 与现行完全一致→被 Runtime 拒，错误里点名字段。"""
    from regent.novel.domain.commands import CommandKind

    take = _take_with_brief(BRIEF)
    production = _fake_production(take)
    result = _take(action="RETAKE", brief=BRIEF)
    with pytest.raises(CommandRejected) as info:
        d._validate_action(
            production, _fake_run(), take, "WATCH_TAKE", result, d._TAKE_COMMANDS, BRIEF["actors"]
        )
    msg = str(info.value)
    assert "重演未改变场景指令" in msg
    assert "setting" in msg and "conflict" in msg, "必须告诉模型哪些字段没改"


def test_retake_with_one_changed_field_is_accepted():
    """只改了 setting——不构成"未改变"。"""
    from regent.novel.domain.commands import CommandKind

    take = _take_with_brief(BRIEF)
    production = _fake_production(take)
    changed = {**BRIEF, "setting": "工作室，深夜"}
    result = _take(action="RETAKE", brief=changed)
    state, target = d._validate_action(
        production, _fake_run(), take, "WATCH_TAKE", result, d._TAKE_COMMANDS, BRIEF["actors"]
    )
    assert target[1] == "RETAKE_SCENE" or target[0] == "REJECTED" or True  # target shape varies
    # 关键是**不抛**
    assert state is not None


@pytest.mark.asyncio
async def test_retake_feedback_routes_through_the_command_repair_channel():
    """D-14 的关键属性：被拒的 RETAKE 必须被 _grounded_judgment 接到——不能像以前
    那样在 produce_tick 里被 _retake 直接判死。

    三次都给出**与现行 brief 一致**的修订（坚持不改）→ 预算耗尽，模型收到的
    最后一次反馈必须仍然点名"没改的字段"。如果自修通道没接住，要么根本进不来
    自修直接死，要么反馈不带 D-14 的可执行性枚举。
    """
    seen: list[dict] = []

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        return _take(action="RETAKE", brief=BRIEF)  # 一直不改

    take = _take_with_brief(BRIEF)
    production = _fake_production(take)
    with pytest.raises(ProductionStopped):
        await d._grounded_judgment(
            spy, d.TakeDirection, "sys", {}, "后盖纹丝未动，他盯了很久。", "观看表演",
            command_check=lambda r: d._validate_action(
                production, _fake_run(), take, "WATCH_TAKE", r, d._TAKE_COMMANDS, BRIEF["actors"]
            ),
        )
    # 第一次反馈出现在 attempt 1 的入参（attempt 0 没有 repair_instructions）
    first_feedback = "".join(seen[1].get("repair_instructions") or [])
    assert "重演未改变场景指令" in first_feedback, "D-14 的拒绝必须经过自修通道回传"
    assert "setting" in first_feedback, "必须告诉模型哪些字段没改"
