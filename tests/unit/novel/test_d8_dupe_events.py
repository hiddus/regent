"""缺陷 8：结算回吐已有事件，``extend`` 重复累加。

真机样本（作品 636b93ed）：6 条事件跑完 3 个节拍变成 22 条，三组完全相同。
重复事件会喂出重复正文、触发规则冲突、迫使导演重演，最后卡死整章——而根因
只是一句 ``extend``。

去重按**陈述原文**：同一句陈述就是同一件事，第二次入场只会污染上下文。
"""

from __future__ import annotations

from regent.novel.application import direction as d


def _event(statement: str, known_by=()):
    return d.SceneEvent(
        statement=statement, known_by=list(known_by), reader_visible=True
    )


def test_echoed_events_are_not_accumulated():
    """模型把看过的事件原样回吐时，不该再入场一次。"""
    take = {"events": [{"statement": "他放下怀表"}]}
    d._extend_events(take, [_event("他放下怀表"), _event("他取下目镜")])
    assert [e["statement"] for e in take["events"]] == ["他放下怀表", "他取下目镜"]


def test_repeated_echoes_across_beats_stay_at_one_copy():
    """真机形态：同一组 6 条事件回吐 3 次，仍然只有 6 条。"""
    take = {"events": []}
    beat = [_event(f"事件{i}") for i in range(6)]
    for _ in range(3):
        d._extend_events(take, list(beat))
    assert len(take["events"]) == 6


def test_duplicates_within_one_beat_are_collapsed():
    take = {"events": []}
    d._extend_events(take, [_event("同一件事"), _event("同一件事")])
    assert len(take["events"]) == 1


def test_new_events_still_land_and_order_is_preserved():
    take = {"events": [{"statement": "第一件"}]}
    d._extend_events(take, [_event("第二件"), _event("第三件")])
    assert [e["statement"] for e in take["events"]] == ["第一件", "第二件", "第三件"]


def test_other_event_fields_survive_the_merge():
    """去重只按陈述，不能把知情范围、台词这些字段丢掉。"""
    take = {"events": []}
    d._extend_events(
        take,
        [
            d.SceneEvent(
                statement="他承认了",
                known_by=["陈渡"],
                reader_visible=False,
                dialogue_by_character={"陈渡": ["是我"]},
            )
        ],
    )
    assert take["events"][0]["known_by"] == ["陈渡"]
    assert take["events"][0]["reader_visible"] is False
    assert take["events"][0]["dialogue_by_character"] == {"陈渡": ["是我"]}
