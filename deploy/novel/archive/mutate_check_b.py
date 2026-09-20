"""B-01 / B-02 / B-03 / B-05 的离线反证（不调用模型、不连接服务器）。

为什么要有这个文件：回归通过只能说明「当前实现没被测出来」，不能说明「缺陷修
好了」。每一条反证都构造**旧行为**，并断言现实现给出不同结果——如果某条反证
不成立，说明对应的修复是空的。
"""

from __future__ import annotations

from regent.novel.domain import ending, evaluation, memory

CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, bool(ok)))


# ---------------------------------------------------------------------------
# B-05 自然完结
# ---------------------------------------------------------------------------

# 旧行为：末节点完成一律先扩卷，扩不出来才结束。
check(
    "B-05 用户设定的卷数写完即结束，不必先试扩卷",
    ending.decide_ending(
        intent=ending.EndingIntent(target_volume_count=2), volume_no=2
    ).complete,
)
check(
    "B-05 未达用户卷数时继续扩卷",
    ending.decide_ending(
        intent=ending.EndingIntent(target_volume_count=3), volume_no=1
    ).expand,
)
# 旧行为把「模型故障」当成「还没讲完」→ 自动扩卷
check(
    "B-05 导演没有判定时是 undecided，不是默认继续扩卷",
    ending.decide_ending(
        intent=ending.EndingIntent(), volume_no=1, director_complete=None
    ).choice
    == ending.UNDECIDED,
)
check(
    "B-05 导演判达成即结束",
    ending.decide_ending(
        intent=ending.EndingIntent(ending_statement="他交出钥匙"),
        volume_no=1,
        director_complete=True,
    ).complete,
)
check(
    "B-05 判定依据可查（不是拍脑袋）",
    ending.decide_ending(
        intent=ending.EndingIntent(target_volume_count=1), volume_no=1
    ).basis
    == ending.BASIS_USER_VOLUME,
)

# ---------------------------------------------------------------------------
# B-01 正式长期记忆
# ---------------------------------------------------------------------------

# 旧行为：正式事实按人物共现分类，承诺被记成人物弧线
kind, basis = memory.classify_with_basis(
    {"statement": "甲承诺明日归还钥匙", "known_by": ["甲"]}, cast=["甲"]
)
check("B-01 承诺不再被记成人物弧线", kind == "promise" and basis == memory.BASIS_PROMISE_SIGNAL)

# 旧行为：同一人物同类记忆互相覆盖，伏笔丢在覆盖里
promises = memory.extract_items(
    [
        {"statement": "甲承诺明日归还钥匙", "known_by": ["甲"]},
        {"statement": "甲立誓三年后复仇", "known_by": ["甲"]},
    ],
    chapter_no=1,
    cast=["甲"],
)
check("B-01 同人的两条承诺各成一条", len(promises) == 2)

# 旧行为：共现推断的关系与显式关系变化无法区分
items = [
    *memory.extract_items(
        [{"statement": "甲与乙同在厅中", "known_by": ["甲", "乙"]}],
        chapter_no=1,
        cast=["甲", "乙"],
    ),
    *memory.extract_items(
        [{"statement": "甲与乙当场反目", "known_by": ["甲", "乙"]}],
        chapter_no=1,
        cast=["甲", "乙"],
    ),
]
confidences = {i.basis: i.confidence for i in items}
check(
    "B-01 共现推断与显式关系变化置信度不同",
    confidences.get(memory.BASIS_CO_OCCURRENCE) == "medium"
    and confidences.get(memory.BASIS_RELATION_SIGNAL) == "high",
)

check(
    "B-01 世界规则仍按 (kind, subject) 归一，更新替换旧值",
    memory.item_key("rule", "灯") == memory.item_key("rule", "灯", "换内容"),
)
check(
    "B-01 承诺键带内容指纹，不同承诺不互相覆盖",
    memory.item_key("promise", "甲", "甲承诺归还") != memory.item_key(
        "promise", "甲", "甲立誓复仇"
    ),
)

# 六视图与可见性边界
views_items = memory.extract_items(
    [
        {"memory_kind": "reader_knowledge", "subject": "钥匙", "fact": "读者未见来处"},
        {"memory_kind": "director_note", "subject": "雨景", "fact": "雨景写得太满"},
    ],
    chapter_no=1,
)
for audience in ("character", "narrator", "reader"):
    check(
        f"B-01 导演记忆对 {audience} 不可见",
        not any(i.kind == "director_note" for i in memory.project_for(views_items, audience)),
    )
check(
    "B-01 读者认知不给人物",
    not any(
        i.kind == "reader_knowledge" for i in memory.project_for(views_items, "character")
    ),
)

# ---------------------------------------------------------------------------
# B-02 依赖完整性
# ---------------------------------------------------------------------------

