"""R4 评估运行链入口的验收（remaining-work v7.8）。

命题：从真实定典运行出发，「采样 → 评者视图 → 收分 → 报告 → 裁决」能一次
贯通且证据取自真实账本——而不是「库级函数存在但没人能把它们接起来」。

Chinese fixture prose deliberately uses full-width punctuation.
ruff: noqa: RUF001
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from regent.novel.infrastructure.models import NovelBase
from regent.novel.application import eval_runner
from regent.novel.application import production
from regent.novel.domain import evaluation as domain
from regent.novel.domain.states import ChapterRunState
from regent.novel.eval_cli import main as cli_main
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ModelCallModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

ARMS = ("director_v2", "legacy_v1")
TEXT = "他把钥匙放在桌上。" + "雨水沿窗棂流下，两个人仍旧没有开口。" * 65


def _config(eval_id: str, *, min_samples: int, total: int) -> domain.EvalConfig:
    return domain.EvalConfig(
        eval_id=eval_id,
        arms=ARMS,
        sample=domain.SampleSpec(total=total, calm_scene_ratio=0.0, solo_scene_ratio=0.0,
                                 levels=("chapter",)),
        budget=domain.BudgetBand(model="test-model", max_cost_minor_per_scene=10_000,
                                 max_cost_minor_per_chapter=100_000),
        thresholds=domain.PromotionThresholds(
            min_samples=min_samples, min_read_willingness=0.0,
            min_character_credibility=0.0, min_emotion_and_payoff=0.0,
            max_fact_error=1.0, max_cost_minor_per_chapter=100_000,
        ),
        raters=("r1",),
    )


def _run(work, *, chapter_no: int, executor: str, calm_plan: bool = True,
         solo: bool = True) -> ChapterRunModel:
    if executor == "director_v2":
        ctx = {
            "executor": executor,
            "architecture_version": executor,
            "production": {
                "plan": {"scenes": [
                    {"actors": ["主角"] if solo else ["主角", "同伴"], "conflict": "试探"},
                ]},
                "decisions": [] if calm_plan else [
                    {"action": "RETAKE", "phase": "WATCH_TAKE"},
                ],
            },
        }
    else:
        ctx = {
            "executor": executor,
            "architecture_version": executor,
            "director_plan": {
                "beats": ["进门", "对峙", "放手"],
                "character_actions": [{"persona": "主角"}] if solo else [
                    {"persona": "主角"}, {"persona": "同伴"},
                ],
            },
            "review": {} if calm_plan else {"revised": True},
        }
    return ChapterRunModel(
        id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
        chapter_no=chapter_no, attempt=1,
        state=ChapterRunState.CANONIZED.value, content=TEXT,
        generation_context=ctx,
    )


def _call(run: ChapterRunModel, *, model: str = "test-model", cost: int = 500,
          latency_ms: int = 900) -> ModelCallModel:
    now = datetime.now(UTC)
    return ModelCallModel(
        id=uuid.uuid4(), logical_call_id=f"call:{run.id}:{uuid.uuid4()}",
        work_id=run.work_id, run_id=run.id, chapter_no=run.chapter_no,
        step="PRODUCE", purpose="test", provider="stub", model=model,
        status="SUCCEEDED", input_tokens=10, output_tokens=20,
        actual_amount_minor=cost, reserved_amount_minor=cost,
        created_at=now, updated_at=now + timedelta(milliseconds=latency_ms),
    )


async def _seed(novel_db, *, chapters: int = 1):
    """两臂各一个作品、各章 CANONIZED + 真实账本调用行。"""
    async with novel_db() as s:
        for executor in ARMS:
            owner = uuid.uuid4()
            s.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
            work = StoryWorkModel(
                id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑",
                latest_chapter_no=chapters,
            )
            s.add(work)
            await s.flush()
            for chapter_no in range(1, chapters + 1):
                run = _run(work, chapter_no=chapter_no, executor=executor)
                s.add(run)
                s.add(_call(run))
        await s.commit()


@pytest.mark.asyncio
async def test_full_chain_from_real_runs_to_verdict(novel_db):
    """采样/视图/收分/报告/裁决一次贯通；证据取自真实账本（R4 关闭条件）。"""
    await _seed(novel_db, chapters=2)

    async with novel_db() as s:
        # ① 采样：两臂同章号配对，content_refs 指向真实运行
        prepared = await eval_runner.prepare(
            s, config=_config("pilot-1", min_samples=1, total=2), limit=10
        )
        await s.commit()
    assert prepared["registered"] == 2, prepared
    assert prepared["proxy_definitions"]["calm"]

    async with novel_db() as s:
        from regent.novel.application import evaluation as eval_app

        samples = await eval_app.rater_view(s, eval_id="pilot-1")
    assert len(samples) == 2
    for sample in samples:
        assert sample["options"] == ["A", "B"]
        assert "arm" not in json.dumps(sample), "评者视图泄露 arm 身份"

    # ② 收分：评者只按位置报分；服务端还原 arm
    async with novel_db() as s:
        from regent.novel.application import evaluation as eval_app

        row = await eval_app._must_get(s, "pilot-1")
        view = [domain.BlindSample.from_record(p) for p in (row.samples or [])]
        ratings = []
        for sample in view:
            for position in ("A", "B"):
                ratings.append({
                    "sample_id": sample.sample_id, "position": position,
                    "scores": {
                        "read_willingness": 4.5, "character_credibility": 4.0,
                        "emotion_and_payoff": 4.2, "fact_error": 0.0,
                    },
                    "preferred": position == "A",
                })
        accepted = await eval_runner.ingest_ratings(
            s, eval_id="pilot-1", rater="r1", payload=ratings
        )
        await s.commit()
    assert accepted == 4

    # ③ 报告：成本/延迟/模型/正文来源取自真实账本
    async with novel_db() as s:
        report = await eval_runner.build_report_from_runs(s, eval_id="pilot-1")
        await s.commit()
    evidence = report["evidence"]
    assert evidence["model"] == "test-model", evidence
    assert evidence["cost_provided"] == list(ARMS), "成本没有从账本接进来"
    assert evidence["content_refs_complete"] is True, "正文引用没有接到运行 id"
    assert evidence["runs_bound"] == list(ARMS)
    for arm in report["arms"]:
        assert arm["cost_minor_per_chapter"] == 500
        assert arm["latency_ms_p95"] == 900
        assert len(arm["run_refs"]) == 2, "run_refs 应指向两章的真实运行"

    # ④ 裁决：样本足够 + 证据齐 → 按阈值给结论（不是永远 HOLD 的死链路）
    async with novel_db() as s:
        verdict, reasons = await eval_runner.finish(s, eval_id="pilot-1")
        await s.commit()
    assert verdict in {"PROMOTED", "HOLD"}
    assert reasons


@pytest.mark.asyncio
async def test_insufficient_sampling_holds_even_with_perfect_scores(novel_db):
    """样本不足时结论只能是 HOLD——链路贯通不等于放宽门槛（R4 纪律）。"""
    await _seed(novel_db, chapters=1)

    async with novel_db() as s:
        await eval_runner.prepare(
            s, config=_config("pilot-2", min_samples=5, total=5), limit=10
        )
        await s.commit()
        from regent.novel.application import evaluation as eval_app

        row = await eval_app._must_get(s, "pilot-2")
        view = [domain.BlindSample.from_record(p) for p in (row.samples or [])]
        ratings = []
        for sample in view:
            for position in ("A", "B"):
                ratings.append({
                    "sample_id": sample.sample_id, "position": position,
                    "scores": {"read_willingness": 5.0}, "preferred": True,
                })
        await eval_runner.ingest_ratings(s, eval_id="pilot-2", rater="r1", payload=ratings)
        await eval_runner.build_report_from_runs(s, eval_id="pilot-2")
        await s.commit()
        verdict, reasons = await eval_runner.finish(s, eval_id="pilot-2")
        await s.commit()

    assert verdict == "HOLD"
    assert any("样本" in r or "sample" in r.lower() for r in reasons), reasons


@pytest.mark.asyncio
async def test_cli_end_to_end_on_file_database(tmp_path, monkeypatch):
    """CLI 受控命令在真实文件库上走完 prepare → ingest → report → decide。"""
    db_path = tmp_path / "eval.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(NovelBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    production.configure_session_factory(factory)
    try:
        async with factory() as s:
            await _seed_data(s)
            await s.commit()

        config = {
            "eval_id": "cli-1", "arms": list(ARMS),
            "sample": {"total": 1, "calm_scene_ratio": 0.0, "solo_scene_ratio": 0.0,
                       "levels": ["chapter"]},
            "budget": {"model": "test-model", "max_cost_minor_per_scene": 10_000,
                       "max_cost_minor_per_chapter": 100_000},
            "thresholds": {"min_samples": 1, "min_read_willingness": 0.0,
                           "min_character_credibility": 0.0,
                           "min_emotion_and_payoff": 0.0, "max_fact_error": 1.0,
                           "max_cost_minor_per_chapter": 100_000},
            "raters": ["r1"],
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        assert cli_main([
            "--database-url", url, "prepare", "--eval-id", "cli-1",
            "--config-file", str(config_file),
        ]) == 0

        # 评者视图与评分文件走 CLI
        async with factory() as s:
            from regent.novel.application import evaluation as eval_app

            row = await eval_app._must_get(s, "cli-1")
            view = [domain.BlindSample.from_record(p) for p in (row.samples or [])]
        ratings = [
            {"sample_id": sample.sample_id, "position": position,
             "scores": {"read_willingness": 4.0}, "preferred": False}
            for sample in view for position in ("A", "B")
        ]
        ratings_file = tmp_path / "ratings.json"
        ratings_file.write_text(json.dumps(ratings, ensure_ascii=False), encoding="utf-8")
        assert cli_main([
            "--database-url", url, "ingest", "--eval-id", "cli-1",
            "--rater", "r1", "--file", str(ratings_file),
        ]) == 0
        assert cli_main(["--database-url", url, "report", "--eval-id", "cli-1"]) == 0
        assert cli_main(["--database-url", url, "decide", "--eval-id", "cli-1"]) == 0

        async with factory() as s:
            from regent.novel.application import evaluation as eval_app

            row = await eval_app._must_get(s, "cli-1")
            assert row.report, "CLI 未产出报告"
            assert row.verdict in {"PROMOTED", "HOLD"}
            assert row.report.get("evidence", {}).get("cost_provided")
    finally:
        production.configure_session_factory(None)
        await engine.dispose()


async def _seed_data(session) -> None:
    for executor in ARMS:
        owner = uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑",
            latest_chapter_no=1,
        )
        session.add(work)
        await session.flush()
        run = _run(work, chapter_no=1, executor=executor)
        session.add(run)
        session.add(_call(run))


def test_cli_help_renders():
    """CLI 自描述可用：不是只有库函数没有入口。"""
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0
