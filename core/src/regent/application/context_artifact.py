"""Long-task context durability: tool-result offload, transcript artifacts, rehydration.

Spec §18.6 — large tool results and pre-compact transcripts become immutable
Artifacts; messages retain URI/SHA-256/MIME/length/preview. Rehydration
re-injects hard constraints, Permit state, and open HumanTasks.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import ArtifactModel

TOOL_RESULT_OFFLOAD_TOKEN_THRESHOLD = 20_000
PREVIEW_CHARS = 500
CONTEXT_ARTIFACT_SCHEMA = "context-artifact/v1"


@dataclass(frozen=True, slots=True)
class OffloadRef:
    uri: str
    content_hash: str
    mime_type: str
    length_chars: int
    estimated_tokens: int
    preview: str
    artifact_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "content_hash": self.content_hash,
            "mime_type": self.mime_type,
            "length_chars": self.length_chars,
            "estimated_tokens": self.estimated_tokens,
            "preview": self.preview,
            "artifact_id": str(self.artifact_id) if self.artifact_id else None,
            "schema_version": CONTEXT_ARTIFACT_SCHEMA,
        }


@dataclass(frozen=True, slots=True)
class StructuredCompactSummary:
    goal_intent: str
    produced_artifacts: list[str]
    open_risks: list[str]
    next_actions: list[str]
    hard_constraints: list[str]
    permit_state: dict[str, Any]
    open_human_tasks: list[str]
    plan_checkpoint_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_intent": self.goal_intent,
            "produced_artifacts": list(self.produced_artifacts),
            "open_risks": list(self.open_risks),
            "next_actions": list(self.next_actions),
            "hard_constraints": list(self.hard_constraints),
            "permit_state": dict(self.permit_state),
            "open_human_tasks": list(self.open_human_tasks),
            "plan_checkpoint_ref": self.plan_checkpoint_ref,
            "schema_version": CONTEXT_ARTIFACT_SCHEMA,
        }


def estimate_tokens_from_text(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def should_offload_tool_result(
    text: str, *, threshold_tokens: int = TOOL_RESULT_OFFLOAD_TOKEN_THRESHOLD
) -> bool:
    return estimate_tokens_from_text(text) >= threshold_tokens


def build_offload_ref(
    text: str,
    *,
    uri: str,
    mime_type: str = "text/plain",
    artifact_id: uuid.UUID | None = None,
) -> OffloadRef:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return OffloadRef(
        uri=uri,
        content_hash=digest,
        mime_type=mime_type,
        length_chars=len(text),
        estimated_tokens=estimate_tokens_from_text(text),
        preview=text[:PREVIEW_CHARS],
        artifact_id=artifact_id,
    )


def build_structured_compact_summary(
    *,
    goal_intent: str,
    produced_artifacts: Sequence[str] | None = None,
    open_risks: Sequence[str] | None = None,
    next_actions: Sequence[str] | None = None,
    hard_constraints: Sequence[str] | None = None,
    permit_state: Mapping[str, Any] | None = None,
    open_human_tasks: Sequence[str] | None = None,
    plan_checkpoint_ref: str | None = None,
    heuristic_blob: str | None = None,
) -> StructuredCompactSummary:
    risks = list(open_risks or [])
    actions = list(next_actions or [])
    if heuristic_blob and not actions:
        # Pull last non-empty lines as weak next-action hints (deterministic).
        lines = [ln.strip() for ln in heuristic_blob.splitlines() if ln.strip()]
        actions = lines[-3:]
    return StructuredCompactSummary(
        goal_intent=goal_intent,
        produced_artifacts=list(produced_artifacts or []),
        open_risks=risks,
        next_actions=actions,
        hard_constraints=list(hard_constraints or []),
        permit_state=dict(permit_state or {}),
        open_human_tasks=list(open_human_tasks or []),
        plan_checkpoint_ref=plan_checkpoint_ref,
    )


def verify_artifact_hash(*, content: bytes | str, expected_hash: str) -> bool:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest() == expected_hash


def rehydrate_context(
    *,
    summary: StructuredCompactSummary | Mapping[str, Any],
    artifact_payloads: Mapping[str, str] | None = None,
    expected_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Rebuild durable context; fail closed on hash mismatch."""
    structured = (
        summary
        if isinstance(summary, StructuredCompactSummary)
        else StructuredCompactSummary(
            goal_intent=str(summary.get("goal_intent") or ""),
            produced_artifacts=list(summary.get("produced_artifacts") or []),
            open_risks=list(summary.get("open_risks") or []),
            next_actions=list(summary.get("next_actions") or []),
            hard_constraints=list(summary.get("hard_constraints") or []),
            permit_state=dict(summary.get("permit_state") or {}),
            open_human_tasks=list(summary.get("open_human_tasks") or []),
            plan_checkpoint_ref=summary.get("plan_checkpoint_ref"),
        )
    )
    payloads = dict(artifact_payloads or {})
    expected = dict(expected_hashes or {})
    verified: dict[str, str] = {}
    failures: list[str] = []
    for key, content in payloads.items():
        want = expected.get(key)
        if want and not verify_artifact_hash(content=content, expected_hash=want):
            failures.append(key)
        else:
            verified[key] = content
    if failures:
        return {
            "ok": False,
            "error": "ARTIFACT_HASH_MISMATCH",
            "failed_keys": failures,
            "summary": structured.as_dict(),
        }
    return {
        "ok": True,
        "summary": structured.as_dict(),
        "artifacts": verified,
        "hard_constraints": list(structured.hard_constraints),
        "permit_state": dict(structured.permit_state),
        "open_human_tasks": list(structured.open_human_tasks),
    }


