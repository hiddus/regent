"""Structured generation progress (avoids Chinese-string round-trips)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ProgressEvent:
    summary: str
    type: str = "status"
    turn: int | None = None
    tool: str | None = None
    args_preview: str | None = None
    result_preview: str | None = None
    detail: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    event_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


ProgressLike = ProgressEvent | str


def coerce_progress(value: ProgressLike) -> ProgressEvent:
    if isinstance(value, ProgressEvent):
        return value
    text = str(value or "").strip()
    tool: str | None = None
    if text.startswith("执行工具 "):
        rest = text[len("执行工具 ") :]
        if "：" in rest:
            tool = rest.split("：", 1)[0].strip() or None
        elif ":" in rest:
            tool = rest.split(":", 1)[0].strip() or None
    return ProgressEvent(summary=text[:240], type="tool_call" if tool else "status", tool=tool)
