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

建表以 ORM metadata 为唯一来源，避免迁移与模型漂移。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0048"
down_revision: str | None = "20260810_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from regent.novel.infrastructure.models import NovelBase

    bind = op.get_bind()
    for table in NovelBase.metadata.sorted_tables:
        table.create(bind)


def downgrade() -> None:
    from regent.novel.infrastructure.models import NovelBase

    bind = op.get_bind()
    for table in reversed(NovelBase.metadata.sorted_tables):
        table.drop(bind)
