"""盲评评估与灰度裁决的落库服务（Plan §4 R4）。

分寸与 R3 一致：判定全部由 ``domain.evaluation`` 的纯函数完成，这里只负责
「冻结 → 采样 → 收分 → 出报告 → 裁决 → 落库」。

- 冻结后配置指纹写进库，采样后改配置会被指纹比对挡下；
- 评者看不到 arm 标签，导演自评不进人评；
- 样本不足时结论只能是 HOLD，不允许用趋势代替结论。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain import evaluation as domain
from regent.novel.domain.errors import GuardViolation
from regent.novel.infrastructure.models import EvalRunModel


async def freeze_eval(session: AsyncSession, *, config: domain.EvalConfig) -> EvalRunModel:
    """冻结一次评估的配置。同 eval_id 幂等。"""
    existing = await session.scalar(
        select(EvalRunModel).where(EvalRunModel.eval_id == config.eval_id)
    )
    if existing is not None:
        return existing
    row = EvalRunModel(
        id=uuid.uuid4(),
        eval_id=config.eval_id,
        config=config.as_payload(),
        config_fingerprint=config.fingerprint,
        samples=[],
        scores=[],
        report={},
        report_fingerprint=config.fingerprint,  # 未出报告时与配置同源
        verdict="HOLD",
        verdict_reasons=["尚未采样"],
    )
    session.add(row)
    await session.flush()
    return row


async def add_samples(
    session: AsyncSession, *, eval_id: str, samples: Sequence[domain.BlindSample]
) -> list[domain.BlindSample]:
    """注册样本。落库的是**含映射与正文引用的存档**，不是评者看到的载荷。"""
    row = await _must_get(session, eval_id)
    known = {str(s.get("sample_id")) for s in (row.samples or [])}
    appended = [s for s in samples if str(s.sample_id) not in known]
    if appended:
        row.samples = list(row.samples or []) + [s.as_record() for s in appended]
        # 报告是「对这批样本与评分的结论」：样本变了，旧结论就不是这份证据的结论。
        _invalidate_if_reported(row, reason="报告形成后又新增了样本")
    await session.flush()
    return appended


async def rater_view(session: AsyncSession, *, eval_id: str) -> list[dict[str, Any]]:
    """评者可见视图：只有 A/B 位置，没有 arm 身份、没有映射、没有正文引用。"""
    row = await _must_get(session, eval_id)
    return [sample.as_payload() for sample in (await _samples_of(row)).values()]


async def _samples_of(row: EvalRunModel) -> dict[str, domain.BlindSample]:
    records: dict[str, domain.BlindSample] = {}
    for payload in row.samples or []:
        sample = domain.BlindSample.from_record(payload)
        if sample is not None:
            records[sample.sample_id] = sample
    return records


async def submit_ratings(
    session: AsyncSession,
    *,
    eval_id: str,
    rater: str,
    ratings: Sequence[domain.PositionalScore],
) -> int:
    """评者按 A/B 位置提交评分：位置→arm 的还原只在服务端发生。

    评者手里没有 arm 标签，因此入口只有位置；若允许评者直接报 arm，盲评的前提
    就消失了（他知道自己在给哪一版打分）。
    """
    row = await _must_get(session, eval_id)
    raters = tuple((row.config or {}).get("raters") or ())
    if str(rater) not in raters:
        raise GuardViolation(
            f"评者未预注册：{rater}", available_actions=["register_rater"]
        )
    records = await _samples_of(row)
    resolved: list[domain.BlindScore] = []
    for entry in ratings:
        sample = records.get(str(entry.sample_id))
        if sample is None:
            raise GuardViolation(
                f"样本未注册：{entry.sample_id}", available_actions=["add_samples"]
            )
        arm = domain.arm_at_position(sample, entry.position)
        if arm is None:
            raise GuardViolation(f"样本上没有这个位置：{entry.position}")
        resolved.append(
            domain.BlindScore(
                sample_id=sample.sample_id, arm=arm, rater=str(rater),
                scores=dict(entry.scores), preferred=bool(entry.preferred),
            )
        )
    return await record_scores(session, eval_id=eval_id, scores=resolved)


async def record_scores(
    session: AsyncSession, *, eval_id: str, scores: Sequence[domain.BlindScore]
) -> int:
    """记录人评（去重后按 triple 保留最后一次）。

    只接受**已注册样本**与**预注册评者**，且 arm 必须是该样本映射里存在的一条：
    凭空造分数是最省力的晋级方式，不接受比事后发现便宜得多。
    """
    row = await _must_get(session, eval_id)
    raters = tuple((row.config or {}).get("raters") or ())
    records = await _samples_of(row)
    accepted: list[dict[str, Any]] = []
    for score in scores:
        sample = records.get(str(score.sample_id))
        if sample is None:
            raise GuardViolation(
                f"样本未注册：{score.sample_id}", available_actions=["add_samples"]
            )
        if raters and str(score.rater) not in raters:
            raise GuardViolation(
                f"评者未预注册：{score.rater}", available_actions=["register_rater"]
            )
        if score.arm not in sample.arms_in_order:
            raise GuardViolation(
                f"样本 {score.sample_id} 上没有 arm {score.arm}"
            )
        accepted.append(score.as_payload())
    index = {
        (r["sample_id"], r["arm"], r["rater"]): r for r in (row.scores or [])
    }
    index.update({(r["sample_id"], r["arm"], r["rater"]): r for r in accepted})
    changed = row.scores != [index[key] for key in sorted(index)]
    row.scores = [index[key] for key in sorted(index)]
    if changed:
        _invalidate_if_reported(row, reason="报告形成后评分发生变化")
    await session.flush()
    return len(accepted)


async def build_report(
    session: AsyncSession,
    *,
    eval_id: str,
    cost_minor_per_chapter: dict[str, int] | None = None,
    latency_ms_p95: dict[str, int] | None = None,
    replay_gain: dict[str, float] | None = None,
    model: str = "",
    cost_minor_per_scene: dict[str, int] | None = None,
    latency_ms_per_scene_p95: dict[str, int] | None = None,
    run_refs: dict[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """出报告：把「结论」和「为结论作证的证据」一起冻结下来。

    指纹一律**重算**，不沿用库里那份：那份值本来就是用来发现篡改的，拿它跟自己比
    等于没有校验。
    """
    row = await _must_get(session, eval_id)
    scores = [domain.BlindScore(**s) for s in (row.scores or [])]
    config = row.config or {}
    arms = tuple(config.get("arms") or ("v1", "v2"))
    samples = await _samples_of(row)
    reports = []
    for arm in arms:
        report = domain.summarise(scores, arm)
        reports.append(
            domain.ArmReport(
                arm=report.arm,
                n=report.n,
                ratings=report.ratings,
                means=report.means,
                cost_minor_per_chapter=int((cost_minor_per_chapter or {}).get(arm, 0)),
                latency_ms_p95=int((latency_ms_p95 or {}).get(arm, 0)),
                replay_gain=float((replay_gain or {}).get(arm, 0.0)),
                cost_minor_per_scene=int((cost_minor_per_scene or {}).get(arm, 0)),
                latency_ms_per_scene_p95=int(
                    (latency_ms_per_scene_p95 or {}).get(arm, 0)
                ),
                run_refs=tuple(str(r) for r in (run_refs or {}).get(arm, ())),
            )
        )
    evidence = _collect_evidence(
        row,
        arms=arms,
        reports=reports,
        cost=cost_minor_per_chapter or {},
        latency=latency_ms_p95 or {},
        model=model,
        runs=run_refs or {},
    )
    current_fingerprint = domain.fingerprint_of(config)
    payload = {
        "eval_id": eval_id,
        "arms": [r.as_payload() for r in reports],
        "samples": len(samples),
        "ratings": len(scores),
        "evidence": evidence.as_payload(),
        "config_fingerprint": current_fingerprint,
        "config_drifted": current_fingerprint != str(row.config_fingerprint or ""),
    }
    row.report = payload
    row.report_fingerprint = domain.report_fingerprint_of(
        payload, config_fingerprint=current_fingerprint
    )
    await session.flush()
    return payload


def _collect_evidence(
    row: EvalRunModel,
    *,
    arms: Sequence[str],
    reports: Sequence[domain.ArmReport],
    cost: dict[str, int],
    latency: dict[str, int],
    model: str,
    runs: dict[str, Sequence[str]] | None = None,
) -> domain.Evidence:
    """把「哪些证据真的交上来了」逐项落定，交给 domain 判齐不齐。

    模型名**不回填**冻结配置：那样一来「没记录模型」会被伪装成「记录的就是要求
    的模型」，而这条证据本来是要用来发现「跑的模型不对」的（B-03）。
    """
    samples = list(row.samples or [])
    total = len(samples) or 1
    calm = sum(1 for s in samples if s.get("calm")) / total
    solo = sum(1 for s in samples if s.get("solo")) / total
    # 每个位置都要有正文引用：只要一个 arm 留空，就无法证明评的是哪一版内容。
    complete = bool(samples) and all(
        len(list(s.get("content_refs") or [])) >= len(list(s.get("arms_in_order") or ()))
        and all(str(ref).strip() for ref in (s.get("content_refs") or []))
        for s in samples
    )
    runs = runs or {}
    return domain.Evidence(
        model=str(model or ""),
        cost_provided=tuple(a for a in arms if int(cost.get(a, 0)) > 0),
        latency_provided=tuple(a for a in arms if int(latency.get(a, 0)) > 0),
        registered_samples=len(samples),
        raters_registered=len((row.config or {}).get("raters") or ()),
        calm_ratio=round(calm, 4),
        solo_ratio=round(solo, 4),
        content_refs_complete=complete,
        runs_bound=tuple(a for a in arms if any(str(r).strip() for r in runs.get(a, ()))),
    )


async def decide(session: AsyncSession, *, eval_id: str) -> tuple[str, tuple[str, ...]]:
    """按冻结的阈值裁决。配置漂移、证据缺失、样本不足一律 HOLD。"""
    row = await _must_get(session, eval_id)
    if str(row.report_fingerprint or "").startswith("invalidated:"):
        # 显式作废是一次决定，裁决不得把它改写成「指纹不符」。
        return "HOLD", tuple(row.verdict_reasons or [])
    config = row.config or {}
    thresholds_payload = config.get("thresholds") or {}
    thresholds = domain.PromotionThresholds(**thresholds_payload)
    budget_payload = config.get("budget") or {}
    budget = domain.BudgetBand(
        model=str(budget_payload.get("model") or ""),
        max_cost_minor_per_scene=int(budget_payload.get("max_cost_minor_per_scene") or 0),
        max_cost_minor_per_chapter=int(
            budget_payload.get("max_cost_minor_per_chapter") or 0
        ),
        max_latency_ms_per_scene=int(budget_payload.get("max_latency_ms_per_scene") or 0),
    )
    sample_payload = config.get("sample") or {}
    sample_spec = domain.SampleSpec(
        total=int(sample_payload.get("total") or 0),
        calm_scene_ratio=float(sample_payload.get("calm_scene_ratio") or 0.0),
        solo_scene_ratio=float(sample_payload.get("solo_scene_ratio") or 0.0),
        levels=tuple(sample_payload.get("levels") or ()),
    )
    arms = tuple(config.get("arms") or ("v1", "v2"))

    # ① 配置漂移：重算冻结配置的指纹，不信任库里那份存档值
    current = domain.fingerprint_of(config)
    if current != str(row.config_fingerprint or ""):
        return await _hold(session, row, "冻结配置在采样后被改动：指纹重算不符")

    report = row.report or {}
    if not report:
        return await _hold(session, row, "尚未出报告")
    # ② 报告篡改：报告内容与报告指纹必须能对上
    expected = domain.report_fingerprint_of(report, config_fingerprint=current)
    if expected != str(row.report_fingerprint or ""):
        return await _hold(session, row, "报告内容与其指纹不符：不得据其晋级")

    evidence = domain.Evidence.from_payload(report.get("evidence"))
    resolved: dict[str, domain.ArmReport] = {}
    for arm_payload in (report.get("arms") or []):
        resolved[arm_payload["arm"]] = domain.ArmReport(
            arm=arm_payload["arm"],
            n=int(arm_payload.get("n") or 0),
            ratings=int(arm_payload.get("ratings") or 0),
            means=dict(arm_payload.get("means") or {}),
            cost_minor_per_chapter=int(arm_payload.get("cost_minor_per_chapter") or 0),
            latency_ms_p95=int(arm_payload.get("latency_ms_p95") or 0),
            replay_gain=float(arm_payload.get("replay_gain") or 0.0),
            cost_minor_per_scene=int(arm_payload.get("cost_minor_per_scene") or 0),
            latency_ms_per_scene_p95=int(
                arm_payload.get("latency_ms_per_scene_p95") or 0
            ),
            run_refs=tuple(str(r) for r in (arm_payload.get("run_refs") or ())),
        )
    baseline = resolved.get(arms[0]) or domain.ArmReport(arms[0])
    challenger = (
        resolved.get(arms[1]) or domain.ArmReport(arms[1])
        if len(arms) > 1
        else baseline
    )
    verdict, reasons = domain.verdict(
        challenger,
        baseline,
        thresholds=thresholds,
        config_fingerprint=current,
        # 报告指纹已在上面核对过；此处传同值让 domain 的漂移检查保持通过
        report_fingerprint=current,
        evidence=evidence,
        budget=budget,
        sample=sample_spec,
        arms=arms,
    )
    row.verdict = verdict
    row.verdict_reasons = list(reasons)
    await session.flush()
    return verdict, reasons


async def _hold(
    session: AsyncSession, row: EvalRunModel, reason: str
) -> tuple[str, tuple[str, ...]]:
    row.verdict = "HOLD"
    row.verdict_reasons = [reason]
    await session.flush()
    return "HOLD", (reason,)


def _invalidate_if_reported(row: EvalRunModel, *, reason: str) -> bool:
    """报告已经出了，而支撑它的样本/评分又变了：报告必须作废，不能留着晋级。

    留着会怎样：裁决读的是旧报告（高分），而库里的样本与评分已经换成另一批，
    「这份结论对应哪些证据」从此说不清（B-03）。
    """
    if not (row.report or {}):
        return False
    if str(row.report_fingerprint or "").startswith("invalidated:"):
        return False
    row.report_fingerprint = f"invalidated:{reason}"[:64]
    row.verdict = "HOLD"
    row.verdict_reasons = [f"报告已作废，需重新出报告：{reason}"]
    return True


async def invalidate_report(session: AsyncSession, *, eval_id: str, reason: str) -> None:
    """配置在采样后被改动：报告作废，只能重跑。"""
    row = await _must_get(session, eval_id)
    row.report_fingerprint = f"invalidated:{reason}"[:64]
    row.verdict = "HOLD"
    row.verdict_reasons = [f"配置已变更，报告作废：{reason}"]
    await session.flush()


async def _must_get(session: AsyncSession, eval_id: str) -> EvalRunModel:
    row = await session.scalar(
        select(EvalRunModel).where(EvalRunModel.eval_id == eval_id)
    )
    if row is None:
        raise LookupError(f"eval not found: {eval_id}")
    return row
