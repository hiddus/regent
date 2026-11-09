"""Cross-repair Agent Run ledger (M0-3).

Accumulates model/tool/wall costs across nested repair rounds so totals
equal the sum of each round (exit gate for M0).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentRunLedger:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_invocations: int = 0
    wall_seconds: float = 0.0
    turns: int = 0
    repair_rounds: int = 0
    compact_events: int = 0
    primary_failure_code: str | None = None
    snapshot_file_count: int = 0
    snapshot_truncated: bool = False
    transcript_turns: int = 0
    notes: list[str] = field(default_factory=list)

    def add_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.cached_tokens += max(0, int(cached_tokens))

    def add_tool_invocation(self, n: int = 1) -> None:
        self.tool_invocations += max(0, int(n))

    def add_turn(self, n: int = 1) -> None:
        self.turns += max(0, int(n))

    def merge(self, other: AgentRunLedger) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.tool_invocations += other.tool_invocations
        self.wall_seconds += other.wall_seconds
        self.turns += other.turns
        self.repair_rounds += other.repair_rounds
        self.compact_events += other.compact_events
        self.snapshot_file_count = max(self.snapshot_file_count, other.snapshot_file_count)
        self.snapshot_truncated = self.snapshot_truncated or other.snapshot_truncated
        self.transcript_turns += other.transcript_turns
        if other.primary_failure_code and not self.primary_failure_code:
            self.primary_failure_code = other.primary_failure_code
        self.notes.extend(other.notes)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_rate(self) -> float | None:
        if self.input_tokens <= 0:
            return None
        return min(1.0, self.cached_tokens / self.input_tokens)
