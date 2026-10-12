"""工作包补充：结构化议题、事件模态、场景边界。"""

from __future__ import annotations

from regent.novel.application.directing_contracts import ActualEvent
from regent.novel.domain.review_issues import StructuredIssue, parse_issue_text
from regent.novel.domain.scene_boundary import check_scene_boundary


def test_structured_issue_has_required_fields():
    issue = StructuredIssue(
        issue_id="abc123",
        code="cont:prop",
        severity="hard",
        origin="reviewer",
        description="道具位置不一致",
        origin_scene_id="s1",
        affected_scene_ids=["s2"],
        evidence_spans=["怀表在桌上"],
        status="OPEN",
    )
    assert issue.issue_id == "abc123"
    assert issue.severity == "hard"
    assert issue.status == "OPEN"
    assert issue.to_text() == "[cont:prop] 道具位置不一致"


def test_parse_issue_text_extracts_code():
    issue = parse_issue_text("[cont:prop] 道具位置不一致")
    assert issue.code == "cont:prop"
    assert issue.description == "道具位置不一致"
    assert issue.severity == "hard"


def test_parse_issue_text_extracts_quotes():
    issue = parse_issue_text("[cont:prop] 「怀表在桌上」与入场状态不符")
    assert issue.evidence_spans == ["怀表在桌上"]


def test_parse_issue_text_soft_issue():
    issue = parse_issue_text("[soft-cont:style] 可以更简洁")
    assert issue.severity == "soft"


def test_actual_event_modality():
    evt = ActualEvent(
        event_id="evt1",
        statement="他读出了怀表的铭文",
        modality="occurrence",
        evidence="他凝视着表盘，默念那行字",
    )
    assert evt.modality == "occurrence"
    assert evt.statement == "他读出了怀表的铭文"


def test_actual_event_recall():
    evt = ActualEvent(
        event_id="evt2",
        statement="他想起昨天的对话",
        modality="recall",
    )
    assert evt.modality == "recall"


def test_check_scene_boundary_no_violation():
    scene_plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "发现怀表"},
            {"scene_id": "s2", "purpose": "读取怀表铭文"},
        ]
    }
    events = [
        {"statement": "他发现了怀表", "modality": "occurrence", "responsibility_scene_id": "s1"},
    ]
    violations = check_scene_boundary("s1", events, scene_plan, {})
    assert violations == []


def test_check_scene_boundary_premature_occurrence():
    scene_plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "发现怀表"},
            {"scene_id": "s2", "purpose": "读取怀表铭文"},
        ]
    }
    events = [
        {"statement": "读取怀表铭文", "modality": "occurrence", "responsibility_scene_id": "s1"},
    ]
    violations = check_scene_boundary("s1", events, scene_plan, {})
    assert any("premature_occurrence" in v for v in violations)


def test_check_scene_boundary_wrong_responsibility():
    scene_plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "发现怀表"},
            {"scene_id": "s2", "purpose": "读取怀表铭文"},
        ]
    }
    events = [
        {"statement": "他发现了怀表", "modality": "occurrence", "responsibility_scene_id": "s2"},
    ]
    violations = check_scene_boundary("s1", events, scene_plan, {})
    assert any("boundary_violation" in v for v in violations)


def test_chapter_canon_blockers_checks_ledger_consistency():
    """工作包1.2：chapter_canon_blockers 须校验台账一致性（仅当台账存在时）。"""
    from types import SimpleNamespace
    from regent.novel.application.chapter_accept import chapter_canon_blockers

    # 无台账时不阻断（兼容旧作品）
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "missing_continuity_ledger" not in blockers

    # 有台账违规时阻断
    production["script_protocol"] = {
        "continuity_ledger": {"time_marker": "day1"},
        "ledger_violations": [{"scene_id": "s1", "v": "prop_mismatch"}],
    }
    blockers = chapter_canon_blockers(run, production)
    assert "ledger_violations:1" in blockers

    # 有边界违规时阻断
    production["script_protocol"] = {
        "continuity_ledger": {"time_marker": "day1"},
        "boundary_violations": [{"scene_id": "s1", "v": "premature"}],
    }
    blockers = chapter_canon_blockers(run, production)
    assert "boundary_violations:1" in blockers

    # 台账正常时不阻断
    production["script_protocol"] = {
        "continuity_ledger": {"time_marker": "day1"},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "ledger_violations" not in str(blockers)
    assert "boundary_violations" not in str(blockers)


def test_check_scene_boundary_recall_not_violation():
    scene_plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "发现怀表"},
            {"scene_id": "s2", "purpose": "读取怀表铭文"},
        ]
    }
    events = [
        {"statement": "读取怀表铭文", "modality": "recall", "responsibility_scene_id": "s1"},
    ]
    violations = check_scene_boundary("s1", events, scene_plan, {})
    assert violations == []
