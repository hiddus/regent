"""Novel Engine 领域对象与传输契约（Tech-Spec §3.1 / §8）。

规则：
- 核心响应禁止 ``Record[str, Any]`` 和任意 string 状态（Tech-Spec §8）。
  所有枚举字段使用 ``str, Enum``，未收录值直接 422，不静默降级。
- 用户可见字段不出现 Agent / model / token / Canon / Scenario 等内部术语（Plan M2 出口）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .money import DEFAULT_CURRENCY
from .states import (
    ChapterRunState,
    ChapterStep,
    DecisionState,
    ModerationDecision,
    StepState,
)

# ---------------------------------------------------------------------------
# 枚举：对外契约（与 states.Enum 同值，独立定义以便演进展示文案）
# ---------------------------------------------------------------------------


class WorkStateOut(StrEnum):
    ONBOARDING = "ONBOARDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PENDING_DECISION = "PENDING_DECISION"
    PAUSED_QUOTA = "PAUSED_QUOTA"
    PAUSED_COST = "PAUSED_COST"
    RECOMPUTING = "RECOMPUTING"
    FAILED = "FAILED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class PathNodeType(StrEnum):
    INCITING = "INCITING"
    REVERSAL = "REVERSAL"
    ESCALATION = "ESCALATION"
    REVELATION = "REVELATION"
    BETRAYAL = "BETRAYAL"
    DEATH = "DEATH"
    WAR = "WAR"
    CLIMAX = "CLIMAX"
    RESOLUTION = "RESOLUTION"
    CUSTOM = "CUSTOM"


class FundingSource(StrEnum):
    PLATFORM_GRANT = "platform_grant"
    USER_PAID = "user_paid"
    ONBOARDING = "onboarding"


# 需要人工裁决的节点类型：死亡/背叛/身份揭露/开战/主线改变（PRD §4 第 8 条）
HUMAN_REQUIRED_NODE_TYPES: frozenset[PathNodeType] = frozenset(
    {
        PathNodeType.DEATH,
        PathNodeType.BETRAYAL,
        PathNodeType.REVELATION,
        PathNodeType.WAR,
    }
)

# ---------------------------------------------------------------------------
# StoryGoal / CriticalPath
# ---------------------------------------------------------------------------


class StoryGoalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_intent: str
    normalized_goal: str = ""
    assumptions: list[str] = Field(default_factory=list)
    locked_at: datetime | None = None
    version: int = 0


class CriticalNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    ordinal: int
    title: str
    node_type: PathNodeType = PathNodeType.CUSTOM
    promise: str = ""
    preconditions: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    requires_human: bool = False
    locked: bool = False
    # 30 万字架构：节点归属卷/弧段
    volume_no: int | None = None
    arc_no: int | None = None


class ArcNodeOut(BaseModel):
    """弧段输出（30 万字架构）。"""

    model_config = ConfigDict(extra="forbid")

    arc_no: int
    title: str
    arc_type: str = "STANDARD"
    chapter_range_start: int = 0
    chapter_range_end: int = 0
    core_conflict: str = ""
    resolution_type: str = ""


class VolumeOut(BaseModel):
    """卷输出（30 万字架构）。"""

    model_config = ConfigDict(extra="forbid")

    volume_no: int
    title: str
    cultivation_realm: str = ""
    start_chapter_no: int = 0
    end_chapter_no: int = 0
    state: str = "PENDING"
    summary: list[dict[str, Any]] = Field(default_factory=list)
    arcs: list[ArcNodeOut] = Field(default_factory=list)


class CriticalPathOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[CriticalNode] = Field(default_factory=list)
    dependency_edges: list[dict[str, str]] = Field(default_factory=list)
    frozen_through_chapter: int = 0
    version: int = 1


class CriticalPathUpdate(BaseModel):
    """关键路径更新请求（FR-04 / FR-05）。

    必带 ``expected_version``——乐观并发，冲突返回 409 + current_version。
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[CriticalNode]
    dependency_edges: list[dict[str, str]] = Field(default_factory=list)
    expected_version: int
    change_note: str = ""


class PathChangeImpact(BaseModel):
    """路径修改的影响预览（FR-05）。"""

    model_config = ConfigDict(extra="forbid")

    affected_chapters: list[int] = Field(default_factory=list)
    frozen_conflict: bool = False
    frozen_through_chapter: int = 0
    eta_minutes_min: int | None = None
    eta_minutes_max: int | None = None
    cost_ceiling_minor: int | None = None
    currency: str = DEFAULT_CURRENCY


# ---------------------------------------------------------------------------
# Onboarding / 方向卡
# ---------------------------------------------------------------------------