class ContextArtifactService:
    """Persist offloaded tool results / transcripts as Artifact rows + files."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        artifact_root: str | Path,
    ) -> None:
        self._sessions = sessions
        self._root = Path(artifact_root)
        self._root.mkdir(parents=True, exist_ok=True)

    async def offload_tool_result(
        self,
        *,
        goal_id: uuid.UUID,
        text: str,
        producer_ref: str,
        run_id: uuid.UUID | None = None,
        work_id: uuid.UUID | None = None,
        mime_type: str = "text/plain",
        threshold_tokens: int = TOOL_RESULT_OFFLOAD_TOKEN_THRESHOLD,
    ) -> OffloadRef | None:
        if not should_offload_tool_result(text, threshold_tokens=threshold_tokens):
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rel = f"context/{goal_id}/tool_result_{digest[:16]}.txt"
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        uri = f"artifact://{rel}"
        artifact_id = await self._insert_artifact(
            goal_id=goal_id,
            run_id=run_id,
            work_id=work_id,
            artifact_type="tool_result_offload",
            uri=uri,
            content_hash=digest,
            producer_ref=producer_ref,
            provenance={
                "schema_version": CONTEXT_ARTIFACT_SCHEMA,
                "length_chars": len(text),
                "estimated_tokens": estimate_tokens_from_text(text),
                "mime_type": mime_type,
            },
        )
        return build_offload_ref(
            text, uri=uri, mime_type=mime_type, artifact_id=artifact_id
        )

    async def save_transcript_before_compact(
        self,
        *,
        goal_id: uuid.UUID,
        transcript: Sequence[Mapping[str, Any]] | str,
        producer_ref: str,
        run_id: uuid.UUID | None = None,
    ) -> OffloadRef:
        if isinstance(transcript, str):
            text = transcript
        else:
            text = json.dumps(list(transcript), ensure_ascii=False, indent=2)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rel = f"context/{goal_id}/transcript_{digest[:16]}.json"
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        uri = f"artifact://{rel}"
        artifact_id = await self._insert_artifact(
            goal_id=goal_id,
            run_id=run_id,
            work_id=None,
            artifact_type="transcript_pre_compact",
            uri=uri,
            content_hash=digest,
            producer_ref=producer_ref,
            provenance={"schema_version": CONTEXT_ARTIFACT_SCHEMA},
        )
        return build_offload_ref(
            text, uri=uri, mime_type="application/json", artifact_id=artifact_id
        )

    async def read_by_hash(self, content_hash: str) -> str | None:
        async with self._sessions() as session:
            model = await session.scalar(
                select(ArtifactModel).where(ArtifactModel.content_hash == content_hash).limit(1)
            )
        if model is None:
            return None
        # uri: artifact://rel
        rel = model.uri.removeprefix("artifact://")
        path = self._root / rel
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        if not verify_artifact_hash(content=text, expected_hash=content_hash):
            return None
        return text

    async def _insert_artifact(
        self,
        *,
        goal_id: uuid.UUID,
        run_id: uuid.UUID | None,
        work_id: uuid.UUID | None,
        artifact_type: str,
        uri: str,
        content_hash: str,
        producer_ref: str,
        provenance: dict[str, Any],
    ) -> uuid.UUID:
        async with self._sessions() as session, session.begin():
            if work_id is None:
                version = await session.scalar(
                    select(func.coalesce(func.max(ArtifactModel.version), 0)).where(
                        ArtifactModel.goal_id == goal_id,
                        ArtifactModel.artifact_type == artifact_type,
                        ArtifactModel.work_id.is_(None),
                    )
                )
            else:
                version = await session.scalar(
                    select(func.coalesce(func.max(ArtifactModel.version), 0)).where(
                        ArtifactModel.work_id == work_id,
                        ArtifactModel.artifact_type == artifact_type,
                    )
                )
            artifact_id = uuid.uuid4()
            session.add(
                ArtifactModel(
                    id=artifact_id,
                    goal_id=goal_id,
                    work_id=work_id,
                    run_id=run_id,
                    artifact_type=artifact_type,
                    schema_ref=CONTEXT_ARTIFACT_SCHEMA,
                    uri=uri,
                    content_hash=content_hash,
                    producer_ref=producer_ref,
                    provenance=provenance,
                    version=int(version or 0) + 1,
                )
            )
            await session.flush()
            return artifact_id
