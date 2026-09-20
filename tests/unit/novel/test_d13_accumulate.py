"""缺陷 13：自修反馈被覆盖，且模型不知道还剩哪个动作合法。

两条都是真机打出来的，不是推演（run16，2026-09-10）：

- 第一版引文不合格 → 修；第二版引文**改好了**却被拒了命令 → 修；第三版按命令
  反馈改了动作，又把引文退回第一版的坏样子（``draft: '…'``），最后一次两边都没过。
  根因是 ``repair = [...]`` 是赋值：后一次失败把前一次的约束顶掉了。
- 修订次数用尽后 REWRITE 非法、存在硬失败时 ACCEPT 非法，导演连试三个全被拒。
  可执行性是 Runtime 能**确定性**算出来的，不该让模型猜。
"""

from __future__ import annotations

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import CommandRejected, ProductionStopped

STAGE = "后盖纹丝未动，他盯了很久。"
BAD_QUOTE = "他当场跪下承认了四十年前的旧案"


def _take(action: str = "ACCEPT") -> d.ProseDirection:
    return d.ProseDirection(
        action=action,
        observation="节奏偏平",
        evidence=["后盖纹丝未动"],
        instruction="把犹豫写进动作",
    )


@pytest.mark.asyncio
async def test_second_failure_keeps_the_first_constraint():
    """引文坏 → 命令坏：第二次反馈必须同时带着两条约束，否则改一边坏一边。"""
    seen: list[dict] = []

    def check(result: object) -> None:
        if getattr(result, "action") != "REWRITE":
            raise CommandRejected(str(result.action), "存在硬失败", "fp")

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        if repair_no == 0:
            return _take("ACCEPT").model_copy(update={"evidence": [BAD_QUOTE]})
        return _take("ACCEPT")

    with pytest.raises(ProductionStopped):
        await d._grounded_judgment(
            spy, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文", command_check=check
        )
    assert len(seen) == 4
    second = "".join(seen[2]["repair_instructions"])
    assert BAD_QUOTE in second, "第一次的引文约束被第二次的命令反馈顶掉了"
    assert "存在硬失败" in second


BAD_QUOTE_B = "他当场跪下承认了另一桩旧案"


@pytest.mark.asyncio
async def test_repeated_evidence_failures_accumulate():
    """两次引文失败带**不同的坏引文**：第三次收到的反馈必须两条都在。

    用两条不同的坏引文，是因为"覆盖"和"累积"只有在**前一次的痕迹还在不在**上
    才有区别——两次都用同一条的话，覆盖后的反馈里也还看得到它，反证就是假的。
    """
    seen: list[dict] = []

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        if repair_no == 2:
            return _take("REWRITE")  # 看到两条约束后改对
        bad = BAD_QUOTE if repair_no == 0 else BAD_QUOTE_B
        return _take("ACCEPT").model_copy(update={"evidence": [bad]})

    result = await d._grounded_judgment(
        spy, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文"
    )
    assert result.action == "REWRITE"
    third = "".join(seen[2]["repair_instructions"])
    assert BAD_QUOTE in third, "第一次的引文约束被第二次顶掉了"
    assert BAD_QUOTE_B in third


@pytest.mark.asyncio
async def test_stage_text_is_not_repeated_on_every_evidence_failure():
    """累积不能把原文一遍遍塞回去：两轮引文失败，原文只出现一次。"""
    seen: list[dict] = []

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        return _take("ACCEPT").model_copy(update={"evidence": [BAD_QUOTE]})

    with pytest.raises(ProductionStopped):
        await d._grounded_judgment(spy, d.ProseDirection, "系统提示", {}, STAGE, "审阅正文")
    last = seen[-1]["repair_instructions"]
    assert sum(1 for item in last if item == STAGE[:1500]) == 1


@pytest.mark.asyncio
async def test_rejection_feedback_lists_which_actions_are_still_executable():
    """模型看不见状态机：被拒后必须告诉它**还剩哪些动作能执行**。"""
    seen: list[dict] = []

    def check(result: object) -> None:
        if getattr(result, "action") != "REWRITE":
            raise CommandRejected(str(result.action), "存在硬失败", "fp")

    def report(result: object) -> list[str]:
        return [
            "- RETAKE：可执行",
            "- REWRITE：可执行",
            "- ACCEPT：不可执行（存在硬失败，导演不能接受该场景）",
        ]

    async def spy(schema, system, payload, *, repair_no=0):
        seen.append(payload)
        return _take("ACCEPT") if repair_no == 0 else _take("REWRITE")

    result = await d._grounded_judgment(
        spy,
        d.ProseDirection,
        "系统提示",
        {},
        STAGE,
        "审阅正文",
        command_check=check,
        command_report=report,
    )
    assert result.action == "REWRITE"
    feedback = "".join(seen[1]["repair_instructions"])
    assert "ACCEPT：不可执行（存在硬失败，导演不能接受该场景）" in feedback
    assert "REWRITE：可执行" in feedback
    assert "可执行性" in feedback
