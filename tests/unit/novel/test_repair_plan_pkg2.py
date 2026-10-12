"""工作包2：连续性台账数据模型与更新规则。"""

from __future__ import annotations

from regent.novel.domain.continuity_ledger import (
    LIFECYCLE_DAMAGED,
    LIFECYCLE_DESTROYED,
    LIFECYCLE_INTACT,
    apply_delta,
    check_leave_once_per_visit,
    check_one_time_event_unique,
    check_partition_unchanged,
    check_prop_readable,
    check_resource_not_double_consumed,
    empty_ledger,
    ledger_to_text_summary,
    ledger_to_working_state,
    ledger_to_text_summary,
    record_ability_read,
    scope_key_ability_read,
    scope_key_leave,
)


def test_empty_ledger_has_expected_structure():
    ledger = empty_ledger()
    assert ledger["props"] == {}
    assert ledger["resources"] == {}
    assert ledger["one_time_events"] == {}
    assert ledger["actor_positions"] == {}
    assert ledger["version"] == 0


def test_prop_destroyed_blocks_reading():
    ledger = empty_ledger()
    ledger["props"]["watch"] = {
        "prop_id": "watch",
        "name": "怀表",
        "position": "钟表行",
        "holder": "",
        "lifecycle": LIFECYCLE_DESTROYED,
        "partitions": {},
        "read_status": {},
    }
    errs = check_prop_readable(ledger, "watch")
    assert "prop_destroyed:watch" in errs


def test_ability_read_duplicate_blocked():
    ledger = empty_ledger()
    ledger["props"]["gear"] = {
        "prop_id": "gear",
        "name": "齿轮",
        "lifecycle": LIFECYCLE_INTACT,
        "partitions": {},
        "read_status": {"ability": {"count": 1}},
    }
    errs = check_prop_readable(ledger, "gear")
    assert "ability_read_duplicate:gear" in errs


def test_ability_read_first_time_allowed():
    ledger = empty_ledger()
    ledger["props"]["gear"] = {
        "prop_id": "gear",
        "name": "齿轮",
        "lifecycle": LIFECYCLE_INTACT,
        "partitions": {},
        "read_status": {},
    }
    assert check_prop_readable(ledger, "gear") == []
    new_ledger, errs = record_ability_read(ledger, "gear", scene_id="s1")
    assert errs == []
    assert new_ledger["props"]["gear"]["read_status"]["ability"]["count"] == 1


def test_partition_text_cannot_change_without_evidence():
    ledger = empty_ledger()
    ledger["props"]["watch"] = {
        "prop_id": "watch",
        "name": "怀表",
        "lifecycle": LIFECYCLE_INTACT,
        "partitions": {"铭文": "时间不回头"},
        "read_status": {},
    }
    errs = check_partition_unchanged(ledger, "watch", "铭文", "时间会回头")
    assert "partition_changed:watch:铭文" in errs
    assert check_partition_unchanged(ledger, "watch", "铭文", "时间不回头") == []


def test_resource_double_consume_blocked():
    ledger = empty_ledger()
    ledger["resources"]["memory"] = {
        "resource_id": "memory",
        "name": "亲忆",
        "consumed": [{"entry": "祖母的摇篮曲", "scene_id": "s1"}],
    }
    errs = check_resource_not_double_consumed(ledger, "memory", "祖母的摇篮曲")
    assert "resource_double_consume:memory:祖母的摇篮曲" in errs
    assert check_resource_not_double_consumed(ledger, "memory", "新的条目") == []


def test_one_time_event_duplicate_blocked():
    ledger = empty_ledger()
    key = scope_key_ability_read("watch", "default")
    ledger["one_time_events"][key] = {"event_type": "ability_read", "scene_id": "s1"}
    errs = check_one_time_event_unique(ledger, key)
    assert f"one_time_event_duplicate:{key}" in errs


def test_leave_once_per_visit():
    ledger = empty_ledger()
    ledger["actor_positions"]["沈寻"] = {
        "location": "钟表行",
        "visit_round": 1,
    }
    key = scope_key_leave("沈寻", "钟表行", 1)
    ledger["one_time_events"][key] = {"event_type": "leave", "scene_id": "s1"}
    errs = check_leave_once_per_visit(ledger, "沈寻", "钟表行")
    assert any("one_time_event_duplicate" in e for e in errs)


def test_apply_delta_atomic_on_violation():
    ledger = empty_ledger()
    ledger["props"]["watch"] = {
        "prop_id": "watch",
        "name": "怀表",
        "lifecycle": LIFECYCLE_DESTROYED,
        "partitions": {},
        "read_status": {},
    }
    original_version = ledger["version"]
    delta = {
        "prop_updates": [
            {"prop_id": "watch", "lifecycle": LIFECYCLE_INTACT},
        ],
        "one_time_events": [
            {"scope_key": "test_key", "event_type": "test"},
        ],
    }
    new_ledger, violations = apply_delta(ledger, delta)
    assert violations
    assert new_ledger is ledger
    assert ledger["version"] == original_version


