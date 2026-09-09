"""R4 质量验证与灰度（Plan §4 R4 / A-06）。

出口条件对应的断言：
- 冻结：配置指纹参与裁决，采样后改配置即作废（指纹一律重算，不信库存值）；
- 盲评：评者载荷里没有 arm 标签/映射/正文引用；A/B→arm 映射持久化但不对评者可见；
- 同一预算带：成本/延迟/模型进证据，缺一项只能 HOLD；
- 样本计数按**独立样本**，多评者评同一份样本不放大样本量；
- 未注册样本与未预注册评者的评分不被接受；
- 样本不足不得宣布胜出；灰度按作品确定性分桶，运行中的任务不得切换执行器。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import evaluation as eval_app
from regent.novel.domain import evaluation as domain
from regent.novel.domain.errors import GuardViolation

ARMS = ("v1", "v2")
RATERS = ("r1", "r2", "r3")
MODEL = "deepseek-v3"

V1_MEANS = {
    "read_willingness": 3.0, "character_credibility": 3.4,
    "emotion_and_payoff": 3.2, "fact_error": 0.05,
}
V2_MEANS = {
    "read_willingness": 4.4, "character_credibility": 4.1,
    "emotion_and_payoff": 4.0, "fact_error": 0.02,
}
MEANS_BY_ARM = {"v1": V1_MEANS, "v2": V2_MEANS}
# 每臂绑定的真实运行：报告必须能回答「这些分数是哪次跑出来的」
RUN_REFS: dict[str, tuple[str, ...]] = {"v1": ("run-v1-001",), "v2": ("run-v2-001",)}
SCENE_COST = {"v1": 700, "v2": 720}
SCENE_LATENCY = {"v1": 22000, "v2": 25000}


def _config(**over) -> domain.EvalConfig:
    base = domain.EvalConfig(
        eval_id="eval-2026-r4",
        sample=domain.SampleSpec(total=24, calm_scene_ratio=0.25, solo_scene_ratio=0.25),
        budget=domain.BudgetBand(
            model=MODEL, max_cost_minor_per_scene=800,
            max_cost_minor_per_chapter=6000, max_latency_ms_per_scene=30000,
        ),
        thresholds=domain.PromotionThresholds(
            min_samples=10, min_read_willingness=3.5, min_character_credibility=3.5,
            min_emotion_and_payoff=3.2, max_fact_error=0.1,
            max_cost_minor_per_chapter=6000,
        ),
        raters=RATERS,
    )
    if not over:
        return base
    return domain.EvalConfig(**{**base.__dict__, **over})


def _ok_evidence(**over) -> domain.Evidence:
    base = domain.Evidence(
        model=MODEL,
        cost_provided=ARMS, latency_provided=ARMS,
        registered_samples=24, raters_registered=len(RATERS),
        calm_ratio=0.25, solo_ratio=0.25, content_refs_complete=True,
        runs_bound=ARMS,
    )
    if not over:
        return base
    return domain.Evidence(**{**base.__dict__, **over})


def _verdict_kwargs(**over) -> dict:
    """裁决调用所需的全部前置证据（缺一项就应当 HOLD）。"""
    cfg = _config()
    kwargs = dict(
        thresholds=cfg.thresholds, config_fingerprint="same", report_fingerprint="same",
        evidence=_ok_evidence(), budget=cfg.budget, sample=cfg.sample, arms=ARMS,
    )
    kwargs.update(over)
    return kwargs


def _samples(count: int = 24) -> list[domain.BlindSample]:
    """构成达标的样本：舒缓与单人各占 1/4，每个位置都带正文引用。"""
    return [
        domain.make_sample(
            f"s-{i}", ARMS, calm=(i % 4 == 1), solo=(i % 4 == 3),
            content_refs=(f"chapter-ref-{i}-A", f"chapter-ref-{i}-B"),
        )
        for i in range(count)
    ]


def _scores(arm: str, n: int, **means) -> list[domain.BlindScore]:
    return [
        domain.BlindScore(
            sample_id=f"s-{i}", arm=arm, rater=RATERS[i % len(RATERS)], scores=dict(means),
        )
        for i in range(n)
    ]


async def _rate_all(session, eval_id: str, samples: list[domain.BlindSample],
                    raters=RATERS) -> None:
    """评者按位置打分：位置到 arm 的还原发生在服务端，评者手里没有 arm。"""
    for rater in raters:
        entries = []
        for sample in samples:
            for position in ("A", "B"):
                arm = domain.arm_at_position(sample, position)
                entries.append(
                    domain.PositionalScore(
                        sample_id=sample.sample_id, position=position,
                        scores=dict(MEANS_BY_ARM[arm]),
                    )
                )
        await eval_app.submit_ratings(
            session, eval_id=eval_id, rater=rater, ratings=entries
        )


def test_frozen_config_has_stable_fingerprint():
    assert _config().fingerprint == _config().fingerprint
    changed = _config(thresholds=domain.PromotionThresholds(
        min_samples=5, min_read_willingness=3.5, min_character_credibility=3.5,
        min_emotion_and_payoff=3.2, max_fact_error=0.1, max_cost_minor_per_chapter=6000,
    ))
    assert changed.fingerprint != _config().fingerprint
    # 存储重算与对象直算必须同源：两处口径不一致会让漂移检测形同虚设
    assert domain.fingerprint_of(_config().as_payload()) == _config().fingerprint
    # 预注册评者也参与冻结：中途换人等于换了一批人打分
    assert _config(raters=("x",)).fingerprint != _config().fingerprint


def test_blind_payload_hides_arm_identity_and_mapping():
    sample = domain.make_sample("s-1", ARMS, content_refs=("ref-a", "ref-b"))
    payload = sample.as_payload()
    assert "v1" not in str(payload) and "v2" not in str(payload)
    assert "ref-" not in str(payload), "正文引用不得出现在评者载荷里"
    assert payload["options"] == ["A", "B"]
    # 顺序对同一样本稳定：不同评者看到的是同一组 A/B
    assert domain.shuffle_arms("s-1", list(ARMS)) == sample.arms_in_order
    # 私有映射留在存档里，且能往返
    record = sample.as_record()
    assert record["arms_in_order"] == list(sample.arms_in_order)
    assert domain.BlindSample.from_record(record) == sample
    assert "v1" not in str(payload) and sample.blind_map == dict(
        zip(("A", "B"), sample.arms_in_order, strict=False)
    )


def test_sample_mix_requires_calm_and_solo():
    """样本里必须有舒缓与单人场景，否则评测会奖励机械冲突与多人对白。"""
    spec = _config().sample
    assert spec.calm_scene_ratio > 0 and spec.solo_scene_ratio > 0
    assert set(spec.levels) == {"scene", "chapter", "volume"}


def test_multiple_raters_on_one_sample_count_as_one_sample():
    """A-06：按独立样本计数。多评者评同一份样本不得放大样本量。"""
    rows = [
        domain.BlindScore(sample_id="s-0", arm="v2", rater=rater, scores=V2_MEANS)
        for rater in RATERS
    ]
    report = domain.summarise(rows, "v2")
    assert report.n == 1, f"三个评者不应变成三个样本：n={report.n}"
    assert report.ratings == 3
    verdict, reasons = domain.verdict(
        report, domain.summarise(_scores("v1", 20, **V1_MEANS), "v1"),
        **_verdict_kwargs(),
    )
    assert verdict == "HOLD" and "样本不足" in reasons[0]


def test_missing_evidence_never_promotes():
    """A-06：缺成本/延迟/模型/样本构成/正文引用/预注册评者，一律 HOLD。"""
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=5000,
    )
    baseline = domain.summarise(_scores("v1", 20, **V1_MEANS), "v1")
    for label, over in (
        ("缺成本", {"cost_provided": ("v1",)}),
        ("缺延迟", {"latency_provided": ("v1",)}),
        ("模型不符", {"model": "another-model"}),
        ("缺正文引用", {"content_refs_complete": False}),
        ("注册样本不足", {"registered_samples": 5}),
        ("舒缓占比不足", {"calm_ratio": 0.0}),
        ("单人占比不足", {"solo_ratio": 0.0}),
        ("无预注册评者", {"raters_registered": 0}),
    ):
        verdict, reasons = domain.verdict(
            challenger, baseline, **_verdict_kwargs(evidence=_ok_evidence(**over)),
        )
        assert verdict == "HOLD", f"{label} 竟然判了 {verdict}"
        assert reasons, f"{label} 没有给出理由"
    # 完全没有证据时同样不得晋级
    verdict, reasons = domain.verdict(
        challenger, baseline, **_verdict_kwargs(evidence=None),
    )
    assert verdict == "HOLD"


def test_insufficient_samples_never_promote():
    challenger = domain.summarise(_scores("v2", 3, **V2_MEANS), "v2")
    baseline = domain.summarise(_scores("v1", 20, **V1_MEANS), "v1")
    verdict, reasons = domain.verdict(
        challenger, baseline, **_verdict_kwargs(),
    )
    assert verdict == "HOLD"
    assert "样本不足" in reasons[0]


def test_config_drift_after_sampling_blocks_verdict():
    challenger = domain.summarise(_scores("v2", 20, **V2_MEANS), "v2")
    baseline = domain.summarise(_scores("v1", 20, **V1_MEANS), "v1")
    verdict, reasons = domain.verdict(
        challenger, baseline,
        **_verdict_kwargs(config_fingerprint="before", report_fingerprint="after"),
    )
    assert verdict == "HOLD" and "配置指纹" in reasons[0]


def test_over_budget_band_blocks_promotion():
    """超同预算带 = 这次比较不成立。它不等于「更差」，所以只能是 HOLD。"""
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=99999,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.0})
    verdict, reasons = domain.verdict(challenger, baseline, **_verdict_kwargs())
    assert verdict == "HOLD"
    assert any("超出同预算带" in r for r in reasons)


def test_budget_band_cap_holds_even_when_thresholds_are_loose():
    """B-03 反例：预算带上限 10、实际 100、门槛上限 1000 时仍不得晋级。"""
    band = domain.BudgetBand(
        model=MODEL, max_cost_minor_per_scene=0,
        max_cost_minor_per_chapter=10, max_latency_ms_per_scene=0,
    )
    loose = _config(
        budget=band,
        thresholds=domain.PromotionThresholds(
            min_samples=10, min_read_willingness=3.5, min_character_credibility=3.5,
            min_emotion_and_payoff=3.2, max_fact_error=0.1,
            max_cost_minor_per_chapter=1000,
        ),
    )
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=100,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.0})
    verdict, reasons = domain.verdict(
        challenger, baseline, **_verdict_kwargs(budget=band, thresholds=loose.thresholds),
    )
    assert verdict == "HOLD", "只查门槛不查预算带，超支 10 倍也能晋级"
    assert any("超出同预算带" in r for r in reasons)


def test_threshold_overrun_rejects_while_band_is_respected():
    """门槛与预算带是两件事：带内超门槛 = 明确更贵 → REJECT。"""
    band = domain.BudgetBand(
        model=MODEL, max_cost_minor_per_scene=0,
        max_cost_minor_per_chapter=99999, max_latency_ms_per_scene=0,
    )
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=7000,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.0})
    verdict, reasons = domain.verdict(
        challenger, baseline, **_verdict_kwargs(budget=band),
    )
    assert verdict == "REJECT"
    assert any("超上限" in r for r in reasons)


def test_scene_level_budget_band_is_enforced():
    """同预算带按场景冻结：只有章级数字就无法发现单场景超支。"""
    band = domain.BudgetBand(
        model=MODEL, max_cost_minor_per_scene=10,
        max_cost_minor_per_chapter=99999, max_latency_ms_per_scene=100,
    )
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=5000,
        cost_minor_per_scene=100, latency_ms_per_scene_p95=500,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.0})
    verdict, reasons = domain.verdict(
        challenger, baseline, **_verdict_kwargs(budget=band),
    )
    assert verdict == "HOLD"
    assert any("单场景成本" in r for r in reasons)
    assert any("单场景时延" in r for r in reasons)


def test_promote_only_when_beating_baseline_on_read_willingness():
    good = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=5000,
        latency_ms_p95=20000, replay_gain=0.3,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.6})
    verdict, _ = domain.verdict(good, baseline, **_verdict_kwargs())
    assert verdict == "PROMOTE"

    not_better = domain.ArmReport(
        "v2", n=20, means={**V2_MEANS, "read_willingness": 3.6},
        cost_minor_per_chapter=5000,
    )
    verdict, reasons = domain.verdict(not_better, baseline, **_verdict_kwargs())
    assert verdict == "HOLD" and "未超过基线" in reasons[0]


@pytest.mark.asyncio
async def test_full_registered_blind_eval_promotes(novel_db):
    """完整链路：样本注册 → 评者按位置打分 → 出报告 → 裁决。"""
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config())
        await eval_app.add_samples(s, eval_id="eval-2026-r4", samples=_samples())
        await _rate_all(s, "eval-2026-r4", _samples())
        report = await eval_app.build_report(
            s, eval_id="eval-2026-r4", model=MODEL,
            cost_minor_per_chapter={"v1": 5200, "v2": 5400},
            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,
            replay_gain={"v2": 0.22},
        )
        verdict, reasons = await eval_app.decide(s, eval_id="eval-2026-r4")
        await s.commit()
    assert report["samples"] == 24
    assert report["evidence"]["registered_samples"] == 24
    assert report["arms"][1]["replay_gain"] == 0.22
    assert verdict == "PROMOTE", reasons


@pytest.mark.asyncio
async def test_unregistered_sample_or_rater_is_refused(novel_db):
    """A-06：凭空造分数是最省力的晋级方式，入口必须挡住。"""
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config(eval_id="eval-unreg"))
        await eval_app.add_samples(s, eval_id="eval-unreg", samples=_samples(4))
        with pytest.raises(GuardViolation):
            await eval_app.submit_ratings(
                s, eval_id="eval-unreg", rater="r1",
                ratings=[domain.PositionalScore(sample_id="s-999", position="A",
                                                scores=V2_MEANS)],
            )
        with pytest.raises(GuardViolation):
            await eval_app.submit_ratings(
                s, eval_id="eval-unreg", rater="outsider",
                ratings=[domain.PositionalScore(sample_id="s-0", position="A",
                                                scores=V2_MEANS)],
            )
        # 绕过入口直接落 arm 评分也要被样本注册校验挡下
        with pytest.raises(GuardViolation):
            await eval_app.record_scores(
                s, eval_id="eval-unreg",
                scores=[domain.BlindScore(sample_id="s-404", arm="v2", rater="r1",
                                          scores=V2_MEANS)],
            )
        view = await eval_app.rater_view(s, eval_id="eval-unreg")
    assert "v1" not in str(view) and "v2" not in str(view)
    assert all(item["options"] == ["A", "B"] for item in view)


@pytest.mark.asyncio
async def test_zero_registered_samples_cannot_promote(novel_db):
    """A-06：一个样本都没注册，评分再漂亮也只能 HOLD。"""
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config(eval_id="eval-zero"))
        report = await eval_app.build_report(
            s, eval_id="eval-zero", model=MODEL,
            cost_minor_per_chapter={"v1": 5200, "v2": 5400},
            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,
        )
        verdict, reasons = await eval_app.decide(s, eval_id="eval-zero")
        await s.commit()
    assert report["samples"] == 0
    assert verdict == "HOLD"
    assert any("注册样本不足" in r or "预注册评者" in r for r in reasons)


@pytest.mark.asyncio
async def test_missing_cost_evidence_holds(novel_db):
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config(eval_id="eval-nocost"))
        await eval_app.add_samples(s, eval_id="eval-nocost", samples=_samples())
        await _rate_all(s, "eval-nocost", _samples())
        await eval_app.build_report(
            s, eval_id="eval-nocost", model=MODEL,
            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,  # 故意不给成本
        )
        verdict, reasons = await eval_app.decide(s, eval_id="eval-nocost")
        await s.commit()
    assert verdict == "HOLD"
    assert any("成本" in r for r in reasons)


@pytest.mark.asyncio
async def test_tampered_threshold_is_detected(novel_db):
    """A-06：采样后改门槛必须被指纹重算发现，即使改得「更容易」。"""
    async with novel_db() as s:
        row = await eval_app.freeze_eval(s, config=_config(eval_id="eval-tamper"))
        await eval_app.add_samples(s, eval_id="eval-tamper", samples=_samples())
        await _rate_all(s, "eval-tamper", _samples())
        await eval_app.build_report(
            s, eval_id="eval-tamper", model=MODEL,
            cost_minor_per_chapter={"v1": 5200, "v2": 5400},
            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,
        )
        before, _ = await eval_app.decide(s, eval_id="eval-tamper")
        # 直接把冻结配置里的门槛改松（模拟库里被改）
        config = dict(row.config or {})
        thresholds = dict(config.get("thresholds") or {})
        thresholds["min_samples"] = 1
        config["thresholds"] = thresholds
        row.config = config
        await s.flush()
        verdict, reasons = await eval_app.decide(s, eval_id="eval-tamper")
        await s.commit()
    assert before == "PROMOTE"
    assert verdict == "HOLD"
    assert "指纹" in reasons[0]


@pytest.mark.asyncio
async def test_tampered_report_is_detected(novel_db):
    """报告内容被改而指纹没跟着改，同样不得晋级。"""
    async with novel_db() as s:
        row = await eval_app.freeze_eval(s, config=_config(eval_id="eval-tamper2"))
        await eval_app.add_samples(s, eval_id="eval-tamper2", samples=_samples())
        await _rate_all(s, "eval-tamper2", _samples())
        await eval_app.build_report(
            s, eval_id="eval-tamper2", model=MODEL,
            cost_minor_per_chapter={"v1": 5200, "v2": 5400},
            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,
        )
        report = dict(row.report or {})
        arms = [dict(a) for a in (report.get("arms") or [])]
        if len(arms) > 1:
            arms[1]["means"] = dict(arms[1].get("means") or {})
            arms[1]["means"]["read_willingness"] = 5.0
        report["arms"] = arms
        row.report = report
        await s.flush()
        verdict, reasons = await eval_app.decide(s, eval_id="eval-tamper2")
        await s.commit()
    assert verdict == "HOLD"
    assert "指纹" in reasons[0]


@pytest.mark.asyncio
async def test_eval_run_invalidated_when_config_changed(novel_db):
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config(eval_id="eval-drift"))
        await eval_app.invalidate_report(s, eval_id="eval-drift", reason="rubric_v2")
        verdict, reasons = await eval_app.decide(s, eval_id="eval-drift")
        await s.commit()
    assert verdict == "HOLD" and "作废" in reasons[0]


def test_canary_bucketing_is_stable_and_bounded():
    work_id = str(uuid.uuid4())
    assert domain.canary_bucket(work_id) == domain.canary_bucket(work_id)
    assert 0 <= domain.canary_bucket(work_id) < 100
    assert domain.assign_executor(work_id, canary_percent=0,
                                  stable_executor="stable", canary_executor="canary") == "stable"
    assert domain.assign_executor(work_id, canary_percent=100,
                                  stable_executor="stable", canary_executor="canary") == "canary"


def test_in_flight_run_cannot_switch_executor():
    assert not domain.switch_executor_allowed("RUNNING", target="canary", current="stable")
    assert not domain.switch_executor_allowed("PENDING_DECISION", target="canary", current="stable")
    assert domain.switch_executor_allowed("CANONIZED", target="canary", current="stable")
    assert domain.switch_executor_allowed("RUNNING", target="stable", current="stable")


def test_model_evidence_is_never_backfilled_from_config():
    """B-03：没记录模型就是没记录，不能用冻结配置里的模型名冒充。"""
    cfg = _config()
    evidence = _ok_evidence(model="")
    missing = domain.missing_evidence(
        evidence, arms=ARMS, budget=cfg.budget, sample=cfg.sample
    )
    assert any("模型" in r for r in missing), "空模型名被当成「符合要求」放行了"


@pytest.mark.asyncio
async def test_report_is_invalidated_when_samples_change_afterwards(novel_db):
    """B-03：报告是对当时那批证据的结论；样本/评分变了，旧结论必须作废。"""
    async with novel_db() as s:
        await eval_app.freeze_eval(s, config=_config(eval_id="eval-late"))
        await eval_app.add_samples(s, eval_id="eval-late", samples=_samples())
        await _rate_all(s, "eval-late", _samples())
        await eval_app.build_report(
            s, eval_id="eval-late", model=MODEL,
            cost_minor_per_chapter={"v1": 5200, "v2": 5400},
            latency_ms_p95={"v1": 18000, "v2": 21000},
            run_refs=RUN_REFS,
        )
        before, _ = await eval_app.decide(s, eval_id="eval-late")
        # 出报告之后又补了一批样本：结论已经不对应这批证据
        await eval_app.add_samples(
            s,
            eval_id="eval-late",
            samples=[domain.make_sample("s-late", ARMS, content_refs=("ref-a", "ref-b"))],
        )
        after, reasons = await eval_app.decide(s, eval_id="eval-late")
        await s.commit()
    assert before == "PROMOTE"
    assert after == "HOLD"
    assert any("作废" in r for r in reasons)
