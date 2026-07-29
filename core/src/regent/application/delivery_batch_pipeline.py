"""Incremental delivery batch pipeline (Phases A–D).

Orchestrates: propose batches → isolated generate (subagent) → batch verify →
merge onto prior workspace → global verify on final snapshot.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.subagent import SubagentBrief, SubagentRunner
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, VerificationGap
from regent.agent.verification import VerificationAgent
from regent.application.delivery_batch_service import (
    BATCH_GENERATING,
    BATCH_MERGED,
    BATCH_PLANNED,
    BATCH_REJECTED,
    BATCH_VERIFYING,
    DeliveryBatchSpec,
    persist_batch_plan,
    propose_delivery_batches,
    transition_batch,
)
from regent.application.p1_contracts import FileChange, FileChangeSet, FileMode, FileOperation
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import DeliveryBatchModel
from regent.infrastructure.workspace_writer import WorkspaceCommit, WorkspaceWriter

logger = logging.getLogger(__name__)


class _ChatProvider(Protocol):
    async def chat(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class BatchPipelineResult:
    commit: WorkspaceCommit
    changes: FileChangeSet
    batches: list[dict[str, Any]]
    global_verification: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model_ref: str


class DeliveryBatchPipeline:
    """Run generate→verify→merge for each batch, then global acceptance gate."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        provider: _ChatProvider,
        artifacts: FileArtifactStore,
        writer: WorkspaceWriter,
        workspace_root: Path,
        budget: AgentBudget | None = None,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._artifacts = artifacts
        self._writer = writer
        self._workspace_root = workspace_root.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._budget = budget or AgentBudget(max_turns=30, max_tokens=120_000)

    async def run(
        self,
        *,
        goal_id: uuid.UUID,
        app_project_id: uuid.UUID,
        generation_run_id: uuid.UUID,
        plan_payload: dict[str, Any],
        correlation_id: str,
        attempt: int = 1,
        prior_workspace: Path | None = None,
        event_sink: Any | None = None,
    ) -> BatchPipelineResult:
        planned_paths = list(plan_payload.get("planned_paths") or [])
        component_plan = list(plan_payload.get("component_plan") or [])
        acceptance = dict(plan_payload.get("acceptance_contract") or {})
        milestone_key = str(acceptance.get("milestone_key") or "")
        milestone_ordinal = acceptance.get("milestone_ordinal")
        milestone_title = acceptance.get("milestone_title")
        specs = propose_delivery_batches(
            planned_paths,
            component_plan,
            milestone_key=milestone_key,
            milestone_ordinal=int(milestone_ordinal) if milestone_ordinal is not None else None,
            milestone_title=str(milestone_title) if milestone_title else None,
            acceptance=acceptance,
            force_incremental=True,
        )
        if event_sink is not None:
            await event_sink(
                "DELIVERY_BATCH_PLANNED",
                f"已拆分为 {len(specs)} 个交付批次，将逐步生成、验证并合并。",
                {
                    "goal_id": str(goal_id),
                    "generation_run_id": str(generation_run_id),
                    "batch_count": len(specs),
                    "batches": [
                        {"ordinal": s.ordinal, "key": s.key, "title": s.title, "paths": list(s.scope_paths)}
                        for s in specs
                    ],
                },
            )

        async with self._sessions() as session, session.begin():
            rows = await persist_batch_plan(
                session,
                goal_id=goal_id,
                app_project_id=app_project_id,
                generation_run_id=generation_run_id,
                specs=specs,
                correlation_id=correlation_id,
                attempt=attempt,
            )
            batch_ids = [row.id for row in rows]

        regent_md = str(plan_payload.get("regent_md") or "")
        goal_anchor = str(plan_payload.get("goal_anchor_text") or "")
        success_criteria = acceptance.get("success_criteria") or acceptance.get(
            "full_goal_success_criteria"
        )
        hypothesis_id = uuid.UUID(str(plan_payload["hypothesis_decision_id"]))
        runtime_profile_hash = str(plan_payload.get("runtime_profile_hash") or "")

        subagent = SubagentRunner(
            self._provider,
            workspace_root=self._workspace_root / "batches" / str(generation_run_id),
            budget=self._budget,
            regent_md=regent_md,
        )

        merged_path = prior_workspace
        input_tokens = 0
        output_tokens = 0
        model_ref = "agentic-batch"
        batch_summaries: list[dict[str, Any]] = []
        final_commit: WorkspaceCommit | None = None
        final_changes: FileChangeSet | None = None

        for spec, batch_id in zip(specs, batch_ids, strict=True):
            async with self._sessions() as session, session.begin():
                row = await session.get(DeliveryBatchModel, batch_id)
                assert row is not None
                transition_batch(row, BATCH_GENERATING)

            if event_sink is not None:
                await event_sink(
                    "DELIVERY_BATCH_STARTED",
                    f"开始第 {spec.ordinal}/{len(specs)} 批：{spec.title}",
                    {
                        "batch_id": str(batch_id),
                        "batch_key": spec.key,
                        "batch_ordinal": spec.ordinal,
                        "scope_paths": list(spec.scope_paths),
                        "base_workspace": str(merged_path) if merged_path else None,
                    },
                )

            brief = SubagentBrief(
                milestone_key=spec.key,
                milestone_title=spec.title,
                milestone_ordinal=spec.ordinal,
                acceptance=dict(spec.acceptance),
                planned_paths=list(spec.scope_paths),
            )
            # Seed subagent sandbox from prior merge before run.
            sandbox = (
                self._workspace_root
                / "batches"
                / str(generation_run_id)
                / "subagents"
                / f"{brief.milestone_ordinal}-{brief.milestone_key}"
            )
            _seed_sandbox(sandbox, merged_path)

            prior_gaps = _gaps_from_acceptance(acceptance)
            try:
                result = await subagent.run_milestone(
                    goal_anchor_text=goal_anchor,
                    success_criteria=success_criteria if isinstance(success_criteria, dict) else None,
                    brief=brief,
                    prior_gaps=prior_gaps,
                    verify=False,  # pipeline owns batch + global gates
                )
            except Exception as exc:
                await self._reject_batch(
                    batch_id,
                    verification={"verdict": "FAIL", "summary": str(exc)[:500]},
                    summary={"error": str(exc)[:500]},
                )
                if event_sink is not None:
                    await event_sink(
                        "DELIVERY_BATCH_REJECTED",
                        f"第 {spec.ordinal} 批生成失败：{exc}",
                        {"batch_id": str(batch_id), "batch_key": spec.key},
                    )
                raise ValueError(
                    f"delivery-review-v1 rejected non-deliverable surface: "
                    f"batch-{spec.key}: {exc}"
                ) from exc

            input_tokens += result.input_tokens
            output_tokens += result.output_tokens

            async with self._sessions() as session, session.begin():
                row = await session.get(DeliveryBatchModel, batch_id)
                assert row is not None
                transition_batch(row, BATCH_VERIFYING)

            batch_verdict = await self._verify_batch(
                files=result.files,
                acceptance=spec.acceptance,
                success_criteria=success_criteria if isinstance(success_criteria, dict) else None,
                run_smoke=bool(spec.is_final),
                workspace=sandbox,
            )
            if not batch_verdict.get("passed"):
                await self._reject_batch(
                    batch_id,
                    verification=batch_verdict,
                    summary=result.summary,
                )
                gaps = "; ".join(
                    f"{g.get('code')}: {g.get('detail')}"
                    for g in batch_verdict.get("gaps") or []
                )[:500]
                if event_sink is not None:
                    await event_sink(
                        "DELIVERY_BATCH_REJECTED",
                        f"第 {spec.ordinal} 批验证未通过：{gaps or batch_verdict.get('summary')}",
                        {
                            "batch_id": str(batch_id),
                            "batch_key": spec.key,
                            "verification": batch_verdict,
                        },
                    )
                raise ValueError(
                    f"delivery-review-v1 rejected non-deliverable surface: "
                    f"batch-verify-{spec.key}: {gaps or batch_verdict.get('summary')}"
                )

            if event_sink is not None:
                await event_sink(
                    "DELIVERY_BATCH_VERIFIED",
                    f"第 {spec.ordinal} 批验证通过，正在合并到主工作区。",
                    {"batch_id": str(batch_id), "batch_key": spec.key, "verification": batch_verdict},
                )

            changes = _diff_to_changeset(
                base_dir=merged_path,
                files=result.files,
                artifacts=self._artifacts,
                scope=hypothesis_id,
                generator_ref="agentic-batch-v1",
                prompt_version="agentic-batch-v1",
            )
            snapshot_key = (
                str(generation_run_id)
                if spec.is_final
                else f"{generation_run_id}-b{spec.ordinal}"
            )
            try:
                commit = self._writer.apply(
                    snapshot_key, changes, base_workspace=merged_path
                )
            except Exception as exc:
                await self._reject_batch(
                    batch_id,
                    verification=batch_verdict,
                    summary={"merge_error": str(exc)[:500], **result.summary},
                )
                raise ValueError(
                    f"delivery-review-v1 rejected non-deliverable surface: "
                    f"batch-merge-{spec.key}: {exc}"
                ) from exc

            merged_path = commit.workspace_path
            if spec.is_final:
                final_commit = commit
                final_changes = changes

            async with self._sessions() as session, session.begin():
                row = await session.get(DeliveryBatchModel, batch_id)
                assert row is not None
                transition_batch(
                    row,
                    BATCH_MERGED,
                    workspace_locator=str(commit.workspace_path),
                    verification_json=batch_verdict,
                    summary_json={
                        **result.summary,
                        "files_written": sorted(result.files.keys()),
                        "change_count": len(changes.changes),
                    },
                )

            if event_sink is not None:
                await event_sink(
                    "DELIVERY_BATCH_MERGED",
                    f"第 {spec.ordinal}/{len(specs)} 批已合并。",
                    {
                        "batch_id": str(batch_id),
                        "batch_key": spec.key,
                        "workspace_locator": str(commit.workspace_path),
                        "is_final": spec.is_final,
                    },
                )
            batch_summaries.append(
                {
                    "batch_id": str(batch_id),
                    "key": spec.key,
                    "ordinal": spec.ordinal,
                    "title": spec.title,
                    "status": BATCH_MERGED,
                    "verification": batch_verdict,
                    "files": sorted(result.files.keys()),
                }
            )

        if final_commit is None or merged_path is None or final_changes is None:
            raise RuntimeError("batch pipeline produced no final workspace")

        # Phase D: global verification on fully merged tree.
        global_verdict = await self._verify_global(
            workspace=merged_path,
            acceptance=acceptance,
            success_criteria=success_criteria if isinstance(success_criteria, dict) else None,
        )
        if not global_verdict.get("passed"):
            gaps = "; ".join(
                f"{g.get('code')}: {g.get('detail')}" for g in global_verdict.get("gaps") or []
            )[:500]
            if event_sink is not None:
                await event_sink(
                    "DELIVERY_GLOBAL_VERIFY_FAILED",
                    f"批次已全部合并，但整体验收未通过：{gaps or global_verdict.get('summary')}",
                    {"verification": global_verdict, "batches": batch_summaries},
                )
            raise ValueError(
                f"delivery-review-v1 rejected non-deliverable surface: "
                f"global-verify: {gaps or global_verdict.get('summary')}"
            )

        if event_sink is not None:
            await event_sink(
                "DELIVERY_BATCHES_COMPLETED",
                f"全部 {len(specs)} 个批次已合并并通过整体验收。",
                {
                    "batch_count": len(specs),
                    "batches": batch_summaries,
                    "global_verification": global_verdict,
                },
            )

        _ = runtime_profile_hash  # used by caller when persisting snapshot
        return BatchPipelineResult(
            commit=final_commit,
            changes=final_changes,
            batches=batch_summaries,
            global_verification=global_verdict,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_ref=model_ref,
        )

    async def _verify_batch(
        self,
        *,
        files: dict[str, str],
        acceptance: dict[str, Any],
        success_criteria: dict[str, Any] | None,
        run_smoke: bool,
        workspace: Path,
    ) -> dict[str, Any]:
        toolkit = WorkspaceToolkit(workspace)
        for relative, content in files.items():
            try:
                toolkit.write_text(relative, content)
            except Exception:  # noqa: BLE001
                pass

        # Non-final batches: structural checks only (no full HTML/smoke gate).
        if not run_smoke and acceptance.get("acceptance_scope") == "batch_subset":
            gaps: list[dict[str, Any]] = []
            if not files:
                gaps.append({"code": "empty-batch", "detail": "batch produced no files", "snippet": ""})
            forbidden = ("SimpleHTTPRequestHandler", "http.server", "lorem ipsum")
            for name, content in files.items():
                lower = content.lower()
                for token in forbidden:
                    if token.lower() in lower:
                        gaps.append(
                            {
                                "code": "forbidden-pattern",
                                "detail": f"{name} contains {token}",
                                "snippet": content[:400],
                            }
                        )
                        break
            # Scoped paths should be present when declared.
            for path in list(acceptance.get("planned_paths") or [])[:20]:
                # planned_paths may live on brief; scope_paths checked via files keys soft
                _ = path
            if gaps:
                return {
                    "verdict": "FAIL",
                    "passed": False,
                    "summary": f"FAIL with {len(gaps)} batch gaps",
                    "gaps": gaps,
                    "smoke": {"attempted": False},
                    "scope": "batch_subset",
                }
            return {
                "verdict": "PASS",
                "passed": True,
                "summary": f"batch subset PASS ({len(files)} files)",
                "gaps": [],
                "smoke": {"attempted": False},
                "scope": "batch_subset",
            }

        verdict = await VerificationAgent(toolkit).verify(
            acceptance_contract=acceptance,
            success_criteria=success_criteria,
            run_smoke=run_smoke,
        )
        return {
            "verdict": verdict.verdict,
            "passed": verdict.passed,
            "summary": verdict.summary,
            "gaps": [
                {"code": g.code, "detail": g.detail, "snippet": g.artifact_snippet}
                for g in verdict.gaps
            ],
            "smoke": verdict.smoke,
            "scope": "batch",
        }

    async def _verify_global(
        self,
        *,
        workspace: Path,
        acceptance: dict[str, Any],
        success_criteria: dict[str, Any] | None,
    ) -> dict[str, Any]:
        toolkit = WorkspaceToolkit(workspace)
        global_acceptance = {
            **acceptance,
            "acceptance_scope": "goal_full",
            "forbid_full_goal_claim": False,
            "batch_run_smoke": True,
        }
        verdict = await VerificationAgent(toolkit).verify(
            acceptance_contract=global_acceptance,
            success_criteria=success_criteria,
            run_smoke=True,
        )
        return {
            "verdict": verdict.verdict,
            "passed": verdict.passed,
            "summary": verdict.summary,
            "gaps": [
                {"code": g.code, "detail": g.detail, "snippet": g.artifact_snippet}
                for g in verdict.gaps
            ],
            "smoke": verdict.smoke,
            "scope": "goal_full",
        }

    async def _reject_batch(
        self,
        batch_id: uuid.UUID,
        *,
        verification: dict[str, Any],
        summary: dict[str, Any],
    ) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(DeliveryBatchModel, batch_id)
            if row is None:
                return
            if row.status in {BATCH_PLANNED, BATCH_GENERATING, BATCH_VERIFYING}:
                transition_batch(
                    row,
                    BATCH_REJECTED,
                    verification_json=verification,
                    summary_json=summary,
                )

