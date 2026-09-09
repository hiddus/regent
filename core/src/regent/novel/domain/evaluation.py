"""质量验证与灰度裁决（Plan §4 R4）。

这个模块只做**判定**，不做采样、不做人评采集、也不发起模型调用。所有判定都是
纯函数，输入相同必得相同结论，因此「凭什么晋级」可以复算。

三条硬规矩：

1. **冻结即生效**：配置（样本、rubric、预算带、停止条件、晋级阈值）一旦冻结，
   指纹就写进报告；报告与配置指纹不符的评估不得用于晋级。
2. **盲评不可逆**：分组顺序由 ``(eval_id, sample_id)`` 确定性洗牌，arm 标签
   不进入评者可见载荷；导演自评不进入人评汇总。
3. **样本不足不得宣布胜出**：未达最小样本量时结论只能是 HOLD，
   不允许用「趋势看起来不错」代替统计结论。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

RUBRIC_VERSION = 1

# 评维度：与 Plan R4 的报告项一一对应
CRITERIA: tuple[str, ...] = (
    "read_willingness",     # 阅读意愿
    "character_credibility",  # 人物可信度
    "emotion_and_payoff",   # 情绪与兑现
    "fact_error",           # 事实错误（越低越好）
)
LOWER_IS_BETTER: frozenset[str] = frozenset({"fact_error"})

Verdict = Literal["PROMOTE", "HOLD", "REJECT"]


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BudgetBand:
    """同预算带：两次评估必须花同样的钱，否则比的不是策略而是钱包。"""

    model: str
    max_cost_minor_per_scene: int
    max_cost_minor_per_chapter: int
    max_latency_ms_per_scene: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_cost_minor_per_scene": self.max_cost_minor_per_scene,
            "max_cost_minor_per_chapter": self.max_cost_minor_per_chapter,
            "max_latency_ms_per_scene": self.max_latency_ms_per_scene,
        }


@dataclass(frozen=True)
class SampleSpec:
    """样本构成。舒缓场景与单人场景必须占一定比例——否则评测会奖励机械冲突
    与多人对白，因为那类场景更容易写出「有戏」的错觉。"""

    total: int
    calm_scene_ratio: float = 0.2
    solo_scene_ratio: float = 0.2
    levels: tuple[str, ...] = ("scene", "chapter", "volume")

    def as_payload(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "calm_scene_ratio": self.calm_scene_ratio,
            "solo_scene_ratio": self.solo_scene_ratio,
            "levels": list(self.levels),
        }


@dataclass(frozen=True)
class PromotionThresholds:
    """晋级门槛。``min_samples`` 是硬门槛，达不到一律 HOLD。"""

    min_samples: int
    min_read_willingness: float
    min_character_credibility: float
    min_emotion_and_payoff: float
    max_fact_error: float
    max_cost_minor_per_chapter: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "min_samples": self.min_samples,
            "min_read_willingness": self.min_read_willingness,
            "min_character_credibility": self.min_character_credibility,
            "min_emotion_and_payoff": self.min_emotion_and_payoff,
            "max_fact_error": self.max_fact_error,
            "max_cost_minor_per_chapter": self.max_cost_minor_per_chapter,
        }


@dataclass(frozen=True)
class EvalConfig:
    """一次评估的完整冻结配置。指纹变化即视为另一次评估。

    ``raters`` 是**预注册评者**：先招募再采样，中途加人等于换了一批人打分。
    空列表表示没有预注册门槛——此时任何评分都不被接受（见 missing_evidence）。
    """

    eval_id: str
    rubric_version: int = RUBRIC_VERSION
    arms: tuple[str, ...] = ("v1", "v2")
    sample: SampleSpec = SampleSpec(total=0)
    budget: BudgetBand = BudgetBand(model="", max_cost_minor_per_scene=0,
                                    max_cost_minor_per_chapter=0)
    thresholds: PromotionThresholds = PromotionThresholds(
        min_samples=0, min_read_willingness=0.0, min_character_credibility=0.0,
        min_emotion_and_payoff=0.0, max_fact_error=1.0,
        max_cost_minor_per_chapter=0,
    )
    raters: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        # 与 fingerprint_of 同源：重算与冻结必须给出同一个值，否则漂移检测形同虚设。
        return fingerprint_of(self._core_payload())

    def _core_payload(self) -> dict[str, Any]:
        """参与指纹计算的字段。指纹本身不在其中——否则会自引用成环。"""
        return {
            "eval_id": self.eval_id,
            "rubric_version": self.rubric_version,
            "arms": list(self.arms),
            "sample": self.sample.as_payload(),
            "budget": self.budget.as_payload(),
            "thresholds": self.thresholds.as_payload(),
            "raters": list(self.raters),
        }

    def as_payload(self) -> dict[str, Any]:
        return {**self._core_payload(), "fingerprint": self.fingerprint}


def fingerprint_of(payload: dict[str, Any]) -> str:
    """按存储的配置重算指纹。

    不能信任库里那份 ``config_fingerprint``——它就是被指望用来发现篡改的那个值；
    用被篡改过的副本来比对自身等于没有校验。
    """
    if not isinstance(payload, dict):
        return ""
    return digest(
        {
            "eval_id": payload.get("eval_id", ""),
            "rubric_version": payload.get("rubric_version", RUBRIC_VERSION),
            "arms": list(payload.get("arms") or ()),
            "sample": payload.get("sample") or {},
            "budget": payload.get("budget") or {},
            "thresholds": payload.get("thresholds") or {},
            "raters": list(payload.get("raters") or ()),
        }
    )


@dataclass(frozen=True)
class BlindSample:
    """盲评样本：``arms_in_order`` 是评者看到的顺序，不含 arm 身份。

    ``content_refs`` 是每个位置对应的正文引用（章节内容哈希/指针）。它是**私有**
    的：评者不能知道哪个位置是哪一版，但事后必须能查证「评的到底是哪份正文」。
    """

    sample_id: str
    level: str = "scene"
    calm: bool = False
    solo: bool = False
    arms_in_order: tuple[str, ...] = ()
    content_refs: tuple[str, ...] = ()

    @property
    def blind_key(self) -> str:
        return digest({"sample_id": self.sample_id, "order": list(self.arms_in_order)})

    @property
    def blind_map(self) -> dict[str, str]:
        """位置 → arm 的私有映射。它只对负责复盘的人可见，不对评者可见。"""
        return {
            chr(ord("A") + index): arm
            for index, arm in enumerate(self.arms_in_order)
        }

    def as_payload(self) -> dict[str, Any]:
        """评者可见载荷：只有 A/B 位置，没有 arm 标签、没有正文引用、没有导演自评。"""
        return {
            "sample_id": self.sample_id,
            "level": self.level,
            "options": [chr(ord("A") + i) for i in range(len(self.arms_in_order))],
        }

    def as_record(self) -> dict[str, Any]:
        """落库存档：映射与正文引用必须留住，否则样本无法复盘，也无法防替换。"""
        return {
            "sample_id": self.sample_id,
            "level": self.level,
            "calm": self.calm,
            "solo": self.solo,
            "arms_in_order": list(self.arms_in_order),
            "content_refs": list(self.content_refs),
            "blind_key": self.blind_key,
        }

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> BlindSample | None:
        if not isinstance(payload, dict) or not payload.get("sample_id"):
            return None
        order = payload.get("arms_in_order")
        refs = payload.get("content_refs")
        return cls(
            sample_id=str(payload["sample_id"]),
            level=str(payload.get("level") or "scene"),
            calm=bool(payload.get("calm", False)),
            solo=bool(payload.get("solo", False)),
            arms_in_order=tuple(str(a) for a in (order or ())),
            content_refs=tuple(str(r) for r in (refs or ())),
        )


def arm_at_position(sample: BlindSample | None, position: str) -> str | None:
    """把评者看到的 A/B 位置还原成 arm。映射不在评者手里，只能由评估方还原。"""
    if sample is None:
        return None
    token = str(position or "").strip().upper()
    if len(token) != 1 or not ("A" <= token <= "Z"):
        return None
    return sample.blind_map.get(token)


def shuffle_arms(sample_id: str, arms: Sequence[str]) -> tuple[str, ...]:
    """确定性洗牌：同一个样本对任何评者都给出同样的 A/B 顺序。

    顺序由 ``sample_id`` 决定，因此评者之间可比；arm 身份不进载荷，因此评者
    看不见自己评的是哪一版。
    """
    seed = int(digest({"sample_id": sample_id, "arms": list(arms)})[:8], 16)
    ordered = list(arms)
    for i in range(len(ordered) - 1, 0, -1):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        j = seed % (i + 1)
        ordered[i], ordered[j] = ordered[j], ordered[i]
    return tuple(ordered)


def make_sample(
    sample_id: str, arms: Sequence[str], *, level: str = "scene",
    calm: bool = False, solo: bool = False,
    content_refs: Sequence[str] = (),
) -> BlindSample:
    return BlindSample(
        sample_id=sample_id, level=level, calm=calm, solo=solo,
        arms_in_order=shuffle_arms(sample_id, arms),
        content_refs=tuple(str(ref) for ref in content_refs),
    )


@dataclass(frozen=True)
class PositionalScore:
    """评者按 A/B 位置给出的评分：位置到 arm 的还原不在评者手里。"""

    sample_id: str
    position: str
    scores: dict[str, float] = field(default_factory=dict)
    preferred: bool = False


@dataclass(frozen=True)
class BlindScore:
    """一位评者对一份样本一个 arm 的打分。``rater`` 只用于去重与统计。"""

    sample_id: str
    arm: str
    rater: str
    scores: dict[str, float] = field(default_factory=dict)
    preferred: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "arm": self.arm,
            "rater": self.rater,
            "scores": dict(self.scores),
            "preferred": self.preferred,
        }


@dataclass(frozen=True)
class ArmReport:
    arm: str
    n: int = 0                 # **独立样本数**：多少份不同样本被评到
    ratings: int = 0           # 评分条数，用于发现「一样本多评者」造成的虚高
    means: dict[str, float] = field(default_factory=dict)
    cost_minor_per_chapter: int = 0
    latency_ms_p95: int = 0
    replay_gain: float = 0.0
    # 场景级成本与时延：同预算带是按**场景**冻结的，只有章级数字就无法核对
    # 「这次评估有没有超预算」（B-03）。
    cost_minor_per_scene: int = 0
    latency_ms_per_scene_p95: int = 0
    run_refs: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n": self.n,
            "ratings": self.ratings,
            "means": dict(self.means),
            "cost_minor_per_chapter": self.cost_minor_per_chapter,
            "latency_ms_p95": self.latency_ms_p95,
            "replay_gain": self.replay_gain,
            "cost_minor_per_scene": self.cost_minor_per_scene,
            "latency_ms_per_scene_p95": self.latency_ms_per_scene_p95,
            "run_refs": list(self.run_refs),
        }


def summarise(scores: Iterable[BlindScore], arm: str) -> ArmReport:
    """按**独立样本**汇总，而不是按评分行数。

    同一份样本由三位评者打分仍是「一个样本」；按行数计会把「多找几个人评同一段」
    当成「评测做得多」。先算每份样本内各评者均分，再对样本求均——避免单人多票
    的样本在均分里压过它应有的权重。
    """
    rows = [s for s in scores if s.arm == arm]
    by_sample: dict[str, list[BlindScore]] = {}
    for score in rows:
        by_sample.setdefault(str(score.sample_id), []).append(score)
    means: dict[str, float] = {}
    for criterion in CRITERIA:
        per_sample: list[float] = []
        for sample_scores in by_sample.values():
            values = [
                float(s.scores.get(criterion))
                for s in sample_scores
                if criterion in s.scores
            ]
            if values:
                per_sample.append(sum(values) / len(values))
        if per_sample:
            means[criterion] = round(sum(per_sample) / len(per_sample), 4)
    return ArmReport(arm=arm, n=len(by_sample), ratings=len(rows), means=means)


@dataclass(frozen=True)
class Evidence:
    """晋级所需的**证据齐备性**。缺一项就不得宣布胜出（Plan §4 R4 / A-06）。

    这不是分数，是「有没有东西可以为结论作证」：模型是不是同一个、钱花了多少、
    延迟多少、注册样本够不够、舒缓/单人场景占比够不够、受评正文有没有留下引用。
    缺证据时结论只能是 HOLD——不是 REJECT，因为「不知道」不等于「更差」。
    """

    model: str = ""
    cost_provided: tuple[str, ...] = ()
    latency_provided: tuple[str, ...] = ()
    registered_samples: int = 0
    raters_registered: int = 0
    calm_ratio: float = 0.0
    solo_ratio: float = 0.0
    content_refs_complete: bool = False
    # 每臂绑定的真实运行：没有它，「这些分数是哪次跑出来的」无法回答。
    runs_bound: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "cost_provided": list(self.cost_provided),
            "latency_provided": list(self.latency_provided),
            "registered_samples": self.registered_samples,
            "raters_registered": self.raters_registered,
            "calm_ratio": self.calm_ratio,
            "solo_ratio": self.solo_ratio,
            "content_refs_complete": self.content_refs_complete,
            "runs_bound": list(self.runs_bound),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> Evidence:
        data = payload or {}
        return cls(
            model=str(data.get("model") or ""),
            cost_provided=tuple(str(a) for a in (data.get("cost_provided") or ())),
            latency_provided=tuple(str(a) for a in (data.get("latency_provided") or ())),
            registered_samples=int(data.get("registered_samples") or 0),
            raters_registered=int(data.get("raters_registered") or 0),
            calm_ratio=float(data.get("calm_ratio") or 0.0),
            solo_ratio=float(data.get("solo_ratio") or 0.0),
            content_refs_complete=bool(data.get("content_refs_complete", False)),
            runs_bound=tuple(str(a) for a in (data.get("runs_bound") or ())),
        )


def missing_evidence(
    evidence: Evidence | None,
    *,
    arms: Sequence[str],
    budget: BudgetBand | None = None,
    sample: SampleSpec | None = None,
) -> tuple[str, ...]:
    """列出缺失的必需证据。返回空元组表示证据齐备。"""
    if evidence is None:
        return ("本次评估没有附带任何证据：不得据其内容裁决",)
    reasons: list[str] = []
    required_model = str((budget.model if budget else "") or "")
    if not evidence.model or not required_model or evidence.model != required_model:
        reasons.append(
            f"缺少同预算带的模型证据：记录模型 {evidence.model or '未记录'}，"
            f"冻结要求 {required_model or '未配置'}"
        )
    for arm in arms:
        if arm not in evidence.cost_provided:
            reasons.append(f"缺少 {arm} 的单章成本证据")
        if arm not in evidence.latency_provided:
            reasons.append(f"缺少 {arm} 的延迟证据")
    if not evidence.content_refs_complete:
        reasons.append("缺少受评正文引用：无法证明评的是哪一版内容")
    if sample is not None:
        if evidence.registered_samples < int(sample.total):
            reasons.append(
                f"注册样本不足：{evidence.registered_samples}/{int(sample.total)}"
            )
        if evidence.registered_samples:
            if evidence.calm_ratio + 1e-9 < float(sample.calm_scene_ratio):
                reasons.append(
                    f"舒缓场景占比不足：{evidence.calm_ratio:.2f} < "
                    f"{sample.calm_scene_ratio}"
                )
            if evidence.solo_ratio + 1e-9 < float(sample.solo_scene_ratio):
                reasons.append(
                    f"单人场景占比不足：{evidence.solo_ratio:.2f} < "
                    f"{sample.solo_scene_ratio}"
                )
    for arm in arms:
        if arm not in evidence.runs_bound:
            reasons.append(f"缺少 {arm} 的真实运行绑定：无法证明分数来自哪次运行")
    if evidence.raters_registered <= 0:
        reasons.append("没有预注册评者：评分来源不可核查")
    return tuple(reasons)


def budget_violations(
    challenger: ArmReport,
    baseline: ArmReport,
    budget: BudgetBand | None,
) -> tuple[str, ...]:
    """同预算带核对：超限的两臂不能互相比较（B-03）。

    只查 ``PromotionThresholds.max_cost_minor_per_chapter`` 是不够的——那是**门槛**
    不是**预算带**：门槛说「最多能花多少」，预算带说「这次实验必须花这么多」。
    挑战方花 100、基线花 10，两者都在门槛内，但比的已经不是策略而是钱包。

    上限为 0 表示该维度未冻结，不约束。
    """
    if budget is None:
        return ()
    reasons: list[str] = []
    for report in (challenger, baseline):
        if budget.max_cost_minor_per_chapter and (
            report.cost_minor_per_chapter > budget.max_cost_minor_per_chapter
        ):
            reasons.append(
                f"{report.arm} 单章成本 {report.cost_minor_per_chapter} 超出同预算带 "
                f"上限 {budget.max_cost_minor_per_chapter}：本次比较不成立"
            )
        if budget.max_cost_minor_per_scene and (
            report.cost_minor_per_scene > budget.max_cost_minor_per_scene
        ):
            reasons.append(
                f"{report.arm} 单场景成本 {report.cost_minor_per_scene} 超出同预算带 "
                f"上限 {budget.max_cost_minor_per_scene}：本次比较不成立"
            )
        if budget.max_latency_ms_per_scene and (
            report.latency_ms_per_scene_p95 > budget.max_latency_ms_per_scene
        ):
            reasons.append(
                f"{report.arm} 单场景时延 P95 {report.latency_ms_per_scene_p95}ms 超出"
                f"同预算带上限 {budget.max_latency_ms_per_scene}ms：本次比较不成立"
            )
    return tuple(reasons)


def report_fingerprint_of(report: dict[str, Any], *, config_fingerprint: str) -> str:
    """报告指纹：覆盖裁决要看的所有内容，不含自身。

    报告内容变了而指纹没跟着变，说明有人改了报告——与配置漂移同一个道理。
    """
    return digest(
        {
            "config": config_fingerprint,
            "eval_id": (report or {}).get("eval_id", ""),
            "arms": (report or {}).get("arms") or [],
            "samples": (report or {}).get("samples", 0),
            "evidence": (report or {}).get("evidence") or {},
            "ratings": (report or {}).get("ratings", 0),
        }
    )


def verdict(
    challenger: ArmReport,
    baseline: ArmReport,
    *,
    thresholds: PromotionThresholds,
    config_fingerprint: str,
    report_fingerprint: str,
    evidence: Evidence | None = None,
    budget: BudgetBand | None = None,
    sample: SampleSpec | None = None,
    arms: Sequence[str] = ("v1", "v2"),
) -> tuple[Verdict, tuple[str, ...]]:
    """晋级裁决：先过不可绕过的前置，再看分数。

    顺序即优先级，且**不能调换**：
    ① 配置漂移（比什么都重要——配置被改过，后面的比较就没有参照系）；
    ② 证据缺失（缺证据连「样本够不够」都无法判断，更不该谈胜出）；
    ③ **同预算带**（花了不同的钱，比的就不是策略）；
    ④ 样本不足；⑤ 分项门槛与成本门槛；⑥ 是否真的超过基线。
    """
    if config_fingerprint != report_fingerprint:
        return "HOLD", ("配置指纹与报告不符：评估配置在采样后被改过",)
    missing = missing_evidence(evidence, arms=arms, budget=budget, sample=sample)
    if missing:
        return "HOLD", missing
    over_budget = budget_violations(challenger, baseline, budget)
    if over_budget:
        return "HOLD", over_budget
    reasons: list[str] = []
    if challenger.n < thresholds.min_samples or baseline.n < thresholds.min_samples:
        return "HOLD", (
            f"样本不足：挑战方 {challenger.n} / 基线 {baseline.n}，"
            f"门槛 {thresholds.min_samples}",
        )
    for criterion, minimum in (
        ("read_willingness", thresholds.min_read_willingness),
        ("character_credibility", thresholds.min_character_credibility),
        ("emotion_and_payoff", thresholds.min_emotion_and_payoff),
    ):
        value = challenger.means.get(criterion)
        if value is None:
            return "HOLD", (f"缺少 {criterion} 的人评数据",)
        if value < minimum:
            reasons.append(f"{criterion}={value} 低于门槛 {minimum}")
    fact_error = challenger.means.get("fact_error")
    if fact_error is None:
        return "HOLD", ("缺少 fact_error 的事实核查数据",)
    if fact_error > thresholds.max_fact_error:
        reasons.append(f"fact_error={fact_error} 高于上限 {thresholds.max_fact_error}")
    if thresholds.max_cost_minor_per_chapter and (
        challenger.cost_minor_per_chapter > thresholds.max_cost_minor_per_chapter
    ):
        reasons.append(
            f"单章成本 {challenger.cost_minor_per_chapter} 超上限 "
            f"{thresholds.max_cost_minor_per_chapter}"
        )
    if reasons:
        return "REJECT", tuple(reasons)
    if challenger.means.get("read_willingness", 0) <= baseline.means.get(
        "read_willingness", 0
    ):
        return "HOLD", ("未超过基线的阅读意愿，不构成晋级理由",)
    return "PROMOTE", ()


# ---------------------------------------------------------------------------
# 灰度
# ---------------------------------------------------------------------------

_IN_FLIGHT_STATES = frozenset({"QUEUED", "RUNNING", "PENDING_DECISION", "AWAITING_INPUT"})


def canary_bucket(work_id: str, *, salt: str = "novel-executor") -> int:
    """确定性分桶：同一作品永远落在同一桶，灰度期间不会来回横跳。"""
    return int(digest({"work_id": str(work_id), "salt": salt})[:8], 16) % 100


def assign_executor(
    work_id: str,
    *,
    canary_percent: int,
    stable_executor: str,
    canary_executor: str,
) -> str:
    """按作品分桶选择执行器。百分比为 0 时永远走稳定版。"""
    if canary_percent <= 0:
        return stable_executor
    if canary_percent >= 100:
        return canary_executor
    return canary_executor if canary_bucket(work_id) < canary_percent else stable_executor


def switch_executor_allowed(state: str, *, target: str, current: str) -> bool:
    """运行中的任务不得切换执行器：切换会让在途产出来源不明。"""
    if target == current:
        return True
    return state not in _IN_FLIGHT_STATES
