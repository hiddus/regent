"""质量对照实验：矩阵、预算帽、协议 S、网文金手指题材。"""

from __future__ import annotations

import json

import pytest

from regent.novel.experiments import quality_ab as qa


def test_matrix_default_includes_script_select():
    rows = qa.build_matrix()
    assert len(rows) == 20  # 4 fragments × A,B,C,S,X
    assert {r.protocol for r in rows} == {"A", "B", "C", "S", "X"}
    assert {r.fragment_id for r in rows} == {f["id"] for f in qa.FRAGMENTS}


def test_budget_meter_stops_at_cap():
    meter = qa.BudgetMeter(call_cap=2)
    meter.charge()
    meter.charge()
    with pytest.raises(qa.BudgetExhausted):
        meter.charge()


@pytest.mark.asyncio
async def test_sim_protocol_a_completes_under_cap():
    result = await qa.run_sample(
        protocol="A",
        fragment_id="f1_appraisal",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
    )
    assert result.completed
    assert result.protocol == "A"
    assert result.calls_used <= qa.CALL_CAP_PER_SAMPLE
    assert result.prose
    assert "write_scene" in result.steps_log


@pytest.mark.asyncio
async def test_sim_protocol_s_isolates_rejected_script():
    result = await qa.run_sample(
        protocol="S",
        fragment_id="f1_appraisal",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
    )
    assert result.completed
    assert result.protocol == "S"
    assert result.steps_log[0] == "director_brief"
    assert "write_scripts_hive" in result.steps_log
    assert "write_script_alpha" in result.steps_log
    assert "director_select" in result.steps_log
    assert "assemble_packet" in result.steps_log
    assert result.artifacts.get("hive", {}).get("enabled") is True
    assert result.artifacts["selected_id"] == "alpha"
    assert "beta" in result.artifacts["rejected"]
    assert result.artifacts.get("assignment") or result.artifacts.get("director_choice")
    packet = result.artifacts["production_packet"]
    assert "beta" not in json.dumps(packet, ensure_ascii=False)
    assert result.facts_committed
    assert result.artifacts["facts_status"] == "committed_from_final_prose"
    assert "extract_facts_from_prose" in result.steps_log


def test_assemble_packet_excludes_other_candidate():
    selected = qa.ChapterScript(
        title_line="选中",
        end_change="变了",
        cost="代价",
        beats=["a"],
        cast_draft=[qa.CastDraftPersona(name="主角", desire="活")],
    )
    other = qa.ChapterScript(title_line="弃选秘密情节XYZ")
    choice = qa.ScriptChoice(
        selected_id="alpha",
        must_land_beats=["a"],
        direction_notes="贴主角",
    )
    packet = qa.assemble_production_packet(
        selected=selected,
        choice=choice,
        meta={"fragment_id": qa.fragment_by_id("f1_appraisal")["id"]},
    )
    blob = json.dumps(packet, ensure_ascii=False)
    assert "弃选秘密情节XYZ" not in blob
    assert other.title_line not in blob


@pytest.mark.asyncio
async def test_sim_protocol_b_mirrors_director_steps():
    result = await qa.run_sample(
        protocol="B",
        fragment_id="f2_reborn",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
    )
    assert result.completed
    assert result.steps_log == [
        "plan_scene",
        "scene_resolve",
        "render_prose",
        "watch_prose",
        "validate",
    ]
    assert "events" in result.artifacts


@pytest.mark.asyncio
async def test_sim_protocol_c_keeps_facts_draft_until_validate():
    result = await qa.run_sample(
        protocol="C",
        fragment_id="f3_awaken",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
    )
    assert result.completed
    assert result.facts_committed
    assert result.artifacts.get("facts_status") == "committed"
    assert "extract_draft_facts" in result.steps_log


@pytest.mark.asyncio
async def test_low_call_cap_records_budget_stop_without_inflating_success():
    result = await qa.run_sample(
        protocol="B",
        fragment_id="f1_appraisal",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
        call_cap=2,
    )
    assert not result.completed
    assert result.stop_reason.startswith("budget:")
    assert result.calls_used <= 2


def test_c_is_not_a_known_executor():
    from regent.novel.application import executor as executor_app

    assert "director_bounds_then_write" not in executor_app.KNOWN_EXECUTORS
    assert "C" not in executor_app.KNOWN_EXECUTORS


def test_s_not_a_known_executor():
    from regent.novel.application import executor as executor_app

    assert "script_select_then_write" not in executor_app.KNOWN_EXECUTORS
    assert "S" not in executor_app.KNOWN_EXECUTORS


def test_too_short_prose_is_not_completed():
    result = qa._finalize_result(
        fragment_id="f1_appraisal",
        protocol="A",
        provider=qa.MeteredProvider(qa.SimProvider(), qa.BudgetMeter()),  # type: ignore[arg-type]
        prose="短。" * 20,
        hard_fails=[],
        used_revision=False,
        stop_reason="",
        facts=[],
        facts_committed=False,
        steps=["write_scene"],
        artifacts={},
    )
    assert not result.completed
    assert result.stop_reason.startswith("too_short:")


def test_fragments_are_web_novel_with_power():
    for frag in qa.FRAGMENTS:
        assert frag.get("slot") == "chapter1_opening"
        assert frag.get("channel") == "web_novel"
        sample = (
            qa.fragment_for_run(frag["id"])
            if frag.get("keyword_rails") == "1"
            else frag
        )
        assert sample.get("power")
        assert sample.get("must_not")
        assert sample.get("hook")
        assert sample.get("immersion")
        assert "route_alpha" not in frag and "route_beta" not in frag
        assert "route_gamma" not in frag and "route_delta" not in frag
    assert {f["id"] for f in qa.FRAGMENTS} == {
        "f1_appraisal",
        "f2_reborn",
        "f3_awaken",
        "f4_ent_gender",
    }
    assert "金手指" in qa.WEB_NOVEL_POWER_CONTRACT
    assert "第一章" in qa.OPENING_CONTRACT
    assert "网文" in qa.WEB_HOOK_CONTRACT
    assert "代入" in qa.IMMERSION_CONTRACT
    assert "注水" in qa.DENSITY_CONTRACT
    assert qa.MIN_PROSE_CHARS < 2000


def test_live_round_lengths_would_fail_new_floor():
    """早期极短样本仍应低于当前下限。"""
    lengths = [469, 769, 579]
    assert all(n < qa.MIN_PROSE_CHARS for n in lengths)
