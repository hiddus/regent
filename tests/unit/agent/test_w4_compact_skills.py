"""W4-P0/P1 contract tests: CJK token estimate + Chinese skill routing."""

from __future__ import annotations

from regent.agent.compact import (
    ContextCompactor,
    estimate_text_tokens,
    estimate_tokens,
)
from regent.agent.skills import select_skills_for_goal
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import ChatMessage


def test_cjk_token_estimate_not_under_ascii_rule() -> None:
    zh = "中国历史人物全集网站" * 20
    ascii_only = "abcd" * 80
    zh_est = estimate_text_tokens(zh)
    ascii_est = estimate_text_tokens(ascii_only)
    # Old bug: len//4 → Chinese underestimated ~4×.
    assert zh_est >= len(zh) * 0.8
    assert ascii_est <= len(ascii_only) // 3 + 5
    mixed = estimate_tokens(
        [ChatMessage(role="user", content=zh + "\n" + ascii_only)]
    )
    assert mixed >= zh_est


def test_provider_prompt_tokens_calibrate_scale(tmp_path) -> None:  # noqa: ANN001
    toolkit = WorkspaceToolkit(tmp_path)
    compactor = ContextCompactor(toolkit=toolkit, context_window_tokens=8_000)
    msgs = [ChatMessage(role="user", content="你好世界" * 100)]
    raw = estimate_tokens(msgs)
    compactor.observe_provider_prompt_tokens(estimated=raw, actual_prompt_tokens=raw * 2)
    assert compactor.state.token_scale > 1.2
    assert compactor.calibrated_estimate(msgs) > raw


def test_chinese_goals_get_skills() -> None:
    goals = [
        "中国历史人物全集",
        "城市噪音地图",
        "待办笔记系统",
        "开放数据上传平台",
        "本地生活服务黄页",
    ]
    hits = sum(1 for g in goals if select_skills_for_goal(g))
    assert hits / len(goals) >= 0.7
