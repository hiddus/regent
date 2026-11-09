"""类型原则透镜：问题类别 → 可复用推理问题，而非某本作的写死禁令。

用法：
1. 按方向关键词激活透镜（embodiment / identity / power_system / …）
2. 由「本作公约」Agent 结合 premise、双小传，为本作推导具体 ``work_conventions``
3. 导演/呈现/LLM 审校按**本作推导结果**执行；禁止把某一本书的梗（裤管、鱼塘名）写进引擎

确定性 regex 只留给真正机械的器物 UX（如 IM 列表密度）；叙事/设定类一律交给 Agent 推理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class PrincipleLens:
    lens_id: str
    category: str
    # 何时激活（给人/Agent 看的说明，不是关键词正则）
    when: str
    # Agent 必须为本作回答的问题
    questions: tuple[str, ...]
    # 抽象失败模式（类别，不是某书的具体句子）
    anti_patterns: tuple[str, ...]
    triggers: tuple[str, ...]  # 方向关键词触发


_LENSES: tuple[PrincipleLens, ...] = (
    PrincipleLens(
        lens_id="host_world_continuity",
        category="embodiment",
        when="意识进入既有身体（穿越/换身/附身/性转占据等）",
        questions=(
            "本场身体所属世界里，哪些物理默认属于『这具身体』（衣着、肌肉、空间尺度）？",
            "发现『身体不对』应走哪条感知链？哪些旧世界线索不能当作第一证据？",
        ),
        anti_patterns=(
            "用旧身份世界的器物尺码/习惯，当作证明已换身的第一证据",
            "把宿主身体上本应合身的东西写成『不合身所以才发现换身』",
        ),
        triggers=("穿越", "穿书", "性转", "换身", "附身", "重生"),
    ),
    PrincipleLens(
        lens_id="sensorimotor_primacy",
        category="embodiment",
        when="苏醒/初次意识到身体与原认知不一致",
        questions=(
            "大脑/神经/潜意识身体图式的第一冲突是什么？",
            "视觉与触摸如何作为确认，而不是唯一发现途径？",
        ),
        anti_patterns=(
            "几乎只靠照镜或外貌清单完成发现，缺少体感/平衡/神经冲突",
            "把确认手段写成第一时间答案",
        ),
        triggers=("穿越", "穿书", "性转", "换身", "附身", "重生"),
    ),
    PrincipleLens(
        lens_id="dual_identity_ledger",
        category="identity",
        when="存在『穿越者意识』与『原身/宿主躯壳』两套身份",
        questions=(
            "穿越者前史是什么（职业、死因/契机、声纹、目标；须有可读称呼，不能只叫代号）？",
            "原身对外身份是什么（资源、为何能独自活动、过人之处或金丝笼风险）？",
            "外挂/系统面板如何区分『躯壳登记名』与『当前操控意识』，且读者一眼能把操控者认作主角？",
        ),
        anti_patterns=(
            "把穿越者的死亡/前职写进原身档案或系统宿主栏",
            "高颜值且能独自活动却无过人之处/处境解释，导致金丝笼穿帮",
            "系统用『操作员』『操作员A/用户001』等代号指代穿越者，读者认不出这是主角",
            "面板同时甩两套互不映射的名字，像多个编剧各写各的",
        ),
        triggers=("穿越", "穿书", "性转", "换身", "系统流", "都市文娱"),
    ),
    PrincipleLens(
        lens_id="power_as_toolkit",
        category="power_system",
        when="存在系统/金手指/抽奖池/技能库类外挂",
        questions=(
            "本作外挂的资源池叫什么、抽什么、边界是什么？",
            "绑定/觉醒后，针对眼前最紧要困境的第一份可选项（抽取/奖励）是什么？",
            "系统首次弹出时，用哪一句接入/绑定提示让读者立刻明白『这是什么、为何出现、跟谁绑定』？",
            "压力与代价如何服务『用不用工具破局』，而不是把工具写成勒死主角的项圈？",
        ),
        anti_patterns=(
            "把资源池主规则写成『池子断了就处决』且不提供抽取/破局玩法",
            "绑定后只甩恐吓规则、迟迟不给针对困境的首份可选项",
            "被选中却看不出资源池规模/质量异常",
            "系统无声突然刷屏，缺少一句可读的接入/绑定提示",
        ),
        triggers=("系统流", "系统", "金手指", "爽文"),
    ),
)


def resolve_lenses(keywords: Iterable[str] | None) -> list[PrincipleLens]:
    tokens = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not tokens:
        return []
    out: list[PrincipleLens] = []
    seen: set[str] = set()
    for lens in _LENSES:
        if lens.lens_id in seen:
            continue
        if any(
            t in tokens or any(t in kw or kw in t for kw in tokens) for t in lens.triggers
        ):
            seen.add(lens.lens_id)
            out.append(lens)
    return out


def lenses_as_prompt_block(lenses: Sequence[PrincipleLens] | None) -> str:
    rows = list(lenses or [])
    if not rows:
        return ""
    parts = [
        "原则透镜（先回答问题，再为本作落地；禁止照搬其他书的具体梗）："
    ]
    for lens in rows:
        parts.append(f"### {lens.lens_id}（{lens.category}）")
        parts.append(f"何时：{lens.when}")
        parts.append("须回答：")
        for q in lens.questions:
            parts.append(f"- {q}")
        parts.append("避免：")
        for a in lens.anti_patterns:
            parts.append(f"- {a}")
    return "\n".join(parts)


def lenses_payload(lenses: Sequence[PrincipleLens] | None) -> list[dict[str, Any]]:
    return [
        {
            "lens_id": lens.lens_id,
            "category": lens.category,
            "when": lens.when,
            "questions": list(lens.questions),
            "anti_patterns": list(lens.anti_patterns),
        }
        for lens in (lenses or [])
    ]
