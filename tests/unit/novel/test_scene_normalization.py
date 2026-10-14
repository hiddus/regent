"""分场格式程序收口：deduplicate / unique_ids / cap_scenes 纯函数单测。"""

from __future__ import annotations

from regent.novel.domain.scene_card import (
    cap_scenes_and_merge,
    deduplicate_cross_scene_beats,
    ensure_unique_beat_ids,
    ensure_unique_scene_ids,
)


def _beat(beat_id: str, text: str) -> dict:
    return {"beat_id": beat_id, "text": text, "scene_slot": 0}


def _card(scene_id: str, beats: list[dict], event_refs: list[dict] | None = None, purpose: str = "") -> dict:
    return {
        "scene_id": scene_id,
        "beats": beats,
        "event_refs": event_refs or [],
        "purpose": purpose,
    }


class TestDeduplicateCrossSceneBeats:
    def test_removes_later_scene_duplicate(self):
        long_text = "这是一段足够长的文本用来测试跨场景节拍去重功能是否正常工作，需要超过二十四个字符"
        cards = [
            _card("s1", [_beat("b1", long_text)]),
            _card("s2", [_beat("b2", long_text)]),
        ]
        result = deduplicate_cross_scene_beats(cards)
        assert len(result[0]["beats"]) == 1
        assert len(result[1]["beats"]) == 0

    def test_keeps_short_beats_even_if_identical(self):
        cards = [
            _card("s1", [_beat("b1", "短文本")]),
            _card("s2", [_beat("b2", "短文本")]),
        ]
        result = deduplicate_cross_scene_beats(cards)
        assert len(result[0]["beats"]) == 1
        assert len(result[1]["beats"]) == 1

    def test_keeps_same_scene_duplicates(self):
        long_text = "这是一段足够长的文本用来测试同场景内节拍不去重，需要超过二十四个字符才行"
        cards = [
            _card("s1", [_beat("b1", long_text), _beat("b2", long_text)]),
        ]
        result = deduplicate_cross_scene_beats(cards)
        assert len(result[0]["beats"]) == 2

    def test_containment_triggers_dedup(self):
        short = "张三在书房里发现了一封密信并交给了李四处理这件事情"
        long = "张三在书房里发现了一封密信并交给了李四处理这件事情，随后李四将密信转交给了王五"
        cards = [
            _card("s1", [_beat("b1", long)]),
            _card("s2", [_beat("b2", short)]),
        ]
        result = deduplicate_cross_scene_beats(cards)
        assert len(result[0]["beats"]) == 1
        assert len(result[1]["beats"]) == 0

    def test_different_beats_both_kept(self):
        text_a = "张三在书房里发现了一封密信并交给了李四处理"
        text_b = "王五在花园里种下了一棵桂花树等待春天到来"
        cards = [
            _card("s1", [_beat("b1", text_a)]),
            _card("s2", [_beat("b2", text_b)]),
        ]
        result = deduplicate_cross_scene_beats(cards)
        assert len(result[0]["beats"]) == 1
        assert len(result[1]["beats"]) == 1


class TestEnsureUniqueSceneIds:
    def test_adds_suffix_to_duplicate_scene_ids(self):
        cards = [
            _card("s1", []),
            _card("s1", []),
            _card("s1", []),
        ]
        result = ensure_unique_scene_ids(cards)
        assert result[0]["scene_id"] == "s1"
        assert result[1]["scene_id"] == "s1_1"
        assert result[2]["scene_id"] == "s1_2"

    def test_no_change_when_all_unique(self):
        cards = [
            _card("s1", []),
            _card("s2", []),
        ]
        result = ensure_unique_scene_ids(cards)
        assert result[0]["scene_id"] == "s1"
        assert result[1]["scene_id"] == "s2"


class TestEnsureUniqueBeatIds:
    def test_adds_suffix_to_duplicate_beat_ids(self):
        cards = [
            _card("s1", [_beat("b1", "甲"), _beat("b1", "乙")]),
            _card("s2", [_beat("b1", "丙")]),
        ]
        result = ensure_unique_beat_ids(cards)
        ids = [b["beat_id"] for c in result for b in c["beats"]]
        assert len(ids) == len(set(ids))
        assert ids == ["b1", "b1_1", "b1_2"]

    def test_no_change_when_all_unique(self):
        cards = [
            _card("s1", [_beat("b1", "甲"), _beat("b2", "乙")]),
        ]
        result = ensure_unique_beat_ids(cards)
        assert result[0]["beats"][0]["beat_id"] == "b1"
        assert result[0]["beats"][1]["beat_id"] == "b2"


class TestCapScenesAndMerge:
    def test_no_change_when_within_limit(self):
        cards = [_card(f"s{i}", [_beat(f"b{i}", f"文本{i}")]) for i in range(4)]
        result = cap_scenes_and_merge(cards, max_scenes=4)
        assert len(result) == 4

    def test_merges_excess_into_last_retained(self):
        cards = [
            _card("s1", [_beat("b1", "文本一")], purpose="目的1"),
            _card("s2", [_beat("b2", "文本二")], purpose="目的2"),
            _card("s3", [_beat("b3", "文本三")], purpose="目的3"),
            _card("s4", [_beat("b4", "文本四")], purpose="目的4"),
            _card("s5", [_beat("b5", "文本五")], purpose="目的5"),
        ]
        result = cap_scenes_and_merge(cards, max_scenes=4)
        assert len(result) == 4
        last_beats = result[3]["beats"]
        assert len(last_beats) == 2
        assert last_beats[0]["beat_id"] == "b4"
        assert last_beats[1]["beat_id"] == "b5"

    def test_merges_occurrence_to_reference_when_duplicate(self):
        cards = [
            _card("s1", [], event_refs=[
                {"event_id": "e1", "role": "occurrence"},
            ]),
            _card("s2", [], event_refs=[
                {"event_id": "e2", "role": "occurrence"},
            ]),
            _card("s3", [], event_refs=[
                {"event_id": "e3", "role": "occurrence"},
            ]),
            _card("s4", [], event_refs=[
                {"event_id": "e4", "role": "occurrence"},
            ]),
            _card("s5", [], event_refs=[
                {"event_id": "e1", "role": "occurrence"},
                {"event_id": "e5", "role": "occurrence"},
            ]),
        ]
        result = cap_scenes_and_merge(cards, max_scenes=4)
        assert len(result) == 4
        last_refs = result[3]["event_refs"]
        e1_ref = [r for r in last_refs if r["event_id"] == "e1"][0]
        assert e1_ref["role"] == "reference"
        e5_ref = [r for r in last_refs if r["event_id"] == "e5"][0]
        assert e5_ref["role"] == "occurrence"

    def test_purpose_merged(self):
        cards = [
            _card("s1", [], purpose="目的1"),
            _card("s2", [], purpose="目的2"),
            _card("s3", [], purpose="目的3"),
            _card("s4", [], purpose="目的4"),
            _card("s5", [], purpose="目的5"),
        ]
        result = cap_scenes_and_merge(cards, max_scenes=4)
        assert "目的4" in result[3]["purpose"]
        assert "目的5" in result[3]["purpose"]