class DirectionCard(BaseModel):
    """2–3 张方向卡，必须可复述差异（FR-03 / NFR 方向卡可复述率 ≥80%）。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    title: str
    protagonist_desire: str
    core_conflict: str
    genre_promise: str
    pacing: str
    differentiator: str


class ClarifyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    default_assumption: str = ""


class CreateWorkRequest(BaseModel):
    """FR-01：首次提交必填仅目标文本。"""

    model_config = ConfigDict(extra="forbid")

    raw_intent: str = Field(min_length=1, max_length=4000)
    title: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=64)
    client_nonce: str = Field(default="", max_length=128)


class OnboardingOut(BaseModel):
    """FR-02：最多 1 轮澄清，每轮最多 3 问；信息不足时写入 assumptions 后继续（G-21）。"""

    model_config = ConfigDict(extra="forbid")

    status: str  # CLARIFYING | READY
    clarify_round: int = 0
    question_count: int = 0
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    directions: list[DirectionCard] = Field(default_factory=list)


class CreateWorkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    state: WorkStateOut
    onboarding: OnboardingOut | None = None


class AnswerClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, str] = Field(default_factory=dict)
    accept_defaults: bool = False


class ConfirmDirectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    client_nonce: str = ""


# ---------------------------------------------------------------------------
# UXProjection（Tech-Spec §10.2）
# ---------------------------------------------------------------------------


class UXProjection(BaseModel):
    """后端投影的用户态。前端不得自行从内部 step 猜用户态。"""

    model_config = ConfigDict(extra="forbid")

    public_stage: str
    stage_label: str
    last_completed_artifact: str | None = None
    next_milestone: str | None = None
    eta_range: dict[str, Any] | None = None  # {min_minutes, max_minutes}
    safe_to_leave: bool = True
    stale_at: datetime | None = None
    action_required: bool = False
    available_actions: list[str] = Field(default_factory=list)
    # 未知状态映射为 unknown_recoverable，不得猜成成功或失败
    unknown_recoverable: bool = False


class WorkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    title: str
    genre: str
    state: WorkStateOut
    chapter_count: int = 0
    latest_chapter_no: int | None = None
    pending_decisions: int = 0
    projection: UXProjection | None = None
    updated_at: datetime | None = None


class WorkDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    title: str
    genre: str
    state: WorkStateOut
    version: int
    goal: StoryGoalOut | None = None
    critical_path: CriticalPathOut | None = None
    projection: UXProjection | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# 裁决（FR-10 / PRD §4 第 9 条）
# ---------------------------------------------------------------------------


class DecisionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str
    label: str
    near_term_consequence: str
    reversibility: str  # REVERSIBLE | COSTLY | IRREVERSIBLE


class DecisionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    work_id: str
    chapter_no: int | None = None
    state: DecisionState
    trigger_summary: str
    why_human: str
    options: list[DecisionOption] = Field(default_factory=list)
    default_option_id: str | None = None
    deadline: datetime | None = None
    impact_level: str = "MEDIUM"  # LOW | MEDIUM | HIGH
    impact_horizon_chapters: int = 1
    confirm_nonce: str = ""
    version: int = 1


class ResolveDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str | None = None
    accept_default: bool = False
    confirm_nonce: str
    client_nonce: str = ""
    rationale: str = ""


# ---------------------------------------------------------------------------
# 章节 / 运行
# ---------------------------------------------------------------------------


class ChapterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    chapter_no: int
    title: str
    state: ChapterRunState
    content: str = ""
    word_count: int = 0
    ai_disclosure: str = "本文内容由 AI 参与生成"
    version: int = 1


class RunProgressOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    chapter_no: int
    state: ChapterRunState
    current_step: ChapterStep | None = None
    steps: dict[str, StepState] = Field(default_factory=dict)
    reused_calls: int = 0
    # 恢复复用的调用所避免的重复花费（最小货币单位）
    avoided_cost_minor: int = 0
    version: int = 1
    # 人在回路
    auto_advance: bool = False
    awaiting_input: bool = False
    scene_no: int = 0
    scene_count: int = 0
    completed_scenes: int = 0


# ---------------------------------------------------------------------------
# 人在回路检查点
# ---------------------------------------------------------------------------


class GuidanceRequest(BaseModel):
    """用户在检查点提交反馈。"""

    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(default="", max_length=5000)
    approve: bool = False  # True = 无反馈直接继续


class AutoAdvanceRequest(BaseModel):
    """切换自动/手动模式。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


# ---------------------------------------------------------------------------
# 事实报错 / 审核 / 分享 / 导出
# ---------------------------------------------------------------------------


class EndingIntentRequest(BaseModel):
    """用户认可的终局（B-05）。

    两项都可空：都不填表示「我还没想好，交给导演判断」，此时完结只能由导演
    判定，且判不出来就保持待定——不允许退化成「扩卷失败即完结」。
    """

    model_config = ConfigDict(extra="forbid")

    target_volume_count: int | None = Field(default=None, ge=1, le=50)
    ending_statement: str | None = Field(default=None, max_length=500)


class ResumeCorrectionRequest(BaseModel):
    """因纠错恢复创作（C-01）。

    ``ticket_id`` 是 ``report_fact`` 返回的工单号，用于把「这次恢复」和「那次
    报错」串起来——恢复是用户动作，必须能追到它对应哪一条纠错。
    """

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(default="", max_length=64)


