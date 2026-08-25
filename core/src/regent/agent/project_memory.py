"""Project memory: REGENT.md + memory_service wiring (P1-2)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.memory_service import AdmitMemory, MemoryKind, MemoryService

REGENT_MD_NAME = "REGENT.md"
REGENT_MD_MAX_LINES = 200
REGENT_MD_MAX_BYTES = 25 * 1024


class ProjectMemoryService:
    """AppProject-scoped REGENT.md plus Semantic/Episodic memory admits."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        *,
        projects_root: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._memories = MemoryService(sessions) if sessions is not None else None
        self._projects_root = (
            projects_root.resolve() if projects_root is not None else None
        )

    def regent_md_path(self, project_id: uuid.UUID | str) -> Path | None:
        if self._projects_root is None:
            return None
        return self._projects_root / str(project_id) / REGENT_MD_NAME

    def load_regent_md(self, project_id: uuid.UUID | str | None) -> str:
        if project_id is None:
            return ""
        path = self.regent_md_path(project_id)
        if path is None or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_regent_md(self, project_id: uuid.UUID | str, content: str) -> Path | None:
        path = self.regent_md_path(project_id)
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        clipped = _clip_regent_md(content)
        path.write_text(clipped, encoding="utf-8")
        return path

    def distill_regent_md(
        self,
        *,
        existing: str,
        goal_text: str,
        stack_hints: list[str],
        structure: list[str],
        gaps: list[str],
        verification_summary: str,
        verification_passed: bool | None = None,
    ) -> str:
        """Incremental distill without requiring an LLM call."""
        sections: dict[str, list[str]] = {
            "Goal lessons": [],
            "Tech stack": [],
            "Structure": [],
            "Known constraints / gaps": [],
            "Verification": [],
        }
        # Parse existing crude sections.
        current = None
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                title = stripped[3:].strip()
                current = title if title in sections else None
                continue
            if current and stripped:
                sections[current].append(stripped)

        if goal_text:
            item = f"- Goal: {goal_text[:240]}"
            if item not in sections["Goal lessons"]:
                sections["Goal lessons"].append(item)
        for hint in stack_hints:
            item = f"- {hint}"
            if item not in sections["Tech stack"]:
                sections["Tech stack"].append(item)
        for path in structure[:40]:
            item = f"- {path}"
            if item not in sections["Structure"]:
                sections["Structure"].append(item)
        if verification_passed is True:
            sections["Known constraints / gaps"] = []
        for gap in gaps[:12]:
            item = f"- {gap}"
            if item not in sections["Known constraints / gaps"]:
                sections["Known constraints / gaps"].append(item)
        if verification_summary:
            item = f"- {verification_summary[:300]}"
            if item not in sections["Verification"]:
                sections["Verification"].append(item)

        lines = ["# REGENT.md", "", "Project memory distilled from delivery runs.", ""]
        for title, items in sections.items():
            lines.append(f"## {title}")
            lines.extend(items[-30:] or ["- (none yet)"])
            lines.append("")
        return _clip_regent_md("\n".join(lines))

    async def record_run_outcome(
        self,
        *,
        org_key: str,
        goal_id: uuid.UUID | None,
        project_id: uuid.UUID | str | None,
        actor: str,
        goal_text: str,
        files: dict[str, str],
        gaps: list[str],
        verification_passed: bool,
        verification_summary: str,
        generator_ref: str,
    ) -> str:
        """Update REGENT.md and admit episodic/semantic memories."""
        stack = _infer_stack(files)
        structure = sorted(files.keys())[:60]
        existing = self.load_regent_md(project_id) if project_id else ""
        distilled = self.distill_regent_md(
            existing=existing,
            goal_text=goal_text,
            stack_hints=stack,
            structure=structure,
            gaps=gaps,
            verification_summary=verification_summary,
            verification_passed=verification_passed,
        )
        if project_id:
            self.write_regent_md(project_id, distilled)

        if self._memories is not None and org_key:
            kind = (
                MemoryKind.EPISODIC_GOAL_ACHIEVED
                if verification_passed
                else MemoryKind.EPISODIC_RUN_FAILURE
            )
            await self._memories.admit(
                AdmitMemory(
                    org_key=org_key,
                    kind=kind.value,
                    content={
                        "goal_text": goal_text[:500],
                        "generator_ref": generator_ref,
                        "verification_passed": verification_passed,
                        "verification_summary": verification_summary[:500],
                        "gaps": gaps[:12],
                        "stack": stack,
                    },
                    actor=actor,
                    goal_id=goal_id,
                )
            )
            if verification_passed and stack:
                pattern_key = json.dumps(
                    {
                        "pattern": "verified_delivery_stack",
                        "stack": sorted(stack),
                        "generator_ref": generator_ref,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                await self._memories.reinforce_semantic(
                    AdmitMemory(
                        org_key=org_key,
                        kind=MemoryKind.SEMANTIC_PATTERN.value,
                        content={
                            "pattern": "verified_delivery_stack",
                            "goal_shape": goal_text[:200],
                            "stack": stack,
                            "generator_ref": generator_ref,
                        },
                        actor=actor,
                        goal_id=goal_id,
                    ),
                    memory_key=pattern_key,
                    verification_threshold=2,
                )
        return distilled

    async def semantic_patterns_for_planning(
        self,
        org_key: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if self._memories is None:
            return []
        rows = await self._memories.query_by_kind(
            org_key,
            "semantic.",
            limit=max(limit * 4, limit),
            verified_only=True,
        )
        return [dict(r.content_json or {}) for r in rows[:limit]]

    async def relevant_verified_memory(
        self,
        org_key: str,
        *,
        query: str,
        limit: int = 4,
        max_chars: int = 4_000,
        include_unmatched: bool = False,
    ) -> str:
        """Return a small, evidence-gated memory slice for the current goal.

        CANDIDATE memories are deliberately excluded: an unverified run must not
        become an instruction merely because it was recorded recently.
        """
        if self._memories is None or not org_key or not query.strip():
            return ""
        rows = await self._memories.query_by_kind(
            org_key, "semantic.", limit=50, verified_only=True
        )
        query_tokens = _memory_tokens(query)
        ranked: list[tuple[int, Any]] = []
        for row in rows:
            content = dict(row.content_json or {})
            blob = json.dumps(content, ensure_ascii=False, sort_keys=True)
            overlap = len(query_tokens & _memory_tokens(blob))
            if overlap or include_unmatched:
                ranked.append((max(1, overlap), row))
        ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        lines: list[str] = []
        for score, row in ranked[:limit]:
            content = {
                key: value
                for key, value in dict(row.content_json or {}).items()
                if not str(key).startswith("_")
            }
            lines.append(
                f"- verified_memory={row.id} relevance={score} "
                f"created_at={getattr(row, 'created_at', '')} "
                + json.dumps(content, ensure_ascii=False, sort_keys=True)[:900]
            )
        return "\n".join(lines)[:max_chars]

    async def record_verified_user_fact(
        self,
        *,
        project_id: uuid.UUID | str,
        goal_id: uuid.UUID | None,
        actor: str,
        target: str,
        detail: str,
    ) -> None:
        """Persist explicit human steering as a verified project-scoped fact."""
        if self._memories is None or not str(detail).strip():
            return
        content = {
            "rule": str(detail).strip()[:1200],
            "target": str(target or "other")[:64],
            "authority": "explicit_user_instruction",
        }
        await self._memories.reinforce_semantic(
            AdmitMemory(
                org_key=f"project:{project_id}",
                kind=MemoryKind.SEMANTIC_RULE.value,
                content=content,
                actor=actor,
                goal_id=goal_id,
                verified=True,
            ),
            memory_key=json.dumps(content, ensure_ascii=False, sort_keys=True),
            verification_threshold=1,
        )


def _clip_regent_md(content: str) -> str:
    lines = content.splitlines()
    if len(lines) > REGENT_MD_MAX_LINES:
        lines = lines[:REGENT_MD_MAX_LINES]
        lines.append("...[truncated lines]")
    text = "\n".join(lines)
    encoded = text.encode("utf-8")
    if len(encoded) > REGENT_MD_MAX_BYTES:
        text = encoded[: REGENT_MD_MAX_BYTES - 20].decode("utf-8", errors="ignore")
        text += "\n...[truncated]"
    return text


def _memory_tokens(text: str) -> set[str]:
    lowered = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", lowered))
    # Chinese goals often have no spaces; character bigrams give a cheap,
    # deterministic relevance signal without introducing an embedding call.
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    words.update(cjk[i : i + 2] for i in range(max(0, len(cjk) - 1)))
    return words


def _infer_stack(files: dict[str, str]) -> list[str]:
    hints: list[str] = []
    req = files.get("requirements.txt") or ""
    lower = req.lower()
    for lib in ("flask", "fastapi", "django", "sqlalchemy", "sqlite", "redis"):
        if lib in lower or any(lib in (c or "").lower() for c in files.values() if c):
            hints.append(lib)
    if "src/app.py" in files:
        hints.append("src/app.py entrypoint")
    return sorted(set(hints))
