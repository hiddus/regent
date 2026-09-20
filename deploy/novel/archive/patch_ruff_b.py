"""清理本轮改动引入的 ruff 问题（I001 排序 / F821 / UP037）。"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        count = text.count(old)
        assert count == 1, f"{rel}: old 出现 {count} 次 -> {old[:70]!r}"
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print(f"patched {rel}")


patch(
    "core/src/regent/novel/application/generation.py",
    [
        ("from typing import Any\n", "from typing import TYPE_CHECKING, Any\n"),
        (
            """async def generate_ending_verdict(""",
            """if TYPE_CHECKING:  # 仅用于类型注解：运行时在函数内导入，避免循环导入
    from regent.novel.application.direction import EndingVerdict


async def generate_ending_verdict(""",
        ),
    ],
)

patch(
    "core/src/regent/novel/domain/ending.py",
    [
        ('def from_payload(cls, payload: dict[str, object] | None) -> "EndingIntent":',
         "def from_payload(cls, payload: dict[str, object] | None) -> EndingIntent:"),
        ('def from_payload(cls, payload: dict[str, object] | None) -> "EndingDecision":',
         "def from_payload(cls, payload: dict[str, object] | None) -> EndingDecision:"),
    ],
)

patch(
    "core/src/regent/novel/application/works.py",
    [
        (
            """from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.domain import ending
from regent.novel.application import memory as memory_app
from regent.novel.application import executor as executor_app""",
            """from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.domain import ending""",
        ),
    ],
)

patch(
    "core/src/regent/novel/api/novel.py",
    [
        (
            """    PathChangeImpact,
    EndingIntentRequest,
    ReportFactRequest,""",
            """    EndingIntentRequest,
    PathChangeImpact,
    ReportFactRequest,""",
        ),
    ],
)

patch(
    "tests/unit/novel/test_last_node_and_volume.py",
    [
        (
            """from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d""",
            """from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d""",
        ),
    ],
)
