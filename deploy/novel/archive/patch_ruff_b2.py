"""修 import 排序（只动本轮改动涉及的块）。"""

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
    "core/src/regent/novel/application/works.py",
    [
        (
            """from regent.novel.application.direction import ARCHITECTURE, ProductionStopped, is_directed
from regent.novel.application.events import append_event
from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.domain import ending
from regent.novel.application.production import (
    acquire_run_lease,
    lease_is_valid,
    release_run_lease,
)""",
            """from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.direction import ARCHITECTURE, ProductionStopped, is_directed
from regent.novel.application.events import append_event
from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.application.production import (
    acquire_run_lease,
    lease_is_valid,
    release_run_lease,
)
from regent.novel.domain import ending""",
        ),
    ],
)

patch(
    "tests/unit/novel/test_last_node_and_volume.py",
    [
        (
            """import pytest
from regent.novel.application import works
from regent.novel.application import direction as d
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application.generation import StoryOutline, StoryOutlineNode""",
            """import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.application.generation import StoryOutline, StoryOutlineNode""",
        ),
    ],
)
