"""缺陷 11：命令被 Runtime 拒绝即判死整章，而模型看不见自己错在哪。

真机死法（run13/run15）：结算给出 6 条状态变化，正文只写了 3 条，独立核验判硬失败，
导演仍选 ``ACCEPT_SCENE``，Runtime 拒绝，整章消失——**没有一次机会让导演知道
ACCEPT 执行不了**。

D-07 拍板：「允许有限次命令自修」。与缺陷 7 的引文自修共用 ``_grounded_judgment``
一条通道，因为两者的共同点都是**模型看不见自己错在哪**；区别是命令类问题给原文
没用，得让它**改选一个动作**。

边界（故意不越）：
- 自修**不改判据**。Runtime 该怎么拒还怎么拒，只是多给一次机会。
- 自修**不做静默替换**。就地收束只发生在「可判定的非法」上（缺陷 9 的
  CONTINUE→RENDER），硬失败时的 ACCEPT 仍然不收束——那是产品决定。
- 预算有上限，用尽即判死。
"""

from __future__ import annotations

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import CommandRejected, ProductionStopped

STAGE = "后盖纹丝未动，他盯了很久。"


def _take(action: str = "ACCEPT") -> d.ProseDirection:
    return d.ProseDirection(
        action=action,
        observation="节奏偏平",
        evidence=["后盖纹丝未动"],
        instruction="把犹豫写进动作",
    )


def _reject_until(allowed: set[str], reason: str = "存在硬失败"):
    """命令校验替身：只放行 ``allowed`` 里的动作，否则抛运行时拒绝。"""
    seen: list[str] = []

    def check(result: Any) -> None:  # noqa: ANN001
        seen.append(result.action)
        if result.action not in allowed:
            raise CommandRejected(result.action, reason, "fp")

    check.seen = seen  # type: ignore[attr-defined]
    return check


@pytest.mark.asyncio
async def test_rejected_command_gets_a_feedback_retry_instead_of_killing_the_chapter():
    """被拒的动作换来一次改选机会，而不是整章判死。"""
    check = _reject_until({"REWRITE"})

    async def call(schema, system, payload, *, repair_no=0):
        return _take("ACCEPT") if repair_no == 0 else _take("REWRITE")

    result = await d._grounded_judgment(
        call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文", command_check=check
    )
    assert result.action == "REWRITE"
    assert check.seen == ["ACCEPT", "REWRITE"]


@pytest.mark.asyncio
async def test_command_feedback_names_the_rejection_reason():
    """反馈必须点名**为什么**执行不了——只说"换个动作"模型会换回同一个。"""

    async def call(schema, system, payload, *, repair_no=0):
        return _take("ACCEPT") if repair_no == 0 else _take("REWRITE")

    seen: list[dict] = []

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        return await call(schema, system, payload, repair_no=repair_no)

    await d._grounded_judgment(
        spy,
        d.ProseDirection,
        "系统提示",
        {},
        STAGE,
        "审阅正文",
        command_check=_reject_until({"REWRITE"}, reason="存在硬失败：3 条状态变化未落正文"),
    )
    feedback = "".join(seen[1]["repair_instructions"])
    assert "存在硬失败：3 条状态变化未落正文" in feedback
    assert "ACCEPT" in feedback, "要点名是哪个动作执行不了"


@pytest.mark.asyncio
async def test_command_repair_uses_a_fresh_command_id():
    """自修必须换 command_id：沿用原 id 会被幂等键挡住或复用上一次的坏结果。"""
    ids: list[int] = []

    async def call(schema, system, payload, *, repair_no=0):
        ids.append(repair_no)
        return _take("ACCEPT") if repair_no == 0 else _take("REWRITE")

    await d._grounded_judgment(
        call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
        command_check=_reject_until({"REWRITE"}),
    )
    assert ids == [0, 1]