class ReportFactRequest(BaseModel):
    """FR-11：事实错误可纠正；审美意见必须给出可行动回落路径。"""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2000)
    chapter_no: int | None = None
    kind: str = "FACT"  # FACT | TASTE
    client_nonce: str = ""
    # 报错针对的记忆主题（人物名、节点标题）。不填时从 statement 里按在册人物推。
    # 它决定重演范围：认不出主题就只能保守重做，不能假装精确。
    subject: str = ""


class ReportFactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    ticket_id: str
    kind: str
    message: str
    affected_chapters: list[int] = Field(default_factory=list)
    # 重演范围是怎么定下来的：dependency_subgraph 还是 conservative_batch。
    # 承诺「局部重演」却整批重做，是必须能让用户看见的事（B-02）。
    replay_scope: str = ""
    # 审美意见不得只显示拒绝——必须给出回落动作（PRD §3.1）
    available_actions: list[str] = Field(default_factory=list)


class ModerationCaseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    work_id: str
    chapter_no: int | None = None
    target_type: str = "CHAPTER"
    decision: ModerationDecision
    reason_code: str | None = None
    detail: str = ""
    appealed_at: datetime | None = None
    resolved_at: datetime | None = None


class ReportModerationRequest(BaseModel):
    """FR-25：投诉/举报入口。任何投诉都必须落 ModerationCase（G-23）。"""

    model_config = ConfigDict(extra="forbid")

    chapter_no: int | None = None
    reason_code: str = "other"
    detail: str = Field(default="", max_length=2000)
    client_nonce: str = ""


class ResolveModerationRequest(BaseModel):
    """P1-4：审核结论。无结论不得视为通过（G-23）。"""

    model_config = ConfigDict(extra="forbid")

    decision: ModerationDecision
    reason_code: str = ""
    evidence: str = Field(default="", max_length=2000)
    # author 不能给自己的案件下“通过”结论；平台审核用 moderator
    actor: Literal["moderator", "author"] = "moderator"
    client_nonce: str = ""


class ResolveAppealRequest(BaseModel):
    """P1-4：申诉结论。upheld=True 表示维持原判定。"""

    model_config = ConfigDict(extra="forbid")

    upheld: bool = False
    reason_code: str = ""
    evidence: str = Field(default="", max_length=2000)
    actor: Literal["moderator", "author"] = "moderator"
    client_nonce: str = ""


class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitee_label: str = Field(default="", max_length=120)
    scope: str = "FULL"  # FULL | CHAPTER_RANGE
    from_chapter: int | None = None
    to_chapter: int | None = None
    expires_in_hours: int = Field(default=168, ge=1, le=24 * 90)


class ShareOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share_id: str
    work_id: str
    share_url: str
    scope: str
    noindex: bool = True
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


class ExportNoticeOut(BaseModel):
    """G-22：告知状态与条款版本。"""

    model_config = ConfigDict(extra="forbid")

    notice_version: str
    satisfied_at: datetime | None = None
    required: bool = True
    title: str
    body: str


class AcknowledgeExportNoticeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notice_version: str


class ExportRequest(BaseModel):
    """G-15：参数白名单。未知 format 直接 422。"""

    model_config = ConfigDict(extra="forbid")

    format: str = Field(pattern="^(txt|md|docx|pdf)$")
    include_chapters: list[int] | None = None


class ExportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str
    work_id: str
    format: str
    byte_size: int
    content_sha256: str
    ai_disclosure: str
    download_url: str


# ---------------------------------------------------------------------------
# 事件（Tech-Spec §9）
# ---------------------------------------------------------------------------

EVENT_SCHEMA_VERSION = 1


class NovelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int
    schema_version: int = EVENT_SCHEMA_VERSION
    type: str
    occurred_at: datetime
    work_id: str
    branch_id: str | None = None
    chapter_no: int | None = None
    decision_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[NovelEvent] = Field(default_factory=list)
    last_sequence: int = 0
    has_more: bool = False
    resync_required: bool = False


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "HUMAN_REQUIRED_NODE_TYPES",
    "AnswerClarifyRequest",
    "AutoAdvanceRequest",
    "ChapterOut",
    "ClarifyQuestion",
    "ConfirmDirectionRequest",
    "CreateShareRequest",
    "CreateWorkRequest",
    "CreateWorkResponse",
    "CriticalNode",
    "CriticalPathOut",
    "CriticalPathUpdate",
    "DecisionOption",
    "DecisionView",
    "DirectionCard",
    "EventPage",
    "ExportNoticeOut",
    "ExportOut",
    "ExportRequest",
    "FundingSource",
    "GuidanceRequest",
    "ModerationCaseOut",
    "NovelEvent",
    "OnboardingOut",
    "PathChangeImpact",
    "PathNodeType",
    "ReportFactRequest",
    "ReportFactResponse",
    "ResolveDecisionRequest",
    "RunProgressOut",
    "ShareOut",
    "StoryGoalOut",
    "UXProjection",
    "WorkDetail",
    "WorkStateOut",
]
