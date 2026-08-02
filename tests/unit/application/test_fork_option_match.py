"""Deterministic fork option matching (run-think-learn L2)."""

from regent.application.app_guidance_service import _match_fork_option


OPTIONS = [
    {"id": "explore_thin", "label": "先做最薄可验证原型", "description": "x"},
    {"id": "clarify_scope", "label": "先收窄范围再做", "description": "y"},
]


def test_match_by_option_id_prefix() -> None:
    hit = _match_fork_option("option:explore_thin 先做最薄可验证原型", OPTIONS)
    assert hit is not None
    assert hit["id"] == "explore_thin"


def test_match_by_label_substring() -> None:
    hit = _match_fork_option("我选先收窄范围再做", OPTIONS)
    assert hit is not None
    assert hit["id"] == "clarify_scope"


def test_match_by_index() -> None:
    hit = _match_fork_option("1", OPTIONS)
    assert hit is not None
    assert hit["id"] == "explore_thin"


def test_match_miss() -> None:
    assert _match_fork_option("随便聊聊天气", OPTIONS) is None
