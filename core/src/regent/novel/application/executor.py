"""执行器灰度：把 ``assign_executor`` / ``switch_executor_allowed`` 接进生产路径。

灰度必须是**可运行的开关**，不能是函数骨架：每个章节运行在创建时确定执行器
并钉进自己的上下文，之后任何灰度比例的调整都不得改变在途运行——否则同一章
的前半段和后半段来自两个执行器，盲评与复盘都说不清哪个方案。

生产默认臂为 ``director_script_scene``（四剧本选题 → 择优 → 逐场演绎）。
对照臂为 ``director_v2``（场景协议直写）、``director_v2_beat``（逐节拍 Hive）、
``director_script``（择优后整章执笔）。
``legacy_v1`` 已退役，不再进入 ``KNOWN_EXECUTORS``。

配置（环境变量，未配置即灰度关闭）：

- ``NOVEL_EXECUTOR_CANARY``：灰度臂的执行器名；必须是已知架构，否则忽略。
- ``NOVEL_EXECUTOR_CANARY_PERCENT``：0-100。同一作品永远落在同一桶，
  灰度期间不会来回横跳（``canary_bucket``）。
"""

from __future__ import annotations

import os

from regent.novel.application.directing_protocol import (
    ARCHITECTURE,
    BEAT_ARCHITECTURE,
)
from regent.novel.application.directing_types import (
    SCRIPT_ARCHITECTURE,
    SCRIPT_SCENE_ARCHITECTURE,
)
from regent.novel.domain import evaluation

# 生产默认：多剧本筛选后再分场；其余为对照/回退臂。
STABLE_EXECUTOR = SCRIPT_SCENE_ARCHITECTURE
SCENE_EXECUTOR = ARCHITECTURE
BEAT_EXECUTOR = BEAT_ARCHITECTURE
SCRIPT_EXECUTOR = SCRIPT_ARCHITECTURE
SCRIPT_SCENE_EXECUTOR = SCRIPT_SCENE_ARCHITECTURE
# 历史名保留只读识别；不得再被 choose_executor 选中。
LEGACY_EXECUTOR = "legacy_v1"
KNOWN_EXECUTORS = frozenset(
    {ARCHITECTURE, BEAT_ARCHITECTURE, SCRIPT_ARCHITECTURE, SCRIPT_SCENE_ARCHITECTURE}
)

# 固定版本号：策略身份与其实现一起冻结，盲评与复盘据此归因；采样可比性的
# 前提是「同版本」。改动任一策略的行为必须显式升版本，否则旧采样的结论
# 会被静默套到新实现头上。
EXECUTOR_VERSIONS: dict[str, str] = {
    ARCHITECTURE: "director_v2@2",
    BEAT_ARCHITECTURE: "director_v2@1",
    SCRIPT_ARCHITECTURE: "director_script@1",
    SCRIPT_SCENE_ARCHITECTURE: "director_script_scene@1",
    LEGACY_EXECUTOR: "legacy_v1@1",
}


def executor_version(name: str) -> str:
    """策略的固定版本标识；未知名字按 ``@0`` 处理（不参与可比采样）。"""
    return EXECUTOR_VERSIONS.get(name, f"{name}@0")

_CANARY_NAME_KEY = "NOVEL_EXECUTOR_CANARY"
_CANARY_PERCENT_KEY = "NOVEL_EXECUTOR_CANARY_PERCENT"


def canary_executor() -> str:
    """灰度臂名；未配置或不是已知架构则返回空。"""
    name = (os.environ.get(_CANARY_NAME_KEY) or "").strip()
    return name if name in KNOWN_EXECUTORS else ""


def canary_percent() -> int:
    """灰度比例。没有配置灰度臂时恒为 0——开关不在坡道上，就在坡底。"""
    if not canary_executor():
        return 0
    raw = (os.environ.get(_CANARY_PERCENT_KEY) or "0").strip()
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(0, min(100, value))


def choose_executor(work_id: object) -> str:
    """为一个新的章节运行选择执行器。"""
    canary = canary_executor()
    if not canary:
        return STABLE_EXECUTOR
    return evaluation.assign_executor(
        str(work_id),
        canary_percent=canary_percent(),
        stable_executor=STABLE_EXECUTOR,
        canary_executor=canary,
    )


DEFER_MARKER = "executor_switch_deferred"
# 一次运行的执行器身份：任何重建 generation_context 的步骤都必须带上它们，
# 否则 ASSEMBLE 一重建就把「这一章到底跑的哪个臂」抹掉了，盲评无法归因。
# executor_version 同理：版本跟着臂走，重建不得丢。
PINNED_CONTEXT_KEYS: tuple[str, ...] = ("executor", "executor_version", DEFER_MARKER)


def carry_over(context: dict[str, object] | None) -> dict[str, object]:
    """从旧上下文里取出必须活过重建的字段（只取这几项，不带别的旧状态）。"""
    return {k: v for k, v in (context or {}).items() if k in PINNED_CONTEXT_KEYS}


def pinned_executor(run: object) -> str:
    """这一次运行真正使用的执行器：创建时钉下，之后不再变。"""
    return str((run.generation_context or {}).get("executor") or STABLE_EXECUTOR)


def may_switch(*, state: str, current: str, target: str) -> bool:
    """在途运行不得切换执行器（domain/evaluation 的规则，此处是生产入口）。"""
    return evaluation.switch_executor_allowed(state, target=target, current=current)


def defer_switch(
    context: dict[str, object],
    *,
    pinned: str,
    requested: str,
    chapter_no: int,
) -> tuple[dict[str, object], bool]:
    """灰度旋钮变了，但在途运行不许换执行器——留下一次记录，继续用旧的。

    注意语义：**不许切换不等于停摆**。灰度是全局旋钮，操作员一调坡度就把所有
    落入新桶的作品的在途章节永久卡住，等于把运维开关做成了死锁；而且前后两半
    来自两个执行器的章节，盲评无法归因。所以正确处置是「这一次运行沿用旧臂，
    切换推迟到下一次运行」，并留下可查的记录。同一 run 只记录一次。
    """
    if context.get(DEFER_MARKER):
        return context, False
    context[DEFER_MARKER] = {
        "pinned": pinned,
        "requested": requested,
        "chapter_no": int(chapter_no),
    }
    return context, True
