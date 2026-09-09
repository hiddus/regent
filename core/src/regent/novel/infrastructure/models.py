"""Novel Engine 持久化模型（Tech-Spec §3 / §6 / §9）。

约束：
- 金额一律 ``amount_minor BIGINT + currency CHAR(3)``，**不出现 Float**（G-10）。
- Canon、账本、事件、告知日志为 append-only（G-07）。
- 所有写路径带 ``version`` 乐观锁，冲突返回 409 不静默覆盖。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class NovelBase(DeclarativeBase):
    """Novel 独立 DeclarativeBase：避免与 Core 的元数据互相污染。"""


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


_WORK_STATES = (
    "ONBOARDING",
    "READY",
    "RUNNING",
    "PENDING_DECISION",
    "PAUSED_QUOTA",
    "PAUSED_COST",
    "RECOMPUTING",
    "FAILED",
    "DONE",
    "CANCELLED",
    "ARCHIVED",
)
_CHAPTER_RUN_STATES = (
    "QUEUED",
    "RUNNING",
    "PENDING_DECISION",
    "AWAITING_INPUT",
    "RETRYABLE_FAILED",
    "TERMINAL_FAILED",
    "CANONIZED",
    "SUPERSEDED",
    "CANCELLED",
)
_STEP_STATES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED")
_DECISION_STATES = ("PENDING", "RESOLVED", "EXPIRED", "SUPERSEDED")
_MODERATION_DECISIONS = ("PENDING", "APPROVED", "REJECTED", "APPEALED", "RESOLVED")
_FUNDING_SOURCES = ("platform_grant", "user_paid", "onboarding")
# 与 domain.memory 的 MEMORY_KINDS / MEMORY_STATES 保持一致：这里只用于 CHECK 约束
# 六个视图（技术方案 §3.2）：世界事实=rule；人物状态=character_arc+relation；
# 人物认知=belief；读者认知=reader_knowledge；承诺与伏笔=promise；导演记忆=director_note。
_MEMORY_KINDS = (
    "rule",
    "character_arc",
    "promise",
    "relation",
    "belief",
    "reader_knowledge",
    "director_note",
)
_MEMORY_STATES = ("OPEN", "RESOLVED", "ABANDONED")
_MEMORY_EDGE_KINDS = ("depends", "independent")


def _states_sql(name: str, values: tuple[str, ...]) -> str:
    body = ",".join(f"'{v}'" for v in values)
    return f"{name} IN ({body})"


# ---------------------------------------------------------------------------
# 身份（G-11：服务端解析 principal，不采信客户端 actor）
# ---------------------------------------------------------------------------


class NovelPrincipalModel(Timestamped, NovelBase):
    __tablename__ = "novel_principals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # 登录标识（手机号/邮箱/第三方 openid），唯一
    subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # 软删除：财务、授权和创作证据按保留策略归档，不级联物理删除（Tech-Spec §7）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NovelSessionModel(NovelBase):
    __tablename__ = "novel_sessions"
    __table_args__ = (
        Index("ix_novel_sessions_principal_expires", "principal_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # 存 hash，不存明文 token
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_principals.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(512), default="", nullable=False)


# ---------------------------------------------------------------------------
# 作品 / 目标 / 路径
# ---------------------------------------------------------------------------


class StoryWorkModel(Timestamped, NovelBase):
    __tablename__ = "novel_works"
    __table_args__ = (
        CheckConstraint(_states_sql("state", _WORK_STATES), name="ck_novel_works_state"),
        Index("ix_novel_works_owner_state", "owner_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_principals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    genre: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ONBOARDING")
    public_state: Mapped[str] = mapped_column(String(16), nullable=False, default="PRIVATE")
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_volume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 用户认可的终局（B-05）：末节点完成后按它决定结束还是扩卷，不再「先扩卷、
    # 扩不出来才结束」。0 / 空串表示用户没说，只能由导演补位。
    ending_target_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ending_statement: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 产品软删除；财务与创作证据不级联物理删除
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryGoalModel(Timestamped, NovelBase):
    __tablename__ = "novel_goals"
    __table_args__ = (UniqueConstraint("work_id", "version", name="uq_novel_goals_work_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # raw_intent 不可变：是三动作留痕的第一项，也是可版权性举证材料
    raw_intent: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CriticalPathModel(Timestamped, NovelBase):
    __tablename__ = "novel_critical_paths"
    __table_args__ = (
        UniqueConstraint("work_id", "version", name="uq_novel_paths_work_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    frozen_through_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 10–20 节点（FR-04）
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dependency_edges: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, default=list, nullable=False
    )


class CriticalNodeModel(NovelBase):
    __tablename__ = "novel_critical_nodes"
    __table_args__ = (
        UniqueConstraint("path_id", "ordinal", name="uq_novel_nodes_path_ordinal"),
        Index("ix_novel_nodes_path", "path_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    path_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_critical_paths.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, default="CUSTOM")
    promise: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preconditions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    consequences: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    requires_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 30 万字架构：节点归属卷/弧段标注
    volume_no: Mapped[int] = mapped_column(Integer, nullable=True)
    arc_no: Mapped[int] = mapped_column(Integer, nullable=True)


class VolumeModel(Timestamped, NovelBase):
    """卷：小说最高层结构单元（30 万字架构）。"""

    __tablename__ = "novel_volumes"
    __table_args__ = (
        UniqueConstraint("work_id", "volume_no", name="uq_novel_volumes_work_vol"),
        Index("ix_novel_volumes_work", "work_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    summary: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, nullable=False)
    cultivation_realm: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    start_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")


class ArcNodeModel(NovelBase):
    """弧段：卷内叙事单元，每个弧段覆盖 3-8 章（30 万字架构）。"""

    __tablename__ = "novel_arc_nodes"
    __table_args__ = (
        UniqueConstraint("volume_id", "arc_no", name="uq_novel_arcs_vol_arc"),
        Index("ix_novel_arcs_volume", "volume_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    volume_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_volumes.id", ondelete="CASCADE"), nullable=False
    )
    arc_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    arc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="STANDARD")
    chapter_range_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_range_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    core_conflict: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolution_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)


class OnboardingSessionModel(Timestamped, NovelBase):
    """G-21：澄清轮次 ≤1、每轮问题数 ≤3；信息不足写 assumptions 后继续。"""

    __tablename__ = "novel_onboarding_sessions"
    __table_args__ = (
        CheckConstraint("clarify_round <= 1", name="ck_novel_onboarding_round"),
        CheckConstraint("question_count <= 3", name="ck_novel_onboarding_questions"),
        UniqueConstraint("work_id", name="uq_novel_onboarding_work"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_principals.id", ondelete="CASCADE"), nullable=False
    )
    clarify_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    directions: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected_card_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)


# ---------------------------------------------------------------------------
# 章执行（Tech-Spec §4）
# ---------------------------------------------------------------------------


class ChapterRunModel(Timestamped, NovelBase):
    __tablename__ = "novel_chapter_runs"
    __table_args__ = (
        CheckConstraint(_states_sql("state", _CHAPTER_RUN_STATES), name="ck_novel_runs_state"),
        UniqueConstraint(
            "work_id", "branch_id", "chapter_no", "attempt", name="uq_novel_runs_identity"
        ),
        Index("ix_novel_runs_work_chapter", "work_id", "chapter_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    current_step: Mapped[str] = mapped_column(String(24), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Agent loop 的可恢复检查点。角色表演按各自 InformationSet 独立生成，
    # 这里只保存结构化结果，进程重启后不会退回“一次性重新成稿”。
    generation_context: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    performances: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    review: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    # 人在回路：用户反馈与自动模式
    user_guidance: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    auto_advance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # 幂等键：work:branch:chapter:step:input_version（Tech-Spec §5）
    input_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    canonized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 运行租约：调用在事务外进行（§4.4），持有租约期间其他 worker 不得并行推进
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChapterStepModel(Timestamped, NovelBase):
    __tablename__ = "novel_chapter_steps"
    __table_args__ = (
        CheckConstraint(_states_sql("state", _STEP_STATES), name="ck_novel_steps_state"),
        UniqueConstraint(
            "run_id",
            "step",
            "input_version",
            name="uq_novel_steps_idempotency",  # 逻辑幂等键
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_chapter_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    input_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_ref: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)


# ---------------------------------------------------------------------------
# 角色 / 信息集 / Canon
# ---------------------------------------------------------------------------


class PersonaSpecModel(Timestamped, NovelBase):
    __tablename__ = "novel_personas"
    __table_args__ = (UniqueConstraint("work_id", "name", name="uq_novel_personas_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    identity: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    drives: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    voice: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    stable_traits: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class InformationSetModel(NovelBase):
    """G-03：每个角色只获得自己的 InformationSet。"""

    __tablename__ = "novel_information_sets"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "scene_id", "context_hash", name="uq_novel_infoset_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_personas.id", ondelete="CASCADE"), nullable=False
    )
    scene_id: Mapped[str] = mapped_column(String(64), nullable=False)
    grants: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    exclusions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CanonCommitModel(NovelBase):
    """G-06/G-07：模型不能直接写 Canon；append-only 版本链。"""

    __tablename__ = "novel_canon_commits"
    __table_args__ = (
        UniqueConstraint(
            "work_id",
            "branch_id",
            "chapter_no",
            "source_hash",
            name="uq_novel_canon_idempotency",
        ),
        Index("ix_novel_canon_work_version", "work_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    facts: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# 裁决 / 审核
# ---------------------------------------------------------------------------


class DecisionRequestModel(Timestamped, NovelBase):
    """G-13：裁决与默认 timer 竞争，仅一个结果成功。"""

    __tablename__ = "novel_decision_requests"
    __table_args__ = (
        CheckConstraint(_states_sql("state", _DECISION_STATES), name="ck_novel_decisions_state"),
        Index("ix_novel_decisions_work_state", "work_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("novel_chapter_runs.id", ondelete="SET NULL")
    )
    node_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    trigger_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    why_human: Mapped[str] = mapped_column(Text, default="", nullable=False)
    options: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    default_option_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    impact_level: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    impact_horizon_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirm_nonce: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # 竞争事务的 fencing：resolved_by 非空即代表已有人胜出
    resolved_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    resolved_option_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ModerationCaseModel(Timestamped, NovelBase):
    """G-23：审核/投诉/申诉必须落库；无结论不得视为通过。"""

    __tablename__ = "novel_moderation_cases"
    __table_args__ = (
        CheckConstraint(
            _states_sql("decision", _MODERATION_DECISIONS), name="ck_novel_moderation_decision"
        ),
        Index("ix_novel_moderation_work", "work_id", "chapter_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=True)
    target_type: Mapped[str] = mapped_column(String(32), default="CHAPTER", nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    reason_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    appealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    appeal_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 分享 / 导出 / 导出告知
# ---------------------------------------------------------------------------


class ShareModel(Timestamped, NovelBase):
    __tablename__ = "novel_shares"
    __table_args__ = (Index("ix_novel_shares_work", "work_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(24), nullable=False, default="FULL")
    from_chapter: Mapped[int | None] = mapped_column(Integer)
    to_chapter: Mapped[int | None] = mapped_column(Integer)
    # FR-17：定向分享必须不可被搜索引擎索引
    noindex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportNoticeModel(Timestamped, NovelBase):
    """G-22：拦截只查本表，不查日志。条款版本变更须重新告知。"""

    __tablename__ = "novel_export_notices"
    __table_args__ = (UniqueConstraint("user_id", "work_id", name="uq_novel_export_notice"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_principals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    notice_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportNoticeLogModel(NovelBase):
    """append-only 告知日志，仅用于举证；不参与拦截判定。"""

    __tablename__ = "novel_export_notice_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    work_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(32), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class ExportJobModel(Timestamped, NovelBase):
    """G-15：格式白名单；字节流不经过 LLM；始终带 AI 标识。"""

    __tablename__ = "novel_export_jobs"
    __table_args__ = (
        CheckConstraint(
            "format IN ('txt','md','docx','pdf')", name="ck_novel_exports_format"
        ),
        Index("ix_novel_exports_work", "work_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_principals.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    notice_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)


# ---------------------------------------------------------------------------
# 成本 / 额度 / 模型调用（Tech-Spec §6）
# ---------------------------------------------------------------------------


class ModelCallModel(NovelBase):
    """G-08：所有生成调用有 work/chapter/step/cost_scope/logical_call_id。"""

    __tablename__ = "novel_model_calls"
    __table_args__ = (
        # logical call 与 attempt 分离：重试是新的 attempt，不复用前一次的费用记录
        UniqueConstraint(
            "logical_call_id", "attempt", name="uq_novel_model_calls_logical_attempt"
        ),
        Index("ix_novel_model_calls_work", "work_id", "chapter_no"),
        Index("ix_novel_model_calls_logical", "logical_call_id"),
        Index("ix_novel_model_calls_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    logical_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    cost_scope: Mapped[str] = mapped_column(String(24), default="generation", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sampling: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="SUCCEEDED", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    price_book_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # --- 生产调用协议（Tech-Spec §4.4 / §5 / §6）---
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 调用前预留、调用后按实际结算；两段式，多退少补
    reserved_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    actual_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    # usage 来源：provider 报告 / estimated（对账失败后的估算）
    usage_source: Mapped[str] = mapped_column(String(32), default="provider", nullable=False)
    # 供应商请求 id：外部结果不确定时用于查询/对账
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    # 对账次数，与模型 attempt 分开计数：attempt 不会因对账而递增，
    # 用它当重试上限会让 UNKNOWN 永远挂起（Plan v6.4 §10 P0-1）。
    reconcile_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 调用租约：RESERVED 且租约过期 → UNKNOWN，禁止盲重试
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    # 已成功输出快照：恢复时复用，不重新调用也不重复计费（G-09）
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CostEntryModel(NovelBase):
    """append-only 成本流水。余额由流水派生并与物化账户校验（G-10）。"""

    __tablename__ = "novel_cost_entries"
    __table_args__ = (
        CheckConstraint(
            _states_sql("funding_source", _FUNDING_SOURCES), name="ck_novel_cost_funding"
        ),
        # 幂等键 logical_call_id:funding_pool:entry_kind；两段式需要区分预留结算与释放
        UniqueConstraint(
            "logical_call_id", "funding_pool", "entry_kind", name="uq_novel_cost_settlement"
        ),
        CheckConstraint("amount_minor >= 0", name="ck_novel_cost_non_negative"),
        Index("ix_novel_cost_work", "work_id", "chapter_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    logical_call_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    funding_pool: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    funding_source: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    entry_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="CONSUME"
    )  # RESERVE | CONSUME | RELEASE
    price_book_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuotaReservationModel(Timestamped, NovelBase):
    """两段式额度：reserved → consumed/released（Tech-Spec §6）。"""

    __tablename__ = "novel_quota_reservations"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_novel_quota_non_negative"),
        CheckConstraint("settled_minor <= amount_minor", name="ck_novel_quota_settled"),
        UniqueConstraint("reservation_key", name="uq_novel_quota_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reservation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    logical_call_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RESERVED")
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 持久事件 / 幂等（Tech-Spec §5 / §9）
# ---------------------------------------------------------------------------


class NovelEventModel(NovelBase):
    """G-17：持久化事件序列，SSE 断线可补帧。"""

    __tablename__ = "novel_events"
    __table_args__ = (
        UniqueConstraint("work_id", "sequence", name="uq_novel_events_work_sequence"),
        UniqueConstraint("event_id", name="uq_novel_events_event_id"),
        Index("ix_novel_events_work_seq", "work_id", "sequence"),
    )

    # SQLite 里只有 INTEGER PRIMARY KEY 才是 rowid 别名、才会自增；BIGINT 不会，
    # 插入直接 NOT NULL 失败。Postgres 上 BigInteger+autoincrement 是 BIGSERIAL，
    # 两边语义一致，用 variant 让模型在测试库里也能真写事件。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    decision_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class NovelWorkSequenceModel(NovelBase):
    """per-work 序列分配器。UPDATE ... RETURNING 保证单调递增无空洞。"""

    __tablename__ = "novel_work_sequences"

    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), primary_key=True
    )
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class IdempotencyRecordModel(NovelBase):
    """同键同参数返回首个结果；同键异参数返回 409（Tech-Spec §5）。"""

    __tablename__ = "novel_idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "idempotency_key", name="uq_novel_idem"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_ref: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# 长期创作记忆（Plan §4 R3）
# ---------------------------------------------------------------------------


class MemoryItemModel(NovelBase):
    """长期记忆条目：稳定规则 / 人物弧线 / 承诺与伏笔 / 关系变化。

    事实链（Canon）保持 append-only，本表只是**可召回的索引**：改意时只把条目
    置 ``invalidated_at``，不删不改，旧版上下文因此不会污染新版。
    """

    __tablename__ = "novel_memory_items"
    __table_args__ = (
        CheckConstraint(
            _states_sql("kind", _MEMORY_KINDS), name="ck_novel_memory_kind"
        ),
        CheckConstraint(
            _states_sql("state", _MEMORY_STATES), name="ck_novel_memory_state"
        ),
        UniqueConstraint(
            "work_id", "branch_id", "item_key", name="uq_novel_memory_key"
        ),
        Index("ix_novel_memory_work_state", "work_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="high")
    source_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 兑现章号：0 表示尚未兑现。「已兑现」与「从没被记住」不能在存储里长得一样，
    # 否则跨章保留未兑现承诺这条硬规矩无法验证（B-01）。
    resolved_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 分类依据（explicit_kind / co_occurrence / promise_signal ...）：「凭什么把这条
    # 记成规则」必须能查，否则分类错误无法与故意归类区分（B-01）。
    basis: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class MemoryEdgeModel(NovelBase):
    """记忆依赖边 ``(upstream -> downstream)``：最小子图重演的依据。"""

    __tablename__ = "novel_memory_edges"
    __table_args__ = (
        UniqueConstraint(
            "work_id", "branch_id", "upstream_key", "downstream_key",
            name="uq_novel_memory_edge",
        ),
        Index("ix_novel_memory_edge_up", "work_id", "upstream_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    upstream_key: Mapped[str] = mapped_column(String(255), nullable=False)
    downstream_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # depends    = 已确认的下游依赖（共享实体/主体推导得出）
    # independent = 已显式确认这一条没有上游依赖
    # 只有 depends 边时，孤立节点既可能是真独立也可能是漏记：必须靠 independent
    # 标记才能把两者分开，否则「有边」会被当成「图完整」（B-02）。
    edge_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="depends")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvalRunModel(NovelBase):
    """一次盲评评估（Plan §4 R4）。

    配置指纹与报告指纹同时落库：采样后改配置会让两者不一致，
    此时无论分数多好都不得宣布晋级。
    """

    __tablename__ = "novel_eval_runs"
    __table_args__ = (UniqueConstraint("eval_id", name="uq_novel_eval_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    eval_id: Mapped[str] = mapped_column(String(120), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    samples: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    scores: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    report: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    report_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="HOLD")
    verdict_reasons: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
