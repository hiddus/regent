"""Offline counterexamples for the September 9 architecture audit."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core' / 'src'))
from regent.novel.domain import memory as m, evaluation as e
from regent.novel.application.direction import VerifiedFact
from regent.novel.application.executor import KNOWN_EXECUTORS, STABLE_EXECUTOR

facts = [VerifiedFact(statement='甲承诺明日归还钥匙', quote='明日归还', known_by=['甲'], entities=['甲']).model_dump()]
items = m.extract_items(facts, chapter_no=1, cast=['甲'])
graph = [m.MemoryItem(key=k, kind='promise', subject=k, content=k) for k in ['a', 'b', 'c']]
plan = m.replay_subgraph(graph, [('a', 'b')], changed=['a'])
means = dict(read_willingness=5, character_credibility=5, emotion_and_payoff=5, fact_error=0)
verdict = e.verdict(
    e.ArmReport('v2', n=10, means=means, cost_minor_per_chapter=100, latency_ms_p95=99999),
    e.ArmReport('v1', n=10, means={**means, 'read_willingness': 4}, cost_minor_per_chapter=1),
    thresholds=e.PromotionThresholds(10, 4, 4, 4, 0.1, 1000),
    config_fingerprint='same', report_fingerprint='same',
    budget=e.BudgetBand('model', 1, 10, 10),
    sample=e.SampleSpec(total=10, calm_scene_ratio=0, solo_scene_ratio=0),
    evidence=e.Evidence(model='model', cost_provided=('v1','v2'), latency_provided=('v1','v2'), registered_samples=10, raters_registered=1, content_refs_complete=True),
)
print(json.dumps({'promise_classification': [i.kind for i in items], 'partial_graph_complete': plan.complete, 'partial_graph_keys': plan.keys, 'budget_limit': 10, 'actual_cost': 100, 'budget_violation_verdict': verdict, 'known_executors': sorted(KNOWN_EXECUTORS), 'stable_executor': STABLE_EXECUTOR}, ensure_ascii=False, indent=2))
