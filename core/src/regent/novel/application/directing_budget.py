"""导演调用预算上限与剩余额度（纯函数）。"""

from __future__ import annotations

from typing import Any

# 货币预算上限（分），与调用次数上限互不替代：次数管行为，金额管钱。
MAX_COST_MINOR = 20_000
CURRENCY = "CNY"
# 旧的多场链路仍用这个上限。短链不共用：追读自评 1 次、章纲至多 4 份、同一招判定 2 次、
# 正文和重写 2 次、改稿 2 次、这三稿各自可能超长要删一次段 3 次、事实抽取连同一次重试 2 次。
MAX_CALLS = 120
CHAPTER_MAX_CALLS = 16
# Harness² 递归臂：一章至多 H2_MAX_ROUNDS 轮，每轮 PROPOSE + 2×PROBE + COMPOSE
# + 通用条目的专有性筛查 + 后继正文，加上交付稿超长时的一次删段；
# 另有一次判正文写没写到章纲结果。
H2_MAX_ROUNDS = 2
H2_CALLS_PER_ROUND = 7
H2_LANDING_CALLS = 1
CHAPTER_H2_MAX_CALLS = CHAPTER_MAX_CALLS + H2_MAX_ROUNDS * H2_CALLS_PER_ROUND + H2_LANDING_CALLS


def effective_call_cap(production: dict[str, Any], *, base_calls: int | None = None) -> int:
    if base_calls is None:
        protocol = str((production or {}).get("protocol") or "")
        executor = str((production or {}).get("executor") or "")
        if protocol == "chapter":
            base_calls = (
                CHAPTER_H2_MAX_CALLS if executor == "director_chapter_h2" else CHAPTER_MAX_CALLS
            )
        else:
            base_calls = MAX_CALLS
    return int(base_calls) + int(production.get("budget_grant_calls", 0) or 0)


def effective_cost_cap(production: dict[str, Any], *, base_cost_minor: int = MAX_COST_MINOR) -> int:
    return int(base_cost_minor) + int(production.get("budget_grant_cost_minor", 0) or 0)


def remaining_calls(production: dict[str, Any], *, base_calls: int = MAX_CALLS) -> int:
    return effective_call_cap(production, base_calls=base_calls) - int(
        production.get("call_count", 0) or 0
    )


def remaining_cost_minor(
    production: dict[str, Any], *, base_cost_minor: int = MAX_COST_MINOR
) -> int:
    committed = int(production.get("committed_minor", 0) or 0)
    return effective_cost_cap(production, base_cost_minor=base_cost_minor) - committed
