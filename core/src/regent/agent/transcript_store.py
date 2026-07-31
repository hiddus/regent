"""Persist agent transcript turns to agent_transcripts (audit / resume)."""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.types import TranscriptTurn
from regent.infrastructure.models import AgentTranscriptModel


class AgentTranscriptStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessions = sessions

    async def persist(
        self,
        *,
        generation_run_id: uuid.UUID,
        turns: Sequence[TranscriptTurn],
    ) -> int:
        if self._sessions is None or not turns:
            return 0
        rows: list[AgentTranscriptModel] = []
        seq = 0
        for turn in turns:
            rows.append(
                AgentTranscriptModel(
                    id=uuid.uuid4(),
                    generation_run_id=generation_run_id,
                    turn=int(turn.turn),
                    seq=seq,
                    role=str(turn.role),
                    content=turn.content,
                    tool_name=turn.tool_name,
                    tool_call_id=turn.tool_call_id,
                    tool_arguments=dict(turn.tool_arguments or {}) or None,
                    tool_result=turn.tool_result,
                    input_tokens=int(turn.input_tokens or 0),
                    output_tokens=int(turn.output_tokens or 0),
                )
            )
            seq += 1
        async with self._sessions() as session, session.begin():
            session.add_all(rows)
            await session.flush()
        return len(rows)

    async def list_for_run(
        self,
        generation_run_id: uuid.UUID,
        *,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        if self._sessions is None:
            return []
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AgentTranscriptModel)
                .where(AgentTranscriptModel.generation_run_id == generation_run_id)
                .order_by(AgentTranscriptModel.turn.asc(), AgentTranscriptModel.seq.asc())
                .limit(limit)
            )
            return [
                {
                    "turn": row.turn,
                    "role": row.role,
                    "content": row.content,
                    "tool_name": row.tool_name,
                    "tool_call_id": row.tool_call_id,
                    "tool_arguments": row.tool_arguments,
                    "tool_result": row.tool_result,
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                }
                for row in rows
            ]

    @staticmethod
    def to_jsonable(turns: Sequence[TranscriptTurn]) -> list[dict[str, Any]]:
        return [
            {
                "turn": t.turn,
                "role": t.role,
                "content": t.content,
                "tool_name": t.tool_name,
                "tool_call_id": t.tool_call_id,
                "tool_arguments": t.tool_arguments,
                "tool_result": t.tool_result,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
            }
            for t in turns
        ]
