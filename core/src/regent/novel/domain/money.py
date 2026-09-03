"""金额最小单位（Tech-Spec §6 / G-10）。

规则：
- 金额一律 ``amount_minor: int``（最小货币单位，如人民币「分」）+ ``currency: str``（ISO 4217）。
- **禁止 float**。任何 float 入口都会被拒绝，避免静默精度丢失。
- 支持 ``from_major(str | Decimal)`` 显式换算，禁止从 float 构造。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# ISO 4217 最小单位指数：0 表示无小数（日元），2 表示分（人民币/美元）。
_CURRENCY_EXPONENT: dict[str, int] = {
    "CNY": 2,
    "USD": 2,
    "EUR": 2,
    "HKD": 2,
    "TWD": 2,
    "GBP": 2,
    "SGD": 2,
    "JPY": 0,
    "KRW": 0,
}

DEFAULT_CURRENCY = "CNY"


class MoneyError(ValueError):
    """金额构造或运算错误。"""


def supported_currencies() -> tuple[str, ...]:
    return tuple(sorted(_CURRENCY_EXPONENT))


def currency_exponent(currency: str) -> int:
    code = (currency or "").upper()
    if code not in _CURRENCY_EXPONENT:
        raise MoneyError(f"unsupported currency: {currency!r}")
    return _CURRENCY_EXPONENT[code]


def from_major(amount: str | int | Decimal, currency: str = DEFAULT_CURRENCY) -> int:
    """把主单位金额换算为最小单位整数。

    只接受 ``str`` / ``int`` / ``Decimal``。**明确拒绝 float**——
    0.1 这类二进制浮点无法精确表示，换算会静默截断。
    """
    if isinstance(amount, float):
        raise MoneyError("refusing float money amount; use str/int/Decimal")
    if isinstance(amount, bool):
        raise MoneyError("refusing bool as money amount")
    code = (currency or "").upper()
    exponent = currency_exponent(code)
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"invalid money amount: {amount!r}") from exc
    scaled = value * (10**exponent)
    if scaled != scaled.to_integral_value():
        raise MoneyError(
            f"amount {amount!r} has more precision than {code} minor unit (10^-{exponent})"
        )
    minor = int(scaled)
    if minor < 0:
        raise MoneyError("money amount must be non-negative")
    return minor


def to_major(amount_minor: int, currency: str = DEFAULT_CURRENCY) -> Decimal:
    """最小单位 → 主单位（仅用于展示，绝不用于计算或存储）。"""
    exponent = currency_exponent(currency)
    return (Decimal(int(amount_minor)) / (10**exponent)).quantize(Decimal(1).scaleb(-exponent))


def format_minor(amount_minor: int, currency: str = DEFAULT_CURRENCY) -> str:
    return f"{to_major(amount_minor, currency)} {currency.upper()}"


def validate_pair(amount_minor: Any, currency: Any) -> tuple[int, str]:
    """校验持久化/传输层的 (amount_minor, currency) 组合。"""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise MoneyError("amount_minor must be int (minor units)")
    if amount_minor < 0:
        raise MoneyError("amount_minor must be non-negative")
    if not isinstance(currency, str) or len(currency) != 3:
        raise MoneyError("currency must be a 3-letter ISO 4217 code")
    currency_exponent(currency)
    return amount_minor, currency.upper()


def add(*amounts_minor: int) -> int:
    return sum(int(a) for a in amounts_minor)


def allocate_evenly(total_minor: int, weights: list[int]) -> list[int]:
    """按权重分配金额，余数逐个补齐，保证 sum(result) == total（不丢分）。"""
    if not weights:
        return []
    if any(w < 0 for w in weights):
        raise MoneyError("weights must be non-negative")
    total_weight = sum(weights)
    if total_weight == 0:
        raise MoneyError("total weight must be positive")
    base = [total_minor * w // total_weight for w in weights]
    remainder = total_minor - sum(base)
    idx = 0
    while remainder > 0:
        base[idx % len(base)] += 1
        remainder -= 1
        idx += 1
    return base
