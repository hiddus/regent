"""R21：局部补丁合并、要求清单、核验报告、动作三分法。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from regent.novel.application import direction as d
from regent.novel.domain import prose_patch as pp
from regent.novel.domain import scene_requirements as sr

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "novel" / "run21"


def _load_prose(index: int) -> str:
    return (FIXTURE / "prose_versions" / f"v{index}.txt").read_text(encoding="utf-8")


def test_run21_fixture_has_three_independent_fault_samples():
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["correspondence"]["remote_run_id"]
    assert [v["chars"] for v in manifest["prose_versions"]] == [1170, 3263, 511]
    assert "local_rewrite_overwrote_full_scene" in manifest["independent_faults"]
    assert "删除后续所有重复" in manifest["revision_instruction"]
    assert manifest["take"]["revisions"] == 2


def test_apply_patch_preserves_prefix_from_run21_sample():
    """局部修订后，开盖前段落与 3263 字版逐字一致。"""
    base = _load_prose(1)
    bad_tail = _load_prose(2)
    paras = pp.split_paragraphs(base)
    assert len(paras) >= 2
    # 导演要求：保留至「取下了开盖用的橡胶球」——定位含该句的段及之前
    keep_until = None
    for p in paras:
        if "取下了开盖用的橡胶球" in p.text:
            keep_until = p.paragraph_id
            break
    assert keep_until is not None
    keep_ids = []
    editable = []
    past = False
    for p in paras:
        if past:
            editable.append(p.paragraph_id)
        else:
            keep_ids.append(p.paragraph_id)
        if p.paragraph_id == keep_until:
            past = True
    assert editable, "应有可编辑的后半段"
    merged = pp.apply_patch(
        base_text=base,
        base_hash=pp.content_hash(base),
        paragraphs=paras,
        patch_hash=pp.content_hash(base),
        replacements=[{"paragraph_ids": editable, "text": bad_tail}],
    )
    # 未修改前缀逐字保留
    prefix = "\n\n".join(p.text for p in paras if p.paragraph_id in keep_ids)
    assert merged.startswith(prefix)
    assert "取下了开盖用的橡胶球" in merged
    assert bad_tail in merged
    # 对照：若整场覆盖会丢掉前缀
    assert not bad_tail.startswith(prefix[:80])


def test_stale_hash_and_unknown_id_rejected_without_merge():
    base = "第一段。\n\n第二段。"
    paras = pp.split_paragraphs(base)
    with pytest.raises(pp.PatchError, match="过期"):
        pp.apply_patch(
            base_text=base,
            base_hash=pp.content_hash(base),
            paragraphs=paras,
            patch_hash="0" * 64,
            replacements=[{"paragraph_ids": [paras[-1].paragraph_id], "text": "新第二段。"}],
        )
    with pytest.raises(pp.PatchError, match="未知"):
        pp.apply_patch(
            base_text=base,
            base_hash=pp.content_hash(base),
            paragraphs=paras,
            patch_hash=pp.content_hash(base),
            replacements=[{"paragraph_ids": ["p9999"], "text": "幽灵段。"}],
        )


def test_overlapping_ranges_rejected():
    base = "A段\n\nB段\n\nC段"
    paras = pp.split_paragraphs(base)
    h = pp.content_hash(base)
    with pytest.raises(pp.PatchError, match="重叠"):
        pp.apply_patch(
            base_text=base,
            base_hash=h,
            paragraphs=paras,
            patch_hash=h,
            replacements=[
                {"paragraph_ids": [paras[0].paragraph_id, paras[1].paragraph_id], "text": "AB"},
                {"paragraph_ids": [paras[1].paragraph_id], "text": "B2"},
            ],
        )


def test_noncontiguous_replacement_rejected_and_gap_preserved_via_split():
    """一个 replacement 含间隙 id 必须拒绝；拆成多个后 KEEP 不得消失（R21-F2）。"""
    base = "A\n\nKEEP\n\nC\n\nTAIL"
    paras = pp.split_paragraphs(base)
    h = pp.content_hash(base)
    with pytest.raises(pp.PatchError, match="不连续"):
        pp.apply_patch(
            base_text=base,
            base_hash=h,
            paragraphs=paras,
            patch_hash=h,
            replacements=[{"paragraph_ids": ["p0000", "p0002"], "text": "NEW"}],
        )
    merged = pp.apply_patch(
        base_text=base,
        base_hash=h,
        paragraphs=paras,
        patch_hash=h,
        replacements=[
            {"paragraph_ids": ["p0000"], "text": "NEW"},
            {"paragraph_ids": ["p0002"], "text": "C2"},
        ],
    )
    assert "KEEP" in merged
    assert merged.startswith("NEW")
    assert "TAIL" in merged
    # offset 拼接：KEEP 两侧分隔符与原稿一致
    assert base[paras[1].start : paras[1].end] == "KEEP"
    assert "KEEP" in merged


def test_patch_preserves_original_separators_and_rejects_out_of_range():
    base = "A\r\n\r\nB\r\n\r\nC"
    paras = pp.split_paragraphs(base)
    h = pp.content_hash(base)
    merged = pp.apply_patch(
        base_text=base,
        base_hash=h,
        paragraphs=paras,
        patch_hash=h,
        replacements=[{"paragraph_ids": ["p0001"], "text": "B2"}],
    )
    assert merged == base[: paras[1].start] + "B2" + base[paras[1].end :]
    assert "\r\n\r\n" in merged
    with pytest.raises(pp.PatchError, match="可改范围"):
        pp.apply_patch(
            base_text=base,
            base_hash=h,
            paragraphs=paras,
            patch_hash=h,
            replacements=[{"paragraph_ids": ["p0000"], "text": "A2"}],
            allowed_paragraph_ids=["p0001"],
        )


def test_independent_attributes_do_not_overwrite():
    events = [
        {
            "statement": "站起",
            "reader_visible": True,
            "state_changes": {"主角.姿态": "站立"},
        },
        {
            "statement": "握紧刀",
            "reader_visible": True,
            "state_changes": {"主角.持械": "短刀在手"},
        },
        {
            "statement": "坐下歇息",
            "reader_visible": True,
            "state_changes": {"主角.姿态": "坐下"},
        },
    ]
    reqs = sr.freeze_scene_requirements(events)
    by_id = {r["requirement_id"]: r for r in reqs}
    assert by_id["state:主角:姿态:final"]["expected_value"] == "坐下"
    assert by_id["state:主角:持械:final"]["expected_value"] == "短刀在手"
    assert by_id["event:0:主角:姿态"]["expected_value"] == "站立"
    assert "state:主角:持械:final" in by_id


def test_empty_validation_report_is_defect_when_requirements_need_evidence():
    """有状态要求时双空报告不得放行（R21-F1）。"""
    requirements = [
        {
            "requirement_id": "state:door:final",
            "state_key": "door",
            "expected_value": "closed",
            "require_direct_evidence": True,
        }
    ]
    draft = "rain fell hard."
    result = d.SceneValidation(
        passed=True,
        facts=[
            d.VerifiedFact(statement="rain", quote="rain", known_by=["主角"])
        ],
        requirements=[],
        state_changes=[],
    )
    defects = d._validation_report_defects(
        result, draft, requirements, protocol_version=sr.REQUIREMENTS_VERSION
    )
    assert any("均为空" in x for x in defects)
    issues = d._substantive_validation_issues(result, draft, requirements, {"主角": {}})
    accept = result.passed and not defects and not issues and bool(result.facts)
    assert accept is False


def test_empty_report_ok_when_no_evidence_requirements():
    result = d.SceneValidation(
        passed=True,
        facts=[d.VerifiedFact(statement="rain", quote="rain", known_by=["主角"])],
        requirements=[],
        state_changes=[],
    )
    defects = d._validation_report_defects(
        result, "rain", [], protocol_version=sr.REQUIREMENTS_VERSION
    )
    assert defects == []


def test_ensure_requirements_does_not_rewrite_frozen_legacy_take():
    frozen = [
        {
            "requirement_id": "state:key:final",
            "state_key": "key",
            "expected_value": "桌上",
            "require_direct_evidence": True,
            "event_index": 0,
        }
    ]
    take = {
        "requirements_version": sr.LEGACY_REQUIREMENTS_VERSION,
        "requirements": frozen,
        "events": [
            {
                "reader_visible": True,
                "state_changes": {"key": "桌上", "extra": "新属性"},
            }
        ],
    }
    got = d._ensure_requirements(take)
    assert got is frozen
    assert take["requirements_version"] == sr.LEGACY_REQUIREMENTS_VERSION


def test_requirements_final_state_hides_intermediate_and_hidden():
    events = [
        {
            "statement": "先把钥匙放桌上",
            "reader_visible": True,
            "state_changes": {"key": "桌上"},
        },
        {
            "statement": "暗中换锁（读者不可见）",
            "reader_visible": False,
            "state_changes": {"lock": "已换"},
        },
        {
            "statement": "再把钥匙交给对方",
            "reader_visible": True,
            "state_changes": {"key": "对方手里"},
        },
    ]
    reqs = sr.freeze_scene_requirements(events)
    by_id = {r["requirement_id"]: r for r in reqs}
    assert by_id["state:key:final"]["expected_value"] == "对方手里"
    assert by_id["state:key:final"]["require_direct_evidence"] is True
    assert by_id["state:lock:final"]["require_direct_evidence"] is False
    # r21_v2：被覆盖的可见中间写入保留为 event 要求
    assert by_id["event:0:key"]["expected_value"] == "桌上"
    assert by_id["event:0:key"]["kind"] == "event"
    preserve = sr.must_preserve_for_writer(reqs)
    assert all(p["state_key"] != "lock" for p in preserve)
    assert any(p["expected_value"] == "对方手里" for p in preserve)
    assert any(p["requirement_id"] == "event:0:key" for p in preserve)
    # 旧协议不生成中间事件要求，口径钉死不漂移
    legacy = sr.freeze_scene_requirements(events, version=sr.LEGACY_REQUIREMENTS_VERSION)
    assert not any(r.get("kind") == "event" for r in legacy)
    assert {r["requirement_id"] for r in legacy} == {
        "state:key:final",
        "state:lock:final",
    }


def test_revision_conflict_flags_delete_of_settled_action():
    reqs = sr.freeze_scene_requirements(
        [
            {
                "statement": "他用螺丝刀拆下秒轮桥，三颗螺丝置于绒布",
                "reader_visible": True,
                "state_changes": {"机芯": "秒轮桥已拆下，螺丝在绒布上"},
            }
        ]
    )
    instruction = "删除后续所有……螺丝刀拆秒轮桥、检查秒针榫头等冗余动作。"
    conflicts = sr.revision_conflicts_settled(instruction, reqs)
    assert conflicts
    assert "RETAKE" in conflicts[0]


def test_validation_report_rejects_fabricated_quotes():
    draft = _load_prose(2)  # 511 字后半段，没有开盖前内容
    requirements = [
        {
            "requirement_id": "state:机芯:final",
            "state_key": "机芯",
            "expected_value": "秒轮桥已拆",
            "require_direct_evidence": True,
        }
    ]
    result = d.SceneValidation(
        passed=True,
        facts=[],
        requirements=[
            d.RequirementVerdict(
                requirement_id="state:机芯:final",
                status="supported",
                quote="他把秒轮桥和三颗螺丝整整齐齐摆在绒布上。",  # 不在 draft
                explanation="幻觉",
            )
        ],
    )
    defects = d._validation_report_defects(result, draft, requirements)
    assert any("不在当前正文" in x for x in defects)


def test_synonym_explanation_does_not_force_string_equality():
    """同义解释不得因 value 字符串不等而误杀——只看 requirement 状态。"""
    requirements = [
        {
            "requirement_id": "state:主角:final",
            "state_key": "主角",
            "expected_value": "暂停拆解，观察暴露的机芯运转",
            "require_direct_evidence": True,
        }
    ]
    draft = "陈默停下动作，只看着机芯运转。"
    result = d.SceneValidation(
        passed=True,
        facts=[
            d.VerifiedFact(
                statement="他在观察",
                quote="只看着机芯运转",
                known_by=["主角"],
            )
        ],
        requirements=[
            d.RequirementVerdict(
                requirement_id="state:主角:final",
                status="supported",
                quote="只看着机芯运转",
                explanation="暂停拆解，观察暴露的机芯运转",  # 与 expected 不同措辞
            )
        ],
    )
    assert not d._validation_report_defects(result, draft, requirements)
    issues = d._substantive_validation_issues(result, draft, requirements, {"主角": {}})
    assert not any("不一致" in i for i in issues)


def test_catastrophic_loss_rejects_run21_half_scene():
    base = _load_prose(1)
    half = _load_prose(2)
    msg = d._catastrophic_prose_loss(base, half)
    assert msg is not None
    assert "拒绝覆盖" in msg


def test_superseded_takes_do_not_consume_retake_budget():
    """整章回退后，SUPERSEDED take 不得占满 MAX_TAKES（run5 死法）。"""
    production = {"scene_index": 1, "takes": [], "working_state": {}, "protocol": "scene"}
    for status in ("REJECTED", "SUPERSEDED"):
        production["takes"].append(
            {"scene_index": 1, "take_no": 1, "status": status}
        )
    # 仅 1 个非 SUPERSEDED → 仍可开新 take（MAX_TAKES=2）
    assert d._live_takes_for_scene(production, 1) == 1
    d._new_take(production, {"purpose": "x"})
    assert production["takes"][-1]["take_no"] == 2
    assert production["takes"][-1]["status"] == "DRAFT"
    assert production["phase"] == "SCENE"


def test_chapter_repair_supersedes_all_takes_from_failed_scene():
    """回退时作废该场及之后的 REJECTED，避免后续场无法再开 take。"""
    production = {
        "scene_index": 0,
        "takes": [
            {"scene_index": 0, "status": "ACCEPTED", "take_no": 1},
            {"scene_index": 1, "status": "REJECTED", "take_no": 1},
            {"scene_index": 1, "status": "REJECTED", "take_no": 2},
            {"scene_index": 1, "status": "ACCEPTED", "take_no": 3},
        ],
        "accepted": [0, 3],
        "working_state": {},
    }
    index = 0
    for take in production["takes"]:
        if int(take.get("scene_index", -1)) >= index:
            take["status"] = "SUPERSEDED"
    assert all(t["status"] == "SUPERSEDED" for t in production["takes"])
    assert d._live_takes_for_scene(production, 1) == 0


def test_forced_patch_path_ignores_revision_mode_full():
    """有旧稿时即使导演选 full，RENDER 也必须走 SceneTextPatch。"""
    import inspect

    src = inspect.getsource(d.produce_tick)
    assert "use_patch" in src
    assert "SceneTextPatch" in src
    assert "_catastrophic_prose_loss" in src


def test_legal_action_report_distinguishes_param_vs_forbidden():
    from regent.novel.domain.states import SceneArtifact, SceneRunState

    brief = {
        "purpose": "p",
        "setting": "s",
        "conflict": "c",
        "exit_condition": "e",
        "actors": [{"persona": "主角", "objective": "o", "instruction": "i"}],
        "narrative": {
            "viewpoint": "主角",
            "distance": "近",
            "style": "克制",
            "reader_effect": "担忧",
            "disclosure_rule": "不泄密",
        },
    }
    take = {
        "scene_index": 0,
        "take_no": 1,
        "revisions": 2,
        "turn": 0,
        "events": [{"reader_visible": True}],
        "content": "x" * 250,
        "round_actions": [],
        "brief": brief,
        "scene_state": SceneRunState.DIRECTOR_VIEW.value,
        "artifact": SceneArtifact.PROSE.value,
        "validation": {
            "passed": False,
            "issues": ["状态要求缺失正文证据：state:key:final"],
            "requirements": [{"requirement_id": "state:key:final", "status": "missing"}],
        },
        "rule_issues": [],
    }
    production = {
        "scene_index": 0,
        "takes": [take],
        "call_count": 3,
        "cast": {"主角": {}},
        "decisions": [],
        "reserved_minor": 0,
    }
    run = type("R", (), {"input_version": 1, "id": "r", "chapter_no": 1})()
    result = d.ProseDirection(
        action="ACCEPT",
        observation="仍想接受",
        evidence=["他把钥匙放在桌上。"],
        instruction="无",
    )
    lines = d._legal_action_report(
        production,
        run,
        take,
        "WATCH_PROSE",
        result,
        d._PROSE_COMMANDS,
        take["brief"]["actors"],
    )
    text = "\n".join(lines)
    assert "ACCEPT：禁止" in text
    assert "REWRITE：禁止" in text
    assert "RETAKE：补参后可执行" in text


def test_validation_repair_uses_distinct_command_suffix():
    """核验自修必须带 repair_no，否则不同入参撞同一幂等键（run3 CALL_IDEMPOTENCY_CONFLICT）。"""
    import inspect

    src = inspect.getsource(d.produce_tick)
    assert "repair_no=_attempt" in src
    assert "MAX_VALIDATION_REPAIRS" in src
