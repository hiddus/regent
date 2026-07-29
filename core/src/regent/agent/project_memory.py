"""Project memory: REGENT.md + memory_service wiring (P1-2)."""

from __future__ import annotations

import json
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
            sections["Goal lessons"].append(f"- Goal: {goal_text[:240]}")
        for hint in stack_hints:
            item = f"- {hint}"
            if item not in sections["Tech stack"]:
                sections["Tech stack"].append(item)
        for path in structure[:40]:
            item = f"- {path}"
            if item not in sections["Structure"]:
                sections["Structure"].append(item)
        for gap in gaps[:12]:
            item = f"- {gap}"
            if item not in sections["Known constraints / gaps"]:
                sections["Known constraints / gaps"].append(item)
        if verification_summary:
            sections["Verification"].append(f"- {verification_summary[:300]}")

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
                await self._memories.admit(
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
                    )
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
            org_key, MemoryKind.SEMANTIC_PATTERN.value, limit=limit
        )
        return [dict(r.content_json or {}) for r in rows]


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
