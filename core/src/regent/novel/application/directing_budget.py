"""导演调用预算上限与剩余额度（纯函数）。"""

from __future__ import annotations

from typing import Any

# 货币预算上限（分），与调用次数上限互不替代：次数管行为，金额管钱。
MAX_COST_MINOR = 20_000
CURRENCY = "CNY"
# 旧的多场链路仍用这个上限。短链不共用：追读自评 1 次、章纲至多 4 份、同一招判定 2 次、
# 正文和重写 2 次、改稿 2 次、事实抽取连同一次重试 2 次。
MAX_CALLS = 120
CHAPTER_MAX_CALLS = 13


def effective_call_cap(production: dict[str, Any], *, base_calls: int | None = None) -> int:
    if base_calls is None:
        base_calls = (
            CHAPTER_MAX_CALLS
            if str((production or {}).get("protocol") or "") == "chapter"
            else MAX_CALLS
        )
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
