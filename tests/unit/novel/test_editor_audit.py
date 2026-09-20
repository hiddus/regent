"""责编审校：硬问题格式与过滤。"""

from __future__ import annotations

from regent.novel.application import editor_audit as ea


def test_editor_hard_issues_format_and_filter():
    result = ea.EditorAuditResult(
        passed=False,
        issues=[
            ea.EditorIssue(
                rule_id="jargon_first_gloss",
                hard=True,
                quote="维持鱼塘浓度",
                reason="『鱼塘』首次出现无读者向释义",
            ),
            ea.EditorIssue(
                rule_id="metaphor_overuse",
                hard=True,
                quote="这单超时了",
                reason="外卖隐喻刷屏，挤掉必要信息",
            ),
            ea.EditorIssue(
                rule_id="made_up",
                hard=True,
                quote="x",
                reason="未知规则应过滤掉",
            ),
        ],
    )
    hard = ea.issues_from_editor_result(result, hard_only=True)
    soft = ea.issues_from_editor_result(result, soft_only=True)
    # 责编全 soft：明显硬伤改由 front_gate / VALIDATE 前置
    assert hard == []
    assert any("jargon_first_gloss" in x for x in soft)
    assert any("metaphor_overuse" in x for x in soft)
    assert all("[editor-soft:" in x for x in soft)


def test_editor_gloss_nearby_demotes_jargon_hard():
    chapter = (
        "【浮名系统·首次抽取】面板弹出。"
        "【鱼塘羁绊值：与鱼塘成员的互动质量决定。羁绊值越高，浮名池浓度越高。】"
    )
    result = ea.EditorAuditResult(
        passed=False,
        issues=[
            ea.EditorIssue(
                rule_id="jargon_first_gloss",
                hard=True,
                quote="【鱼塘羁绊值：与鱼塘成员的互动质量决定。羁绊值越高，浮名池浓度越高。】",
                reason="未解释鱼塘指什么",
            ),
        ],
    )
    hard = ea.issues_from_editor_result(result, hard_only=True, chapter=chapter)
    soft = ea.issues_from_editor_result(result, soft_only=True, chapter=chapter)
    assert hard == []
    assert soft and "jargon_first_gloss" in soft[0]



def test_editor_payload_carries_bible_contrasts():
    payload = ea.editor_payload(
        chapter="正文",
        chapter_no=1,
        title="沈栀开播前夜",
        cast={"沈栀（穿越者意识）": {}, "周予安": {}},
        power_system="浮名系统靠鱼塘抽技能",
        reader_contract={"must_deliver": ["鱼塘拉扯"]},
    )
    assert payload["cast_names"] == ["沈栀（穿越者意识）", "周予安"]
    assert "浮名系统" in payload["power_system"]
    assert "jargon_first_gloss" in payload["allowed_rule_ids"]


def test_editor_audit_sampling_policy():
    assert ea.should_run_editor_audit(chapter_no=1, mode="sample")
    assert not ea.should_run_editor_audit(chapter_no=2, mode="sample")
    assert ea.should_run_editor_audit(chapter_no=5, mode="sample")
    assert not ea.should_run_editor_audit(chapter_no=1, mode="off")
    assert ea.should_run_editor_audit(chapter_no=2, mode="always")
    assert not ea.should_run_editor_audit(
        chapter_no=1, mode="sample", front_fails=["[front:x]"]
    )