trio = memory.extract_items(
    [
        {"memory_kind": "promise", "subject": "a", "fact": "a 待兑现"},
        {"memory_kind": "promise", "subject": "b", "fact": "b 待兑现"},
        {"memory_kind": "promise", "subject": "c", "fact": "c 待兑现"},
    ],
    chapter_no=1,
)
key_of = {i.subject: i.key for i in trio}
# 旧行为：有边就宣称完整，无法区分 c 独立与漏记 b→c
plan = memory.replay_subgraph(trio, [(key_of["a"], key_of["b"])], changed=["a"])
check("B-02 只有部分边时不得宣称完整", not plan.complete and key_of["c"] in plan.unknown)
# 显式登记独立之后才算完整：被改动节点 a 是源头、没有上游，也必须显式登记；
# 否则它会被当成「漏记了依赖」而算作 unknown，图仍不完整（B-02）。
plan = memory.replay_subgraph(
    trio, [(key_of["a"], key_of["b"])], changed=["a"],
    independent=[key_of["a"], key_of["c"]],
)
check("B-02 显式登记独立后才是完整图", plan.complete)
_covered, unknown = memory.coverage_of(trio, [], [])
check("B-02 无任何覆盖记录时全部算未知", len(unknown) == 3)

# ---------------------------------------------------------------------------
# B-03 盲评预算带
# ---------------------------------------------------------------------------

band = evaluation.BudgetBand(
    model="m", max_cost_minor_per_scene=10, max_cost_minor_per_chapter=10,
    max_latency_ms_per_scene=0,
)
loose = evaluation.PromotionThresholds(
    min_samples=1, min_read_willingness=0.0, min_character_credibility=0.0,
    min_emotion_and_payoff=0.0, max_fact_error=1.0, max_cost_minor_per_chapter=1000,
)
challenger = evaluation.ArmReport(
    "v2", n=20, means={"read_willingness": 4.4, "character_credibility": 4.1,
                       "emotion_and_payoff": 4.0, "fact_error": 0.02},
    cost_minor_per_chapter=100,
)
baseline = evaluation.ArmReport("v1", n=20, means={"read_willingness": 3.0})
evidence = evaluation.Evidence(
    model="m", cost_provided=("v1", "v2"), latency_provided=("v1", "v2"),
    registered_samples=20, raters_registered=3, calm_ratio=0.3, solo_ratio=0.3,
    content_refs_complete=True, runs_bound=("v1", "v2"),
)
verdict, reasons = evaluation.verdict(
    challenger,
    baseline,
    thresholds=loose,
    config_fingerprint="same",
    report_fingerprint="same",
    evidence=evidence,
    budget=band,
    arms=("v1", "v2"),
)
check(
    "B-03 预算带上限 10、实际 100 不得晋级（门槛 1000 也不行）",
    verdict == "HOLD" and any("超出同预算带" in r for r in reasons),
)
# 预算带内（两臂花的钱相同，比较有效）但超出单章成本门槛 → REJECT。
# 注意：不能只靠「门槛宽」就晋级——challenger 花 100，门槛收紧到 50 就必须 REJECT；
# 同时预算带仍是宽松的 99999，确保不是因为「超预算带」才被拦（那是 HOLD 的语义）。
cost_capped = evaluation.PromotionThresholds(
    min_samples=1, min_read_willingness=0.0, min_character_credibility=0.0,
    min_emotion_and_payoff=0.0, max_fact_error=1.0, max_cost_minor_per_chapter=50,
)
check(
    "B-03 预算带内且超门槛才 REJECT",
    evaluation.verdict(
        challenger,
        baseline,
        thresholds=cost_capped,
        config_fingerprint="same",
        report_fingerprint="same",
        evidence=evidence,
        budget=evaluation.BudgetBand(
            model="m", max_cost_minor_per_scene=0,
            max_cost_minor_per_chapter=99999, max_latency_ms_per_scene=0,
        ),
        arms=("v1", "v2"),
    )[0]
    == "REJECT",
)
check(
    "B-03 单场景时延超限也不晋级",
    bool(
        evaluation.budget_violations(
            evaluation.ArmReport("v2", cost_minor_per_scene=5, latency_ms_per_scene_p95=999),
            baseline,
            evaluation.BudgetBand(
                model="m", max_cost_minor_per_scene=0,
                max_cost_minor_per_chapter=0, max_latency_ms_per_scene=10,
            ),
        )
    ),
)
# 旧行为：模型名没记录时回填冻结配置里的值
check(
    "B-03 缺模型证据时不得放行",
    any(
        "模型" in r
        for r in evaluation.missing_evidence(
            evaluation.Evidence(model="", cost_provided=("v1",), latency_provided=("v1",),
                                registered_samples=20, raters_registered=3,
                                content_refs_complete=True, runs_bound=("v1",)),
            arms=("v1",),
            budget=band,
        )
    ),
)
check(
    "B-03 缺运行绑定时不得放行",
    any(
        "运行绑定" in r
        for r in evaluation.missing_evidence(
            evaluation.Evidence(model="m", cost_provided=("v1",), latency_provided=("v1",),
                                registered_samples=20, raters_registered=3,
                                content_refs_complete=True),
            arms=("v1",),
            budget=band,
        )
    ),
)

# ---------------------------------------------------------------------------

failed = [name for name, ok in CHECKS if not ok]
for name, ok in CHECKS:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} 反证成立")
if failed:
    raise SystemExit(f"以下反证没有咬住：{failed}")
