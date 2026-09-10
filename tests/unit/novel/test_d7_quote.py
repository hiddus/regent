"""缺陷 7：导演的 evidence 被要求**字节级**逐字相同，一个代词还原就判死整章。

真机样本（作品 edcd26bc）：8 条 evidence 里 7 条逐字命中，第 8 条把原文的
「**他**将开表器握在手中」写成「**陈默**将开表器握在手中」——把代词还原成人名，
一句正常的话——整章 TERMINAL_FAILED。

所以改成量**覆盖度**：凭空捏造的引文凑不出足够长的逐字重合，反捏造的保证在
实质上仍然成立。注意这与角色名归一是**两回事**：角色名是身份键，猜错不可逆；
引文只是审计用的出处，近义改写无害。风险不同，规则就该不同。
"""

from __future__ import annotations

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import ProductionStopped

# 真机场上原文（事件陈述 + 表演动作 + 台词）
STAGE_TEXT = "\n".join(
    [
        "陈默将怀表置于软布垫上，戴上目镜贴近表盘。指针正以恒定速度逆时针移动。",
        "陈默用镊子轻触表冠，开始顺时针上链。他感到阻力每三秒出现一次轻微波动。",
        "陈默拉开工作台抽屉，取出父亲专用的黄铜开表器。开表器表面有经年使用的包浆，"
        "握柄处刻着'陈'字。他将开表器握在手中，停顿了约五秒，拇指反复摩挲刻字。",
        "陈默深吸一口气，将开表器对准后盖凹槽，缓缓施力。后盖纹丝未动。",
        "将怀表置于软布垫上，用目镜贴近表盘，观察指针逆时针移动的轨迹",
        "逆走……游丝力矩不该反向。除非有人故意调换了擒纵叉的位置。",
    ]
)

# 真机导演实际给出的引用：把「他将开表器握在手中」还原成了「陈默将开表器握在手中」
PRONOUN_RESOLVED = "陈默将开表器握在手中，停顿了约五秒，拇指反复摩挲刻字"


def test_pronoun_resolved_quote_is_accepted():
    """真机判死的那条：代词还原成人名不该等于捏造。"""
    d._quote_check([PRONOUN_RESOLVED], STAGE_TEXT)


def test_exact_quote_is_accepted():
    d._quote_check(["陈默用镊子轻触表冠，开始顺时针上链"], STAGE_TEXT)


def test_short_quote_must_match_in_full():
    """短引文天然要求整串命中——它短到没有"差不多"的余地。"""
    d._quote_check(["后盖纹丝未动"], STAGE_TEXT)
    with pytest.raises(ProductionStopped):
        d._quote_check(["后盖纹丝不动"], STAGE_TEXT)


def test_mosaic_quote_is_rejected():
    """两截都出自原文、但拼在一起的引文也不算引用——重合必须是**连续**的一段。"""
    with pytest.raises(ProductionStopped):
        d._quote_check(["陈默用镊子轻触表冠。后盖纹丝未动"], STAGE_TEXT)


def test_fabricated_quote_is_still_rejected():
    """改判据不是取消判据：凭空写的台词仍然必须判死。"""
    with pytest.raises(ProductionStopped) as info:
        d._quote_check(["他当场跪下承认了四十年前的旧案"], STAGE_TEXT)
    assert "最长逐字重合" in str(info.value)


def test_one_bad_quote_poisons_the_whole_judgment():
    """不能因为多数命中就放行：导演的判断是整体成立的。"""
    with pytest.raises(ProductionStopped):
        d._quote_check([PRONOUN_RESOLVED, "他当场跪下承认了四十年前的旧案"], STAGE_TEXT)


def test_empty_evidence_is_rejected():
    with pytest.raises(ProductionStopped):
        d._quote_check([], STAGE_TEXT)


def test_blank_quote_is_rejected():
    with pytest.raises(ProductionStopped) as info:
        d._quote_check(["   "], STAGE_TEXT)
    assert "空白引用" in str(info.value), "空白引用必须被点名，而不是混在通用报错里"


def _take_direction(evidence: list[str]) -> d.TakeDirection:
    return d.TakeDirection(
        action="CONTINUE", observation="表演到位", evidence=evidence, instruction="继续下一节拍"
    )