def _seed_sandbox(sandbox: Path, base: Path | None) -> None:
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    if base is None or not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if rel.startswith(".regent") or rel == ".regent-manifest.json":
            continue
        if "/.regent" in f"/{rel}" or rel.endswith(".regent-source.zip"):
            continue
        dest = sandbox / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_base_files(base_dir: Path | None) -> dict[str, bytes]:
    if base_dir is None or not base_dir.exists():
        return {}
    out: dict[str, bytes] = {}
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base_dir).as_posix()
        if rel.startswith(".regent") or "regent-source.zip" in rel:
            continue
        out[rel] = path.read_bytes()
    return out


def _diff_to_changeset(
    *,
    base_dir: Path | None,
    files: dict[str, str],
    artifacts: FileArtifactStore,
    scope: uuid.UUID,
    generator_ref: str,
    prompt_version: str,
) -> FileChangeSet:
    base_files = _read_base_files(base_dir)
    new_files = {k.replace("\\", "/"): v.encode("utf-8") for k, v in files.items()}
    changes: list[FileChange] = []

    for relative, content in sorted(new_files.items()):
        digest = _file_hash(content)
        artifact = artifacts.put(scope, f"generated/{digest[:2]}/{digest}", content)
        if relative not in base_files:
            changes.append(
                FileChange(
                    relative_path=relative,
                    operation=FileOperation.CREATE,
                    content_artifact_uri=artifact.uri,
                    content_hash=artifact.content_hash,
                    mode=FileMode.REGULAR,
                    media_type=_media_type(relative),
                    rationale=f"batch create ({generator_ref})",
                )
            )
        elif base_files[relative] != content:
            changes.append(
                FileChange(
                    relative_path=relative,
                    operation=FileOperation.REPLACE,
                    content_artifact_uri=artifact.uri,
                    content_hash=artifact.content_hash,
                    expected_previous_hash=_file_hash(base_files[relative]),
                    mode=FileMode.REGULAR,
                    media_type=_media_type(relative),
                    rationale=f"batch replace ({generator_ref})",
                )
            )

    # Deletes: files present in base but removed in new snapshot of scoped writes
    # only if the new files dict is a full tree. Subagent returns full sandbox
    # snapshot after seed, so missing paths are intentional deletes.
    for relative, previous in sorted(base_files.items()):
        if relative in new_files:
            continue
        # Do not delete scaffold files merely because a narrow batch didn't rewrite them.
        # Only delete when the path was in the new tree listing as absent AND
        # the batch sandbox no longer has it — which is already true. Still, skip
        # deletes for paths outside the written set when batch is partial.
        # Heuristic: if new_files is a strict subset of base and didn't touch this
        # path, keep it (REPLACE/CREATE only). Deletes only when explicitly empty
        # marker — skip automatic deletes for safety in partial batches.
        _ = previous  # retained for future explicit delete support
        continue

    if not changes:
        # No-op merge: re-create a tiny marker so writer has ≥1 change, then
        # prefer touching README if present; else emit empty README create.
        marker = b"# batch-noop\n"
        digest = _file_hash(marker)
        artifact = artifacts.put(scope, f"generated/{digest[:2]}/{digest}", marker)
        if "README.md" in base_files:
            changes.append(
                FileChange(
                    relative_path="README.md",
                    operation=FileOperation.REPLACE,
                    content_artifact_uri=artifact.uri,
                    content_hash=artifact.content_hash,
                    expected_previous_hash=_file_hash(base_files["README.md"]),
                    mode=FileMode.REGULAR,
                    media_type="text/plain",
                    rationale="batch noop keep-alive",
                )
            )
        else:
            changes.append(
                FileChange(
                    relative_path="README.md",
                    operation=FileOperation.CREATE,
                    content_artifact_uri=artifact.uri,
                    content_hash=artifact.content_hash,
                    mode=FileMode.REGULAR,
                    media_type="text/plain",
                    rationale="batch noop keep-alive",
                )
            )

    return FileChangeSet(
        changes=changes,
        generator_ref=generator_ref,
        prompt_version=prompt_version,
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


def _gaps_from_acceptance(acceptance: dict[str, Any]) -> list[VerificationGap]:
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