@pytest.mark.asyncio
async def test_command_feedback_does_not_dump_the_stage_text():
    """命令类问题给原文没用——它是要模型改主意，不是要它抄一句话。"""
    seen: list[dict] = []

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        return _take("ACCEPT") if repair_no == 0 else _take("REWRITE")

    await d._grounded_judgment(
        spy, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
        command_check=_reject_until({"REWRITE"}),
    )
    feedback = "".join(seen[1]["repair_instructions"])
    assert STAGE not in feedback


@pytest.mark.asyncio
async def test_persisting_on_an_illegal_action_still_kills_the_chapter():
    """自修是给机会，不是取消判据：改不了的仍然判死。"""

    async def call(schema, system, payload, *, repair_no=0):
        return _take("ACCEPT")

    with pytest.raises(ProductionStopped) as info:
        await d._grounded_judgment(
            call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
            command_check=_reject_until(set()),
        )
    assert "自修次数已用尽" in str(info.value)


@pytest.mark.asyncio
async def test_evidence_and_command_repairs_share_one_budget():
    """两类问题共用一个预算：不能引文修 2 次、命令再修 2 次。"""
    attempts: list[int] = []

    async def call(schema, system, payload, *, repair_no=0):
        attempts.append(repair_no)
        if repair_no == 0:
            return _take("ACCEPT").model_copy(
                update={"evidence": ["他当场跪下承认了四十年前的旧案"]}
            )
        return _take("ACCEPT")

    with pytest.raises(ProductionStopped):
        await d._grounded_judgment(
            call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
            command_check=_reject_until(set()),
        )
    assert attempts == [0, 1, 2, 3], f"预算应为 1+{d.MAX_JUDGE_REPAIRS}+{d.MAX_COMMAND_REPAIRS} 次调用"


@pytest.mark.asyncio
async def test_evidence_repair_then_command_repair_both_land_within_budget():
    """先引文错、改好后再命令错——两次不同的错误都要能被看见。"""
    check = _reject_until({"REWRITE"})

    async def call(schema, system, payload, *, repair_no=0):
        if repair_no == 0:
            return _take("ACCEPT").model_copy(
                update={"evidence": ["他当场跪下承认了四十年前的旧案"]}
            )
        if repair_no == 1:
            return _take("ACCEPT")
        return _take("REWRITE")

    result = await d._grounded_judgment(
        call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文", command_check=check
    )
    assert result.action == "REWRITE"
    assert check.seen == ["ACCEPT", "REWRITE"]


@pytest.mark.asyncio
async def test_budget_exhaustion_reports_the_last_rejection_reason():
    """耗尽时报「次数用尽」会把 Runtime 的真实理由吞掉——既没法诊断，也让依赖
    该理由的判据静默失效（既有测试按「不能接受」匹配 Runtime 的拒绝）。"""

    async def call(schema, system, payload, *, repair_no=0):
        return _take("ACCEPT")

    with pytest.raises(ProductionStopped) as info:
        await d._grounded_judgment(
            call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
            command_check=_reject_until(set(), reason="存在硬失败，导演不能接受该场景"),
        )
    assert "自修次数已用尽" in str(info.value)
    assert "存在硬失败，导演不能接受该场景" in str(info.value)


@pytest.mark.asyncio
async def test_coercion_runs_before_command_check():
    """收束必须发生在校验之前：否则校验的是模型给的动作，收束完又没人验。"""
    order: list[str] = []

    def coerce(result: Any) -> None:  # noqa: ANN001
        order.append(f"coerce:{result.action}")
        result.action = "REWRITE"

    def check(result: Any) -> None:  # noqa: ANN001
        order.append(f"check:{result.action}")
        if result.action != "REWRITE":
            raise CommandRejected(result.action, "硬失败", "fp")

    async def call(schema, system, payload, *, repair_no=0):
        return _take("ACCEPT")

    result = await d._grounded_judgment(
        call, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文",
        coerce=coerce, command_check=check,
    )
    assert result.action == "REWRITE"
    assert order == ["coerce:ACCEPT", "check:REWRITE"]