@pytest.mark.asyncio
async def test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter():
    """判据不放宽，但给模型一次**看见错误**的机会。"""
    seen: list[tuple[int, dict]] = []

    async def call(schema, system, payload, *, repair_no=0):
        seen.append((repair_no, payload))
        if repair_no == 0:
            return _take_direction(["他当场跪下承认了四十年前的旧案"])
        return _take_direction(["后盖纹丝未动"])

    result = await d._grounded_judgment(
        call, d.TakeDirection, "系统提示", {"brief": {}}, STAGE_TEXT, "观看表演"
    )
    assert result.evidence == ["后盖纹丝未动"]
    assert len(seen) == 2, "应当只自修一次"
    assert "repair_instructions" not in seen[0][1], "首次调用不带反馈"
    assert seen[1][0] == 1, "自修必须换 command_id，否则被幂等键挡住或复用坏结果"
    feedback = "".join(seen[1][1]["repair_instructions"])
    assert "他当场跪下承认了四十年前的旧案" in feedback, "反馈要点名哪条引用不能用"
    assert STAGE_TEXT[:200] in feedback, "反馈要给出可原样抄录的原文"


@pytest.mark.asyncio
async def test_repair_budget_is_bounded():
    """改不了的仍然判死——自修是给机会，不是取消判据。"""

    async def call(schema, system, payload, *, repair_no=0):
        return _take_direction(["他当场跪下承认了四十年前的旧案"])

    with pytest.raises(ProductionStopped) as info:
        await d._grounded_judgment(
            call, d.TakeDirection, "系统提示", {}, STAGE_TEXT, "观看表演"
        )
    assert "自修次数已用尽" in str(info.value)


def test_elided_quote_is_accepted_when_every_kept_fragment_is_verbatim():
    """真机形态：``A……B`` 跳读引用，只要两段都逐字存在就算有出处。"""
    quote = "陈默将怀表置于软布垫上……指针正以恒定速度逆时针移动"
    assert d._is_grounded(quote, STAGE_TEXT)


def test_elided_quote_with_one_fabricated_fragment_is_rejected():
    """省略号是把真实片段接起来，不是改写许可证：有一段对不上就整条不算。"""
    quote = "陈默将怀表置于软布垫上……他当场跪下承认了四十年前的旧案"
    assert not d._is_grounded(quote, STAGE_TEXT)


def test_elided_fragment_may_be_slightly_rewritten_but_must_stay_grounded():
    """跳读里某一段被轻微改写不算捏造——只要那段仍与正文有足够长的逐字重合。"""
    quote = "陈默将怀表置于软布垫上，戴上目镜贴近表盘……指针正以恒定速度逆时针移动"
    assert d._is_grounded(quote, STAGE_TEXT)


def test_ellipsis_does_not_rescue_a_fabricated_quote():
    """凭空写的句子即便自带省略号，也凑不出逐字片段。"""
    assert not d._is_grounded("他跪下了……承认了一切", STAGE_TEXT)


def test_rule_issues_are_quotable():
    """提示词要求「有 rule_issues 必须重演」，那规则提示本身就必须是可引用的原文。"""
    take = {
        "events": [{"statement": "他放下怀表"}],
        "performances": [],
        "rule_issues": ["events第1条与第9条完全重复：表针逆时针"],
    }
    d._quote_check(["events第1条与第9条完全重复：表针逆时针"], d._stage_text(take))


def test_stage_text_covers_events_actions_dialogue_and_issues():
    take = {
        "events": [{"statement": "事件陈述"}],
        "performances": [{"actions": ["动作一"], "dialogue": ["台词一"]}],
        "rule_issues": ["规则冲突一"],
    }
    text = d._stage_text(take)
    assert all(part in text for part in ("事件陈述", "动作一", "台词一", "规则冲突一"))


def test_longest_common_run_counts_contiguous_characters_only():
    """只数**连续**相同：散落的字不构成引用。"""
    assert d._longest_common_run("甲乙丙", "乙丙丁") == 2
    assert d._longest_common_run("甲丙", "甲乙丙") == 1
    assert d._longest_common_run("", "任意") == 0
