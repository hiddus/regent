"""版本化模型价格本（Tech-Spec §6 / G-10）。

``ModelCall`` 是成本事实源，价格来自本模块而不是散落在调用点的字面量。
所有金额一律为 ``amount_minor: int``（最小货币单位）+ ``currency``，**禁止 float**。

价格以「每百万 token」计价：主流供应商单价在「每 1K token」尺度上低于 1 分，
直接按 1K 计价会被最小货币单位截断成 0，账本就失去意义。

价格本按 ``PRICE_BOOK_VERSION`` 整体版本化；未收录的模型走 ``DEFAULT_PRICE``
并把 ``model`` 原样记入 ``ModelCall``，便于事后按实际账单复核，而不是假装精确。
"""

from __future__ import annotations

from dataclasses import dataclass

from regent.novel.domain.money import DEFAULT_CURRENCY, from_major

PRICE_BOOK_VERSION = "novel-price-book-v1"

TOKENS_PER_MTOK = 1_000_000
# 估算用：中文约 1 token/字，留出结构化输出与 schema 的余量。
CHARS_PER_TOKEN = 2
DEFAULT_EXPECTED_OUTPUT_TOKENS = 1200
# 预留倍数：结算额不得超过预留额，故按估算值放大预留，事后多退少补。
DEFAULT_RESERVE_FACTOR = 3


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """每百万 token 的最小单位价格。"""

    input_minor_per_mtok: int
    output_minor_per_mtok: int
    cached_input_minor_per_mtok: int = 0
    currency: str = DEFAULT_CURRENCY


def _cny(input_per_mtok: str, output_per_mtok: str, cached_per_mtok: str = "0") -> ModelPrice:
    return ModelPrice(
        input_minor_per_mtok=from_major(input_per_mtok, "CNY"),
        output_minor_per_mtok=from_major(output_per_mtok, "CNY"),
        cached_input_minor_per_mtok=from_major(cached_per_mtok, "CNY"),
    )


PRICE_BOOK: dict[str, ModelPrice] = {
    "deepseek-chat": _cny("2", "8", "0.5"),
    "deepseek-reasoner": _cny("4", "16", "1"),
    "deepseek-v3": _cny("2", "8", "0.5"),
    "deepseek-r1": _cny("4", "16", "1"),
    "glm-4": _cny("5", "5"),
    "qwen-plus": _cny("4", "12"),
    "qwen-max": _cny("20", "60"),
    # 测试与本地桩模型：非零是为了让账本恒等式仍然可验证。
    "test": _cny("1", "3"),
}

DEFAULT_PRICE = _cny("10", "30")


def lookup(model: str) -> ModelPrice:
    key = (model or "").strip()
    if key in PRICE_BOOK:
        return PRICE_BOOK[key]
    # 供应商常返回带后缀的版本名（deepseek-chat-0628 等），按最长前缀回退。
    for known, price in sorted(PRICE_BOOK.items(), key=lambda kv: -len(kv[0])):
        if key.startswith(known):
            return price
    return DEFAULT_PRICE


def _minor(tokens: int, minor_per_mtok: int) -> int:
    """向上取整，避免把小额调用计成 0。"""
    if tokens <= 0 or minor_per_mtok <= 0:
        return 0
    return -(-int(tokens) * int(minor_per_mtok) // TOKENS_PER_MTOK)


def actual_minor(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> int:
    """按实际用量计价。缓存命中部分单独计价，最低 1 个最小单位。"""
    price = lookup(model)
    cached = min(int(cached_input_tokens), int(input_tokens))
    billed_input = max(0, int(input_tokens) - cached)
    total = (
        _minor(billed_input, price.input_minor_per_mtok)
        + _minor(cached, price.cached_input_minor_per_mtok)
        + _minor(int(output_tokens), price.output_minor_per_mtok)
    )
    return max(1, total)


def estimate_minor(
    model: str,
    *,
    prompt_chars: int = 0,
    system_chars: int = 0,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    reserve_factor: int = DEFAULT_RESERVE_FACTOR,
) -> int:
    """调用前的额度预留估算；结算后多退少补。"""
    price = lookup(model)
    prompt_tokens = -(-max(0, int(prompt_chars) + int(system_chars)) // CHARS_PER_TOKEN)
    estimate = _minor(prompt_tokens, price.input_minor_per_mtok) + _minor(
        int(expected_output_tokens), price.output_minor_per_mtok
    )
    return max(1, estimate) * max(1, int(reserve_factor))


__all__ = [
    "DEFAULT_EXPECTED_OUTPUT_TOKENS",
    "DEFAULT_PRICE",
    "DEFAULT_RESERVE_FACTOR",
    "PRICE_BOOK",
    "PRICE_BOOK_VERSION",
    "ModelPrice",
    "actual_minor",
    "estimate_minor",
    "lookup",
]
