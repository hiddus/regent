"""执行器灰度：把 ``assign_executor`` / ``switch_executor_allowed`` 接进生产路径。

灰度必须是**可运行的开关**，不能是函数骨架：每个章节运行在创建时确定执行器
并钉进自己的上下文，之后任何灰度比例的调整都不得改变在途运行——否则同一章
的前半段和后半段来自两个执行器，盲评与复盘都说不清哪个方案。

配置（环境变量，未配置即灰度关闭）：

- ``NOVEL_EXECUTOR_CANARY``：灰度臂的执行器名；必须是已知架构，否则忽略。
- ``NOVEL_EXECUTOR_CANARY_PERCENT``：0-100。同一作品永远落在同一桶，
  灰度期间不会来回横跳（``canary_bucket``）。
"""

from __future__ import annotations

import os

from regent.novel.application.direction import ARCHITECTURE
from regent.novel.domain import evaluation

STABLE_EXECUTOR = ARCHITECTURE
# 只有已知的成文架构才能当灰度臂：未知名字会让整章走进无法重放的路径。
KNOWN_EXECUTORS = frozenset({ARCHITECTURE})

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
PINNED_CONTEXT_KEYS: tuple[str, ...] = ("executor", DEFER_MARKER)


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
