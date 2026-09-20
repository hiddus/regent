"""作品用例共享常量的单一权威来源。"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

LEASE_OWNER = "novel-producer"
RETRY_BACKOFF = timedelta(seconds=90)

MAX_CLARIFY_ROUNDS = 1
MAX_QUESTIONS_PER_ROUND = 3
MIN_PATH_NODES = 10
MAX_PATH_NODES = 20
MIN_NODES_PER_VOLUME = 5
MAX_NODES_PER_VOLUME = 30
CHAPTERS_PER_NODE = 3
CURRENT_EXPORT_NOTICE_VERSION = "export-notice-2026-09-v1"
AI_DISCLOSURE = "本文内容由 AI 参与生成"
CHECKPOINT_STEPS: frozenset[str] = frozenset({"DIRECT", "WEAVE"})
DEFAULT_GENRES = ("东方玄幻", "都市系统", "无限流")

EXPORT_NOTICE_TITLE = "关于作品去向，请先确认"
EXPORT_NOTICE_BODY = (
    "本平台产出的内容包含 AI 参与生成，按《人工智能生成合成内容标识办法》"
    "导出文件会保留 AI 参与标识。国内主流网文平台对 AI 生成内容有比例限制"
    "（例如部分平台要求 AI 含量低于 30%，起点要求全人工）。"
    "因此作品无法以保证过审的方式发布到这些平台。"
    "本平台不提供去除 AI 标识、降低检测值或帮助通过外部平台审核的功能。"
    "导出后你可自行决定是否以及在哪里发布，相关后果由你承担。"
)


class OnboardingStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    DIRECTIONS = "DIRECTIONS"
    WORLD_REVIEW = "WORLD_REVIEW"
    READY = "READY"
