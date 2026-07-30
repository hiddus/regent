"""FileChangeSetGenerator adapter for agentic-generation-v1."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.agent_runner import AgentRunner
from regent.agent.project_memory import ProjectMemoryService
from regent.agent.tools import WorkspaceToolkit
from regent.agent.transcript_store import AgentTranscriptStore
from regent.agent.types import (
    AgentBudget,
    BudgetExhaustedError,
    VerificationGap,
)
from regent.application.p1_contracts import FileChange, FileChangeSet, FileMode, FileOperation
from regent.application.p1_ports import GeneratedFileChangeSet
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.model import ModelProvider

GENERATOR_REF = "agentic-generation-v1"
PROMPT_VERSION = "agentic-generation-v1"


class AgenticCodeGenerator:
    """Multi-turn agent generator implementing FileChangeSetGenerator."""

    def __init__(
        self,
        provider: ModelProvider,
        artifacts: FileArtifactStore,
        *,
        workspace_root: Path,
        budget: AgentBudget | None = None,
        sessions: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._provider = provider
        self._artifacts = artifacts
        self._workspace_root = workspace_root.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._budget = budget or AgentBudget()
        self._sessions = sessions
        self._transcripts = AgentTranscriptStore(sessions)
        self._project_memory = ProjectMemoryService(
            sessions,
            projects_root=self._workspace_root / "project_memory",
        )

    async def generate(
        self,
        plan: dict[str, Any],
        *,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> GeneratedFileChangeSet:
        run_id = str(plan.get("generation_run_id") or uuid.uuid4())
        sandbox = self._workspace_root / "agentic" / run_id
        base_workspace = _resolve_base(plan)
        _prepare_sandbox(sandbox, base_workspace)
        base_files = _read_tree_bytes(sandbox) if base_workspace is not None else {}

        project_id = plan.get("app_project_id") or plan.get("project_id")
        if not project_id:
            project_id = (plan.get("acceptance_contract") or {}).get("app_project_id")
        if not plan.get("goal_id"):
            plan = {
                **plan,
                "goal_id": (plan.get("acceptance_contract") or {}).get("goal_id"),
                "org_key": (plan.get("acceptance_contract") or {}).get("org_key") or "default",
                "app_project_id": project_id,
            }
        else:
            plan = {**plan, "app_project_id": project_id}

        regent_md = (
            str(plan.get("regent_md") or "")
            or self._project_memory.load_regent_md(project_id)
            or _load_regent_md(plan)
        )

        toolkit = WorkspaceToolkit(sandbox)
        prior_gaps = _gaps_from_plan(plan)
        acceptance = dict(plan.get("acceptance_contract") or {})
        run_smoke = bool(acceptance.get("batch_run_smoke", True))
        runner = AgentRunner(
            self._provider,
            toolkit,
            budget=self._budget,
            regent_md=regent_md,
        )

        async def _on_turn(turn: int, summary: str) -> None:
            if on_progress is not None:
                await on_progress(summary)

        try:
            if on_progress is not None:
                await on_progress("正在启动多轮生成…")
            result = await runner.run(
                plan,
                prior_gaps=prior_gaps,
                verify=True,
                run_smoke=run_smoke,
                on_turn=_on_turn if on_progress else None,
            )
        except BudgetExhaustedError as exc:
            raise ValueError(
                f"delivery-review-v1 rejected non-deliverable surface: "
                f"EXHAUSTED_BUDGET: {exc.reason}"
            ) from exc

        # Always persist transcript for audit (even on verification failure).
        _write_transcript_sidecar(sandbox, result.transcript)
        generation_run_id = plan.get("generation_run_id")
        if generation_run_id:
            try:
                await self._transcripts.persist(
                    generation_run_id=uuid.UUID(str(generation_run_id)),
                    turns=result.transcript,
                )
            except Exception:  # noqa: BLE001 — sidecar already written; DB is best-effort
                pass

        gaps = []
        if result.verification is not None and not result.verification.passed:
            gaps = [f"{g.code}: {g.detail}" for g in result.verification.gaps]

        # P1-2: distill project memory after every run.
        try:
            await self._project_memory.record_run_outcome(
                org_key=str(plan.get("org_key") or "default"),
                goal_id=_maybe_uuid(plan.get("goal_id")),
                project_id=project_id,
                actor=str(plan.get("actor") or "regent-agent"),
                goal_text=str(plan.get("goal_anchor_text") or ""),
                files=result.files,
                gaps=gaps,
                verification_passed=bool(
                    result.verification and result.verification.passed
                ),
                verification_summary=(
                    result.verification.summary if result.verification else ""
                ),
                generator_ref=GENERATOR_REF,
            )
        except Exception:  # noqa: BLE001 — memory must not block delivery path
            pass

        if result.verification is not None and not result.verification.passed:
            reasons = "; ".join(gaps[:8])
            raise ValueError(
                f"delivery-review-v1 rejected non-deliverable surface: "
                f"verification-agent: {reasons or result.verification.summary}"
            )

        planned = set(plan.get("planned_paths") or [])
        scope = uuid.UUID(str(plan["hypothesis_decision_id"]))
        changes = _materialize_incremental_changes(
            base_files=base_files,
            files=result.files,
            planned=planned,
            artifacts=self._artifacts,
            scope=scope,
        )
        if not changes:
            raise ValueError("agentic generator produced no materializable files")

        return GeneratedFileChangeSet(
            output=FileChangeSet(
                changes=changes,
                generator_ref=GENERATOR_REF,
                prompt_version=PROMPT_VERSION,
            ),
            model_ref=result.model_ref or "agentic",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _resolve_base(plan: dict[str, Any]) -> Path | None:
    raw = plan.get("base_workspace")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.exists() else None


def _prepare_sandbox(sandbox: Path, base: Path | None) -> None:
    import shutil

    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    if base is None or not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if rel.startswith(".regent") or rel.endswith(".regent-source.zip"):
            continue
        dest = sandbox / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _read_tree_bytes(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".regent"):
            continue
        out[rel] = path.read_bytes()
    return out


def _materialize_incremental_changes(
    *,
    base_files: dict[str, bytes],
    files: dict[str, str],
    planned: set[str],
    artifacts: FileArtifactStore,
    scope: uuid.UUID,
) -> list[FileChange]:
    changes: list[FileChange] = []
    for relative, content in sorted(files.items()):
        if planned and relative not in planned and not _allowed_extra(relative):
            continue
        content_bytes = content.encode("utf-8")
        digest = hashlib.sha256(content_bytes).hexdigest()
        artifact = artifacts.put(scope, f"generated/{digest[:2]}/{digest}", content_bytes)
        previous = base_files.get(relative)
        if previous is None:
            op = FileOperation.CREATE
            prev_hash = None
        elif previous == content_bytes:
            continue
        else:
            op = FileOperation.REPLACE
            prev_hash = hashlib.sha256(previous).hexdigest()
        changes.append(
            FileChange(
                relative_path=relative,
                operation=op,
                content_artifact_uri=artifact.uri,
                content_hash=artifact.content_hash,
                expected_previous_hash=prev_hash,
                mode=FileMode.REGULAR,
                media_type=_media_type(relative),
                rationale=f"agentic turn product ({GENERATOR_REF})",
            )
        )
    return changes


def _allowed_extra(relative: str) -> bool:
    name = relative.replace("\\", "/").lower()
    return (
        name in {"requirements.txt", "readme.md", "pyproject.toml"}
        or name.startswith("tests/")
        or name.startswith("static/")
        or name.startswith("templates/")
        or name.endswith((".html", ".css", ".js", ".py", ".md", ".txt", ".json"))
    )


def _media_type(relative: str) -> str:
    lower = relative.lower()
    if lower.endswith(".html"):
        return "text/html"
    if lower.endswith(".css"):
        return "text/css"
    if lower.endswith(".js"):
        return "application/javascript"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".py"):
        return "text/x-python"
    return "text/plain"


def _gaps_from_plan(plan: dict[str, Any]) -> list[VerificationGap]:
    acceptance = dict(plan.get("acceptance_contract") or {})
    gaps: list[VerificationGap] = []
    for reason in list(acceptance.get("delivery_gap_reasons") or [])[:12]:
        text = str(reason)
        code, _, detail = text.partition(":")
        gaps.append(
            VerificationGap(
                code=(code.strip() or "prior-gap")[:64],
                detail=(detail.strip() or text)[:500],
            )
        )
    return gaps


def _load_regent_md(plan: dict[str, Any]) -> str:
    path = plan.get("regent_md_path")
    if not path:
        return ""
    try:
        return Path(str(path)).read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_transcript_sidecar(sandbox: Path, transcript: list[Any]) -> None:
    payload = AgentTranscriptStore.to_jsonable(transcript)
    (sandbox / ".regent_agent_transcript.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