def test_apply_delta_success():
    ledger = empty_ledger()
    delta = {
        "prop_registrations": [
            {"prop_id": "watch", "name": "怀表", "position": "钟表行"},
        ],
        "resource_registrations": [
            {"resource_id": "memory", "name": "亲忆"},
        ],
        "actor_position_updates": [
            {"actor_id": "沈寻", "location": "钟表行"},
        ],
        "time_marker": "夜",
    }
    new_ledger, violations = apply_delta(ledger, delta)
    assert not violations
    assert "watch" in new_ledger["props"]
    assert "memory" in new_ledger["resources"]
    assert new_ledger["actor_positions"]["沈寻"]["location"] == "钟表行"
    assert new_ledger["time_marker"] == "夜"
    assert new_ledger["version"] == 1


def test_apply_delta_resource_consume():
    ledger = empty_ledger()
    delta = {
        "resource_registrations": [{"resource_id": "memory", "name": "亲忆"}],
    }
    ledger, _ = apply_delta(ledger, delta)
    delta2 = {
        "resource_consumptions": [
            {"resource_id": "memory", "entry": "祖母的摇篮曲", "cause_event_id": "evt1"},
        ],
    }
    new_ledger, violations = apply_delta(ledger, delta2)
    assert not violations
    assert len(new_ledger["resources"]["memory"]["consumed"]) == 1
    delta3 = {
        "resource_consumptions": [
            {"resource_id": "memory", "entry": "祖母的摇篮曲"},
        ],
    }
    _, violations2 = apply_delta(new_ledger, delta3)
    assert any("resource_double_consume" in v for v in violations2)


def test_apply_delta_destroyed_cannot_restore():
    ledger = empty_ledger()
    ledger["props"]["gear"] = {
        "prop_id": "gear",
        "name": "齿轮",
        "lifecycle": LIFECYCLE_DESTROYED,
        "partitions": {},
        "read_status": {},
    }
    delta = {"prop_updates": [{"prop_id": "gear", "lifecycle": LIFECYCLE_INTACT}]}
    _, violations = apply_delta(ledger, delta)
    assert any("destroyed_cannot_restore" in v for v in violations)
    delta2 = {"prop_updates": [{"prop_id": "gear", "lifecycle": LIFECYCLE_DAMAGED}]}
    new_ledger, violations2 = apply_delta(ledger, delta2)
    assert not violations2
    assert new_ledger["props"]["gear"]["lifecycle"] == LIFECYCLE_DAMAGED


def test_ledger_to_text_summary():
    ledger = empty_ledger()
    ledger["props"]["watch"] = {
        "prop_id": "watch",
        "name": "怀表",
        "position": "钟表行",
        "holder": "沈寻",
        "lifecycle": LIFECYCLE_INTACT,
        "partitions": {"铭文": "时间不回头"},
        "read_status": {"ability": {"count": 1}},
    }
    ledger["actor_positions"]["沈寻"] = {
        "location": "钟表行",
        "visit_round": 2,
    }
    summary = ledger_to_text_summary(ledger)
    assert "怀表" in summary
    assert "钟表行" in summary
    assert "沈寻" in summary
    assert "能力读取次数=1" in summary


def test_ledger_to_text_summary_empty():
    assert ledger_to_text_summary(empty_ledger()) == ""
    assert ledger_to_text_summary({}) == ""


def test_ledger_to_working_state_projects_to_dict():
    """工作包2.2：台账投影为 working_state 字典，替代自由文本 state_changes。"""
    from regent.novel.domain.continuity_ledger import ledger_to_working_state

    ledger = empty_ledger()
    delta = {
        "prop_registrations": [
            {"prop_id": "watch", "name": "怀表", "position": "钟表行", "holder": "沈寻"},
        ],
        "actor_position_updates": [
            {"actor_id": "沈寻", "location": "钟表行"},
        ],
        "time_marker": "第一天下午",
    }
    ledger, _ = apply_delta(ledger, delta)
    state = ledger_to_working_state(ledger)
    assert state["prop:watch.position"] == "钟表行"
    assert state["prop:watch.holder"] == "沈寻"
    assert state["actor:沈寻.location"] == "钟表行"
    assert state["time_marker"] == "第一天下午"


def test_ledger_to_working_state_empty():
    from regent.novel.domain.continuity_ledger import ledger_to_working_state

    assert ledger_to_working_state(empty_ledger()) == {}
    assert ledger_to_working_state({}) == {}
