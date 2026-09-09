"""R4 评估运行链的可运行入口：采样 → 评者视图 → 收分 → 报告 → 裁决。

`application/evaluation.py` 是落库服务，`domain/evaluation.py` 是纯判定——
但两者都没有「从真实生产数据走到结论」的入口：样本从哪来、成本/延迟/模型/
正文引用怎么接、评分怎么进出。本模块补上这一段（受控命令，不是 HTTP 端点；
评分与标准确认仍由人工完成，见 remaining-work v7.8）。

代理口径（工程确定量，写进报告 `proxy_definitions`；人工冻结评分标准时可整体替换）：

- **calm（舒缓）**：这一章一次通过——无 retake / rewrite / repair，复审未触发修订。
- **solo（单人）**：全章只有单一角色行动——director_v2 取 plan 内每场 actors
  均为 1 人；legacy 取 director_plan.character_actions 不超过 1 人。
- **配对**：同 ``chapter_no`` 下两臂各取一条最新 CANONIZED 运行（跨作品允许，
  按 ``created_at, id`` 确定性排序取最新），``content_refs`` 记录运行 id，
  事后可查证「评的到底是哪份正文」。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application import evaluation as eval_app
from regent.novel.application.principal import as_utc
from regent.novel.domain import evaluation as domain
from regent.novel.domain.states import ChapterRunState
from regent.novel.infrastructure.models import ChapterRunModel, ModelCallModel

PROXY_DEFINITIONS = {
    "calm": "一次通过：无 RETAKE/REWRITE/repair，复审未触发修订",
    "solo": "全章单一角色：director_v2 每场 actors 均为 1；legacy character_actions ≤ 1",
    "pairing": "同 chapter_no 两臂各取最新 CANONIZED 运行，按 created_at,id 确定性排序",
    "cost_minor_per_chapter": "该臂样本运行的实际结算金额均值",
    "latency_ms_p95": "该臂样本运行的模型调用耗时（updated_at−created_at）p95",
}


def _calm_of(run: ChapterRunModel) -> bool:
    ctx = dict(run.generation_context or {})
    review = dict(ctx.get("review") or {}) if isinstance(ctx.get("review"), dict) else {}
    if review.get("revised"):
        return False
    production = dict(ctx.get("production") or {})
    for decision in production.get("decisions") or []:
        if isinstance(decision, dict) and decision.get("action") in ("RETAKE", "REWRITE"):
            return False
    return True


def _solo_of(run: ChapterRunModel) -> bool:
    ctx = dict(run.generation_context or {})
    production = dict(ctx.get("production") or {})
    plan = dict(production.get("plan") or {})
    scenes = plan.get("scenes") or []
    if scenes:
        return all(len((s or {}).get("actors") or []) == 1 for s in scenes)
    legacy_plan = dict(ctx.get("director_plan") or {})
    actions = legacy_plan.get("character_actions") or []
    return 0 < len(actions) <= 1 if actions else False


async def collect_samples(
    session: AsyncSession, *, eval_id: str, arms: Sequence[str], limit: int
) -> list[domain.BlindSample]:
    """从真实定典运行里做确定性配对采样（口径见模块 docstring）。"""
    if len(arms) < 2:
        raise ValueError("至少需要两个臂才能配对")
    rows = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.state == ChapterRunState.CANONIZED.value,
                    ChapterRunModel.content != "",
                )
                .order_by(
                    ChapterRunModel.chapter_no,
                    ChapterRunModel.created_at,
                    ChapterRunModel.id,
                )
            )
        ).all()
    )
    # (chapter_no, arm) → 最新一条
    picked: dict[tuple[int, str], ChapterRunModel] = {}
    for row in rows:
        arm = str((row.generation_context or {}).get("executor") or "").strip()
        key = (int(row.chapter_no), arm)
        picked[key] = row  # 排序保证后写即最新
    samples: list[domain.BlindSample] = []
    chapter_nos = sorted({no for no, _ in picked})
    for no in chapter_nos:
        arm_runs = [(arm, picked[(no, arm)]) for arm in arms if (no, arm) in picked]
        if len(arm_runs) < len(arms):
            continue  # 缺任一臂的章节不成对：宁缺毋滥，不用单臂样本凑数
        sample_id = f"chapter:{no}:{arm_runs[0][1].id}"
        order = domain.shuffle_arms(sample_id, arms)
        run_by_arm = dict(arm_runs)
        samples.append(
            domain.make_sample(
                sample_id,
                arms,
                level="chapter",
                calm=all(_calm_of(run_by_arm[a]) for a in arms),
                solo=all(_solo_of(run_by_arm[a]) for a in arms),
                content_refs=[str(run_by_arm[a].id) for a in order],
            )
        )
        if len(samples) >= limit:
            break
    return samples


def _scene_count(run: ChapterRunModel) -> int:
    ctx = dict(run.generation_context or {})
    production = dict(ctx.get("production") or {})
    plan = dict(production.get("plan") or {})
    scenes = plan.get("scenes") or []
    if scenes:
        return len(scenes)
    legacy_plan = dict(ctx.get("director_plan") or {})
    beats = legacy_plan.get("beats") or []
    return max(1, len(beats))


async def _arm_metrics(
    session: AsyncSession, *, samples: Sequence[domain.BlindSample], arms: Sequence[str]
) -> dict[str, Any]:
    """从真实账本与运行记录计算每臂成本/延迟/模型/正文来源（B-03：不回填）。"""
    run_ids_by_arm: dict[str, list[str]] = {arm: [] for arm in arms}
    for sample in samples:
        for arm, ref in zip(sample.arms_in_order, sample.content_refs):
            if arm in run_ids_by_arm:
                run_ids_by_arm[arm].append(ref)
    metrics: dict[str, Any] = {"runs": run_ids_by_arm, "cost": {}, "cost_scene": {},
                               "latency": {}, "models": {}}
    for arm in arms:
        refs = [uuid.UUID(r) for r in run_ids_by_arm[arm]]
        if not refs:
            metrics["cost"][arm] = 0
            metrics["cost_scene"][arm] = 0
            metrics["latency"][arm] = 0
            metrics["models"][arm] = ""
            continue
        calls = list(
            (
                await session.scalars(
                    select(ModelCallModel).where(
                        ModelCallModel.run_id.in_(refs),
                        ModelCallModel.status == "SUCCEEDED",
                    )
                )
            ).all()
        )
        total_cost = sum(int(c.actual_amount_minor or 0) for c in calls)
        metrics["cost"][arm] = int(round(total_cost / len(refs)))
        scene_total = 0
        for ref in refs:
            run = await session.get(ChapterRunModel, ref)
            scene_total += _scene_count(run) if run is not None else 1
        metrics["cost_scene"][arm] = int(round(total_cost / max(1, scene_total)))
        durations = sorted(
            (as_utc(c.updated_at) - as_utc(c.created_at)).total_seconds() * 1000.0
            for c in calls
            if c.created_at is not None and c.updated_at is not None
        )
        if durations:
            index = max(0, min(len(durations) - 1, -(-95 * len(durations) // 100) - 1))
            metrics["latency"][arm] = int(round(durations[index]))
        else:
            metrics["latency"][arm] = 0
        models = sorted({str(c.model or "") for c in calls if str(c.model or "").strip()})
        metrics["models"][arm] = models[0] if len(models) == 1 else "mixed:" + "|".join(models)
    return metrics


async def prepare(
    session: AsyncSession, *, config: domain.EvalConfig, limit: int = 10
) -> dict[str, Any]:
    """冻结配置 + 从真实运行采样注册。幂等：同 eval_id 重复执行不重复注册。"""
    await eval_app.freeze_eval(session, config=config)
    samples = await collect_samples(
        session, eval_id=config.eval_id, arms=config.arms, limit=limit
    )
    added = await eval_app.add_samples(session, eval_id=config.eval_id, samples=samples)
    row = await eval_app._must_get(session, config.eval_id)
    return {
        "eval_id": config.eval_id,
        "registered": len(added),
        "total_samples": len(row.samples or []),
        "proxy_definitions": PROXY_DEFINITIONS,
    }


async def ingest_ratings(
    session: AsyncSession, *, eval_id: str, rater: str, payload: Sequence[dict[str, Any]]
) -> int:
    """评者评分文件入口：只收 A/B 位置，arm 还原留在服务端。"""
    ratings = [
        domain.PositionalScore(
            sample_id=str(item.get("sample_id") or ""),
            position=str(item.get("position") or ""),
            scores={str(k): float(v) for k, v in (item.get("scores") or {}).items()},
            preferred=bool(item.get("preferred")),
        )
        for item in payload
    ]
    return await eval_app.submit_ratings(
        session, eval_id=eval_id, rater=rater, ratings=ratings
    )


async def build_report_from_runs(session: AsyncSession, *, eval_id: str) -> dict[str, Any]:
    """出报告：成本/延迟/模型/正文来源全部取自真实账本与运行记录。"""
    row = await eval_app._must_get(session, eval_id)
    config = row.config or {}
    arms = tuple(str(a) for a in (config.get("arms") or ()))
    samples = await eval_app._samples_of(row)
    metrics = await _arm_metrics(session, samples=list(samples.values()), arms=arms)
    # 证据里的模型名必须如实：两臂同一模型记该模型，混用即记 mixed（B-03 不回填）
    model_values = {metrics["models"].get(a, "") for a in arms}
    overall_model = next(iter(model_values)) if len(model_values) == 1 else "mixed"
    report = await eval_app.build_report(
        session,
        eval_id=eval_id,
        cost_minor_per_chapter=metrics["cost"],
        cost_minor_per_scene=metrics["cost_scene"],
        latency_ms_p95=metrics["latency"],
        model=overall_model,
        run_refs=metrics["runs"],
    )
    report["proxy_definitions"] = PROXY_DEFINITIONS
    row.report = report
    row.report_fingerprint = domain.report_fingerprint_of(
        report, config_fingerprint=domain.fingerprint_of(config)
    )
    await session.flush()
    return report


async def finish(session: AsyncSession, *, eval_id: str) -> tuple[str, tuple[str, ...]]:
    """按冻结阈值裁决。人工只负责在此之前完成真实评分与标准确认。"""
    return await eval_app.decide(session, eval_id=eval_id)
