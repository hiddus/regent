"""同步 B-03：评估证据新增「每臂真实运行绑定」，预算带从门槛里独立出来。

只新增必需证据的构造，不放宽任何断言。另补 B-03 的三条反例。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
path = ROOT / "tests/unit/novel/test_eval_and_canary.py"
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str, count: int = 1) -> None:
    global text
    got = text.count(old)
    assert got == count, f"old 出现 {got} 次（期望 {count}）-> {old[:70]!r}"
    text = text.replace(old, new)


# 1) 每臂绑定真实运行：没有它，「这些分数来自哪次运行」无法回答。
sub(
    """MEANS_BY_ARM = {"v1": V1_MEANS, "v2": V2_MEANS}""",
    """MEANS_BY_ARM = {"v1": V1_MEANS, "v2": V2_MEANS}
# 每臂绑定的真实运行：报告必须能回答「这些分数是哪次跑出来的」
RUN_REFS: dict[str, tuple[str, ...]] = {"v1": ("run-v1-001",), "v2": ("run-v2-001",)}
SCENE_COST = {"v1": 700, "v2": 720}
SCENE_LATENCY = {"v1": 22000, "v2": 25000}""",
)

sub(
    """        registered_samples=24, raters_registered=len(RATERS),
        calm_ratio=0.25, solo_ratio=0.25, content_refs_complete=True,
    )""",
    """        registered_samples=24, raters_registered=len(RATERS),
        calm_ratio=0.25, solo_ratio=0.25, content_refs_complete=True,
        runs_bound=ARMS,
    )""",
)

# 2) build_report 调用补上场景级成本/时延与运行绑定
sub(
    """            latency_ms_p95={"v1": 18000, "v2": 21000},""",
    """            latency_ms_p95={"v1": 18000, "v2": 21000},
            cost_minor_per_scene=SCENE_COST, latency_ms_per_scene_p95=SCENE_LATENCY,
            run_refs=RUN_REFS,""",
    count=5,
)
# 「故意不给成本」那处已在上面的 replace-all 里一并覆盖（同字符串）

# 3) 预算带独立于门槛：超带是「比较不成立」，不是「更差」
sub(
    """def test_over_budget_band_blocks_promotion():
    challenger = domain.ArmReport(
        "v2", n=20, means=V2_MEANS, cost_minor_per_chapter=99999,
    )
    baseline = domain.ArmReport("v1", n=20, means={"read_willingness": 3.0})
    verdict, reasons = domain.verdict(challenger, baseline, **_verdict_kwargs())
    assert verdict == "REJECT"
    assert any("超上限" in r for r in reasons)""",
    '''def test_over_budget_band_blocks_promotion():
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
    assert any("单场景时延" in r for r in reasons)''',
)

# 4) 新增：模型证据不得回填；报告形成后改样本/评分必须作废
text += '''

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
            s, eval_id="eval-late", samples=_samples(1)[:1]
        )
        after, reasons = await eval_app.decide(s, eval_id="eval-late")
        await s.commit()
    assert before == "PROMOTE"
    assert after == "HOLD"
    assert any("作废" in r for r in reasons)
'''

path.write_text(text, encoding="utf-8")
print("patched test_eval_and_canary.py")
