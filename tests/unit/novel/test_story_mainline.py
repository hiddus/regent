from regent.novel.domain.scene_card import _text_covers
from regent.novel.domain.story_mainline import (
    mainline_extension_due,
    mainline_for_chapter,
)


class _Node:
    def __init__(self, ordinal: int, title: str) -> None:
        self.node_id = f"n{ordinal:02d}"
        self.ordinal = ordinal
        self.title = title
        self.node_type = "ESCALATION"
        self.promise = title
        self.preconditions = []
        self.consequences = ["留下后果"]


def test_extension_starts_after_two_thirds():
    assert mainline_extension_due(1, 12) is False
    assert mainline_extension_due(24, 12) is False
    assert mainline_extension_due(25, 12) is True
    assert mainline_extension_due(25, 0) is False


def test_chapter_slice_points_at_the_current_node():
    nodes = [_Node(i, f"节点{i}") for i in range(1, 5)]
    sliced = mainline_for_chapter(nodes, 4)
    assert sliced["current"]["title"] == "节点2"
    assert sliced["previous"]["title"] == "节点1"
    assert sliced["following"]["title"] == "节点3"


def test_extension_does_not_modify_existing_nodes():
    """续写只追加新节点，不改已走过节点的 payload。"""
    nodes = [_Node(i, f"节点{i}") for i in range(1, 13)]
    before = [n.title for n in nodes]
    sliced_before = mainline_for_chapter(nodes, 24)
    assert mainline_extension_due(25, 12) is True
    sliced_after = mainline_for_chapter(nodes, 24)
    assert sliced_before["current"]["title"] == sliced_after["current"]["title"]
    assert [n.title for n in nodes] == before


class TestTextCovers:
    def test_exact_substring_match(self):
        assert _text_covers("张三发现了密信", ["张三发现了密信并交给了李四"]) is True

    def test_no_match(self):
        assert _text_covers("张三发现了密信", ["李四在花园种花"]) is False

    def test_empty_target_always_covered(self):
        assert _text_covers("", ["任何文本"]) is True

    def test_empty_corpus_not_covered(self):
        assert _text_covers("有内容", []) is False

    def test_fragment_matching_for_long_targets(self):
        target = "这是一段非常长的目标文本需要拆分成多个窗口来检测是否被覆盖"
        corpus = ["前面有些铺垫，然后这是一段非常长的目标文本需要拆分成多个窗口来检测是否被覆盖，后面还有后续"]
        assert _text_covers(target, corpus) is True

    def test_bidirectional_containment(self):
        assert _text_covers("短文本", ["这是一段包含短文本的较长内容"]) is True
        assert _text_covers("这是一段包含短文本的较长内容", ["短文本"]) is True

