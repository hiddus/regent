"""导演调用预算上限与剩余额度（纯函数）。"""

from __future__ import annotations

from typing import Any

# 货币预算上限（分），与调用次数上限互不替代：次数管行为，金额管钱。
MAX_COST_MINOR = 20_000
CURRENCY = "CNY"
# 单一权威；direction.py 仅兼容再导出。
MAX_CALLS = 120


def effective_call_cap(production: dict[str, Any], *, base_calls: int = MAX_CALLS) -> int:
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
