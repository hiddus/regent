"""Novel Engine 领域 schema（M0 首个开发批次 · Plan §10 第 3/4/6 项）。

Revision ID: 20260903_0048
Revises: 20260810_0047

覆盖：
- 身份与会话（G-11：服务端 principal，不采信客户端 actor）
- 作品 / 目标 / 关键路径 / onboarding（FR-01~FR-05）
- 章运行与步骤（Tech-Spec §3.3 / §5 幂等键）
- 角色 / 信息集 / Canon（G-03 / G-06 / G-07）
- 裁决 / 内容审核（G-13 / G-23 / FR-25）
- 分享 / 导出 / 导出告知（G-15 / G-22 / FR-17 / FR-23）
- 模型调用 / 成本 / 额度（G-08 / G-10 / FR-18 / FR-19）
- 持久事件 / 序列 / 幂等记录（G-09 / G-17 / FR-20）

P0-5（真实 PostgreSQL 认证）修正：本迁移**不再**直接按当前 ORM metadata 全量建表。
metadata 是「今天」的形状，而 0049~0052 还要各自建 novel_volumes / novel_arc_nodes
以及一批列与约束——照抄 metadata 会让全新库在升级链上撞 DuplicateTable /
DuplicateColumn，即**全新环境根本装不起来**。

因此本迁移：建表 → 归一化回 0048 时代的形状 → 用冻结清单核对。模型再漂移时这里
会**显式报错**，而不是静默建出一个与升级库不一致的库。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0048"
down_revision: str | None = "20260810_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- 归后续迁移所有的对象：0048 不得抢先创建 ---------------------------------
_LATER_TABLES = frozenset(
    {
        "novel_volumes",
        "novel_arc_nodes",
        "novel_memory_items",
        "novel_memory_edges",
        "novel_eval_runs",
    }
)

_LATER_COLUMNS: dict[str, frozenset[str]] = {
    "novel_works": frozenset(                                               # 0049 / 0055
        {"total_volume_count", "ending_target_volume", "ending_statement"}
    ),
    "novel_critical_nodes": frozenset({"volume_no", "arc_no"}),             # 0049
    "novel_chapter_runs": frozenset(                                        # 0050 / 0051
        {
            "user_guidance",
            "auto_advance",
            "lease_owner",
            "lease_expires_at",
            "fencing_token",
        }
    ),
    "novel_model_calls": frozenset(                                         # 0051 / 0052
        {
            "attempt",
            "reserved_amount_minor",
            "actual_amount_minor",
            "currency",
            "cached_input_tokens",
            "usage_source",
            "provider_request_id",
            "reconcile_count",
            "lease_owner",
            "lease_expires_at",
            "lease_ttl_seconds",
            "output_json",
            "error_code",
            "updated_at",
        }
    ),
    # 注意：_LATER_COLUMNS 只能列 0048 自己建的表。novel_memory_items /
    # novel_memory_edges 属 _LATER_TABLES（0053 才建），它们后来新增的列
    # （resolved_chapter_no / basis / edge_kind，0055）写在这里会让 0048 对
    # 不存在的表发 DROP COLUMN，全新库 upgrade head 直接失败（真机已复现）。
}

# 0051 会重建的索引与唯一约束：0048 只能是 0051 之前那一版
_LATER_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_novel_model_calls_logical", "novel_model_calls"),
    ("ix_novel_model_calls_status", "novel_model_calls"),
)

_FROZEN_COLUMNS: dict[str, frozenset[str]] = {
    "novel_export_notice_logs": frozenset({
        "acknowledged_at",
        "id",
        "ip_hash",
        "notice_version",
        "user_id",
        "work_id",
    }),
    "novel_idempotency_records": frozenset({
        "created_at",
        "id",
        "idempotency_key",
        "request_fingerprint",
        "response_body",
        "response_ref",
        "scope",
        "status_code",
    }),
    "novel_principals": frozenset({
        "created_at",
        "deleted_at",
        "display_name",
        "id",
        "subject",
        "updated_at",
    }),
    "novel_sessions": frozenset({
        "created_at",
        "expires_at",
        "id",
        "principal_id",
        "revoked_at",
        "token_hash",
        "user_agent",
    }),
    "novel_works": frozenset({
        "branch_id",
        "created_at",
        "deleted_at",
        "genre",
        "id",
        "latest_chapter_no",
        "owner_id",
        "public_state",
        "state",
        "title",
        "updated_at",
        "version",
    }),
    "novel_canon_commits": frozenset({
        "branch_id",
        "chapter_no",
        "created_at",
        "facts",
        "id",
        "parent_version",
        "source_hash",
        "validation_id",
        "version",
        "work_id",
    }),
    "novel_chapter_runs": frozenset({
        "attempt",
        "branch_id",
        "canonized_at",
        "chapter_no",
        "content",
        "created_at",
        "current_step",
        "generation_context",
        "id",
        "input_version",
        "performances",
        "review",
        "state",
        "title",
        "updated_at",
        "version",
        "word_count",
        "work_id",
    }),
    "novel_cost_entries": frozenset({
        "amount_minor",
        "chapter_no",
        "created_at",
        "currency",
        "entry_kind",
        "funding_pool",
        "funding_source",
        "id",
        "logical_call_id",
        "price_book_version",
        "step",
        "work_id",
    }),
    "novel_critical_paths": frozenset({
        "created_at",
        "dependency_edges",
        "frozen_through_chapter",
        "id",
        "node_count",
        "updated_at",
        "version",
        "work_id",
    }),
    "novel_events": frozenset({
        "branch_id",
        "causation_id",
        "chapter_no",
        "correlation_id",
        "data",
        "decision_id",
        "event_id",
        "id",
        "occurred_at",
        "schema_version",
        "sequence",
        "type",
        "work_id",
    }),
    "novel_export_jobs": frozenset({
        "byte_size",
        "content_sha256",
        "created_at",
        "format",
        "id",
        "notice_version",
        "storage_key",
        "updated_at",
        "user_id",
        "work_id",
    }),
    "novel_export_notices": frozenset({
        "created_at",
        "id",
        "notice_version",
        "satisfied_at",
        "updated_at",
        "user_id",
        "work_id",
    }),
    "novel_goals": frozenset({
        "assumptions",
        "created_at",
        "id",
        "locked_at",
        "normalized_goal",
        "raw_intent",
        "updated_at",
        "version",
        "work_id",
    }),
    "novel_model_calls": frozenset({
        "chapter_no",
        "context_hash",
        "cost_scope",
        "created_at",
        "id",
        "input_tokens",
        "logical_call_id",
        "model",
        "output_hash",
        "output_tokens",
        "price_book_version",
        "prompt_hash",
        "prompt_version",
        "provider",
        "purpose",
        "run_id",
        "sampling",
        "status",
        "step",
        "work_id",
    }),
    "novel_moderation_cases": frozenset({
        "appeal_reason",
        "appealed_at",
        "chapter_no",
        "created_at",
        "decision",
        "evidence_ref",
        "id",
        "reason_code",
        "resolved_at",
        "target_type",
        "updated_at",
        "work_id",
    }),
    "novel_onboarding_sessions": frozenset({
        "assumptions",
        "clarify_round",
        "created_at",
        "directions",
        "id",
        "locked_at",
        "question_count",
        "questions",
        "selected_card_id",
        "updated_at",
        "user_id",
        "work_id",
    }),
    "novel_personas": frozenset({
        "created_at",
        "drives",
        "id",
        "identity",
        "name",
        "stable_traits",
        "updated_at",
        "version",
        "voice",
        "work_id",
    }),
    "novel_quota_reservations": frozenset({
        "amount_minor",
        "chapter_no",
        "claim_token",
        "created_at",
        "currency",
        "expires_at",
        "id",
        "logical_call_id",
        "reservation_key",
        "settled_minor",
        "status",
        "updated_at",
        "work_id",
    }),
    "novel_shares": frozenset({
        "created_at",
        "expires_at",
        "from_chapter",
        "id",
        "noindex",
        "revoked_at",
        "scope",
        "to_chapter",
        "token",
        "updated_at",
        "work_id",
    }),
    "novel_work_sequences": frozenset({
        "last_sequence",
        "work_id",
    }),
    "novel_chapter_steps": frozenset({
        "attempt",
        "created_at",
        "error_code",
        "id",
        "input_version",
        "output_ref",
        "run_id",
        "state",
        "step",
        "updated_at",
    }),
    "novel_critical_nodes": frozenset({
        "consequences",
        "id",
        "locked",
        "node_id",
        "node_type",
        "ordinal",
        "path_id",
        "preconditions",
        "promise",
        "requires_human",
        "title",
    }),
    "novel_decision_requests": frozenset({
        "chapter_no",
        "confirm_nonce",
        "created_at",
        "deadline",
        "default_option_id",
        "id",
        "impact_horizon_chapters",
        "impact_level",
        "node_id",
        "options",
        "resolved_at",
        "resolved_by",
        "resolved_option_id",
        "run_id",
        "state",
        "trigger_summary",
        "updated_at",
        "version",
        "why_human",
        "work_id",
    }),
    "novel_information_sets": frozenset({
        "context_hash",
        "created_at",
        "exclusions",
        "grants",
        "id",
        "persona_id",
        "scene_id",
        "work_id",
    }),
}


def _metadata_tables() -> list[sa.Table]:
    from regent.novel.infrastructure.models import NovelBase

    return [
        table
        for table in NovelBase.metadata.sorted_tables
        if table.name not in _LATER_TABLES
    ]


def _assert_frozen(bind: sa.Connection) -> None:
    """建成后核对：与冻结清单不一致就显式失败，不留静默漂移。"""
    inspector = sa.inspect(bind)
    for name in sorted(_FROZEN_COLUMNS):
        actual = {column["name"] for column in inspector.get_columns(name)}
        expected = set(_FROZEN_COLUMNS[name])
        if actual != expected:
            raise RuntimeError(
                f"0048 schema drift on {name}: "
                f"missing={sorted(expected - actual)} "
                f"unexpected={sorted(actual - expected)}; "
                "模型改了但没有迁移——不要改 0048，请新增迁移"
            )


def upgrade() -> None:
    bind = op.get_bind()
    for table in _metadata_tables():
        table.create(bind)

    # 归一化：把「今天」多出来的列与约束退回 0048 时代的形状，交给 0049+ 去加。
    # 顺序要紧：DROP COLUMN 会连带删掉依赖它的唯一约束，所以先换约束再删列。
    for index_name, table_name in _LATER_INDEXES:
        op.drop_index(index_name, table_name=table_name)

    op.drop_constraint(
        "uq_novel_model_calls_logical_attempt", "novel_model_calls", type_="unique"
    )
    op.create_unique_constraint(
        "uq_novel_model_calls_logical", "novel_model_calls", ["logical_call_id"]
    )
    op.drop_constraint("uq_novel_cost_settlement", "novel_cost_entries", type_="unique")
    op.create_unique_constraint(
        "uq_novel_cost_settlement",
        "novel_cost_entries",
        ["logical_call_id", "funding_pool"],
    )

    for table_name in sorted(_LATER_COLUMNS):
        for column in sorted(_LATER_COLUMNS[table_name]):
            op.drop_column(table_name, column)

    _assert_frozen(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_metadata_tables()):
        table.drop(bind)
