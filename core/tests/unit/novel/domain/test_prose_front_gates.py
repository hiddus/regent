"""Tests for prose_front_gates: coerce semantics + REVIEW weak matching."""

from __future__ import annotations

from regent.novel.domain.prose_front_gates import (
    power_surface_fails_review,
    review_issues_may_coerce,
)


class TestReviewIssuesMayCoerce:
    """review_issues_may_coerce: 只认结构化顾问标签，递延债务不再放行。"""

    def test_empty_issues_coercible(self) -> None:
        assert review_issues_may_coerce([]) is True
        assert review_issues_may_coerce(None) is True

    def test_bare_pov_not_coercible(self) -> None:
        assert review_issues_may_coerce(["[pov:shift]"]) is False

    def test_bare_redundant_not_coercible(self) -> None:
        assert review_issues_may_coerce(["[front:redundant] 复读"]) is False

    def test_bare_power_surface_coercible(self) -> None:
        assert review_issues_may_coerce(["[front:power_surface] 缺金手指"]) is True

    def test_gate_debt_label_no_longer_coercible(self) -> None:
        """gate_debt 递延已废除（88ae2c2）：债务标签一律硬停，不许 coerce。"""
        assert review_issues_may_coerce(["[gate_debt:prose_front][front:power_surface] x"]) is False
        assert review_issues_may_coerce(["[gate_debt:cont] 连续性缺失"]) is False

    def test_front_title_not_coercible(self) -> None:
        assert review_issues_may_coerce(["[front:title] 标题缺失"]) is False

    def test_front_banned_not_coercible(self) -> None:
        assert review_issues_may_coerce(["[front:banned] 禁用词"]) is False


class TestPowerSurfaceFailsReview:
    """power_surface_fails_review: 严格标记 + 弱匹配兜底。"""

    def _packet(self, power_mechanism: str = "系统") -> dict:
        return {"script": {"power_mechanism": power_mechanism}}

    def test_no_power_blob_chapter_not_1(self) -> None:
        fails = power_surface_fails_review(
            "正文内容",
            production_packet={},
            chapter_no=2,
        )
        assert fails == []

    def test_strict_marker_present(self) -> None:
        fails = power_surface_fails_review(
            "他打开了面板查看进度",
            production_packet=self._packet("系统"),
            chapter_no=1,
        )
        assert fails == []

    def test_strict_marker_absent_no_weak(self) -> None:
        fails = power_surface_fails_review(
            "他走在路上，想着心事",
            production_packet=self._packet("系统"),
            chapter_no=1,
        )
        assert len(fails) == 1
        assert "[front:power_surface]" in fails[0]

    def test_weak_phrase_repanish_passes(self) -> None:
        fails = power_surface_fails_review(
            "他感到经脉反噬，一口鲜血喷出",
            production_packet=self._packet("系统"),
            chapter_no=1,
        )
        assert fails == []

    def test_weak_phrase_depletion_passes(self) -> None:
        fails = power_surface_fails_review(
            "灵力枯竭，他再也无法动弹",
            production_packet=self._packet("冷却"),
            chapter_no=1,
        )
        assert fails == []

    def test_weak_phrase_exhaustion_passes(self) -> None:
        fails = power_surface_fails_review(
            "力竭吐血，他跪倒在地",
            production_packet=self._packet("抽取"),
            chapter_no=1,
        )
        assert fails == []

    def test_generic_negation_not_weak_match(self) -> None:
        fails = power_surface_fails_review(
            "他不能放弃，无法停下脚步",
            production_packet=self._packet("系统"),
            chapter_no=1,
        )
        assert len(fails) == 1
        assert "[front:power_surface]" in fails[0]

    def test_alias_match_passes(self) -> None:
        fails = power_surface_fails_review(
            "他暂时失灵，耳朵听不见了",
            production_packet=self._packet("冷却"),
            chapter_no=1,
        )
        assert fails == []
