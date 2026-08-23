"""FileChangeSetGenerator adapter for agentic-generation-v1."""

from __future__ import annotations

import hashlib
import json
import logging
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
    ArtifactIncompleteError,
    BudgetExhaustedError,
    PlanApproveRequiredError,
    VerificationGap,
)
from regent.application.agent_control import (
    AskUserRequiredError,
    ToolPermissionRequiredError,
    UserAbortError,
    get_execution_mode,
    session_always_tools,
)
from regent.application.delivery_rejection import DeliveryRejection
from regent.application.p1_contracts import FileChange, FileChangeSet, FileMode, FileOperation
from regent.application.p1_ports import GeneratedFileChangeSet
from regent.domain.errors import ErrorCode
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.sandbox import build_agent_sandbox
from regent.model import ModelProvider, ModelTruncatedError, ToolCallInvalidError
from regent.agent.accepted_workspace import write_accepted_workspace_snapshot
from regent.agent.runtime_profile_v1 import parse_runtime_profile_v1

logger = logging.getLogger(__name__)

GENERATOR_REF = "agentic-generation-v1"
PROMPT_VERSION = "agentic-generation-v1"


class AgenticCodeGenerator:
    """Multi-turn agent generator implementing FileChangeSetGenerator."""

    generator_type = "agentic"
    generator_ref = GENERATOR_REF
    prompt_version = PROMPT_VERSION

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

    def _resolve_sandbox(self, plan: dict[str, Any], run_id: str) -> tuple[Path, bool]:
        """Prefer ProjectAgentSession workspace; never rmtree that root.

        Returns (sandbox_path, used_session_workspace).
        """
        acceptance = dict(plan.get("acceptance_contract") or {})
        raw = (
            plan.get("project_agent_session_workspace_uri")
            or acceptance.get("project_agent_session_workspace_uri")
            or ""
        )
        raw = str(raw).strip()
        if raw:
            return Path(raw), True
        return self._workspace_root / "agentic" / run_id, False

    async def generate(
        self,
        plan: dict[str, Any],
        *,
        on_progress: Callable[[Any], Awaitable[None]] | None = None,
    ) -> GeneratedFileChangeSet:
        run_id = str(plan.get("generation_run_id") or uuid.uuid4())
        sandbox, used_session_workspace = self._resolve_sandbox(plan, run_id)
        base_workspace = _resolve_base(plan)
        if used_session_workspace:
            _ensure_session_workspace(sandbox, base_workspace)
        else:
            _prepare_sandbox(sandbox, base_workspace)
        base_files = _read_tree_bytes(sandbox) if (
            base_workspace is not None or used_session_workspace
        ) else {}

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

        toolkit = WorkspaceToolkit(sandbox, command_sandbox=build_agent_sandbox())
        prior_gaps = _gaps_from_plan(plan)
        acceptance = dict(plan.get("acceptance_contract") or {})
        # Flatten Session resume fields onto plan root for AgentRunner seeding (I-C).
        for key in (
            "project_agent_session_id",
            "project_agent_session_epoch",
            "project_agent_session_workspace_uri",
            "session_resume_brief",
            "session_prior_messages",
            "work_plan_approved",
            "skip_plan_approve",
            "work_plan_trivial",
            "authorized_session_resume",
            "work_plan_seen",
            "work_plan_items",
            "session_steer_brief",
            "active_corrections",
            "latest_goal_spec_version",
            "failure_envelopes",
            "execution_mode",
            "permission_always_tools",
            "permission_allow_once_tools",
            "goal_metadata",
        ):
            if key not in plan and acceptance.get(key) is not None:
                plan[key] = acceptance[key]
        # Default execution_mode ask; merge permission grants from acceptance.
        if "execution_mode" not in plan:
            plan["execution_mode"] = get_execution_mode(acceptance)
        if "permission_always_tools" not in plan:
            plan["permission_always_tools"] = sorted(session_always_tools(acceptance))
        # Ship-first: when first-plan approve is off, auto-stamp approve + grant
        # write/shell tools so the loop does not burn the token budget on ASK.
        from regent.config import get_settings as _get_settings

        if not bool(getattr(_get_settings(), "agent_plan_approve_on_first", True)):
            plan["work_plan_approved"] = True
            plan["skip_plan_approve"] = True
            plan["work_plan_seen"] = True
            always = set(plan.get("permission_always_tools") or ())
            always.update({"write_file", "edit_file", "run_command"})
            plan["permission_always_tools"] = sorted(always)
        elif acceptance:
            merged = set(plan.get("permission_always_tools") or ())
            merged.update(session_always_tools(acceptance))
            plan["permission_always_tools"] = sorted(merged)
        if "goal_metadata" not in plan:
            plan["goal_metadata"] = {
                k: acceptance[k]
                for k in (
                    "agent_abort_requested",
                    "execution_mode",
                    "agent_permission_always",
                    "turn_checkpoint_log",
                    "work_plan_progress_loop",
                    "regent_events",
                    "regent_quarantine",
                    "quarantine_active",
                    "workspace_root",
                )
                if k in acceptance
            }
        run_smoke = bool(acceptance.get("batch_run_smoke", True))
        context_artifacts = None
        execution_plans = None
        goal_uuid = _maybe_uuid(plan.get("goal_id"))
        runtime_run_uuid = _maybe_uuid(plan.get("run_id"))
        if self._sessions is not None and goal_uuid is not None:
            from regent.application.budget_ledger import BudgetLedger
            from regent.application.context_artifact import ContextArtifactService
            from regent.application.execution_plan import ExecutionPlanService

            context_artifacts = ContextArtifactService(
                self._sessions, artifact_root=self._artifacts.root
            )
            execution_plans = ExecutionPlanService(self._sessions)
            budget_ledger = BudgetLedger(self._sessions)
        else:
            budget_ledger = None
        context_window = 128_000
        max_subagent_depth = 1
        model_max_output_tokens = 8192
        model_input_cost_per_million = 0.0
        model_output_cost_per_million = 0.0
        price_book_version = "model-price-book-v1"
        try:
            from regent.config import get_settings

            _settings = get_settings()
            context_window = int(_settings.agent_context_window_tokens)
            max_subagent_depth = int(_settings.max_subagent_depth)
            model_max_output_tokens = int(_settings.model_max_output_tokens or 8192)
            model_input_cost_per_million = float(_settings.model_input_cost_per_million)
            model_output_cost_per_million = float(_settings.model_output_cost_per_million)
            price_book_version = str(_settings.model_price_book_version)
        except Exception:
            context_window = int(plan.get("context_window_tokens") or 128_000)
        if plan.get("context_window_tokens"):
            context_window = int(plan["context_window_tokens"])
        if plan.get("max_subagent_depth") is not None:
            max_subagent_depth = int(plan["max_subagent_depth"])
        # P0-1: resolve tiered budget per-run based on goal_scale.
        run_budget = self._budget
        goal_scale = str(
            (plan.get("acceptance_contract") or {}).get("goal_scale")
            or (plan.get("goal_metadata") or {}).get("goal_scale")
            or ""
        ).upper()
        if goal_scale in ("SMALL", "MEDIUM", "LARGE"):
            from regent.config import budget_for_goal_scale

            tier_turns, tier_tokens = budget_for_goal_scale(goal_scale)
            run_budget = AgentBudget(
                max_turns=min(tier_turns, self._budget.max_turns),
                max_tokens=min(tier_tokens, self._budget.max_tokens),
                max_wall_seconds=self._budget.max_wall_seconds,
            )
        runner = AgentRunner(
            self._provider,
            toolkit,
            budget=run_budget,
            regent_md=regent_md,
            context_window_tokens=context_window,
            context_artifacts=context_artifacts,
            execution_plans=execution_plans,
            goal_id=goal_uuid,
            run_id=runtime_run_uuid,
            producer_ref=GENERATOR_REF,
            runtime_profile=plan.get("runtime_profile"),
            skills_enabled=bool(plan.get("skills_enabled", True)),
            execution_mode=str(plan.get("execution_mode") or "ask"),
            permission_always_tools=set(plan.get("permission_always_tools") or ()),
            max_subagent_depth=max_subagent_depth,
            budget_ledger=budget_ledger,
            model_max_output_tokens=model_max_output_tokens,
            model_input_cost_per_million=model_input_cost_per_million,
            model_output_cost_per_million=model_output_cost_per_million,
            price_book_version=price_book_version,
        )

        from regent.agent.progress_event import ProgressEvent

        async def _on_turn(turn: int, summary: str) -> None:
            if on_progress is not None:
                await on_progress(
                    ProgressEvent(
                        summary=summary,
                        type="turn_start",
                        turn=turn if turn >= 0 else None,
                    )
                )

        async def _on_event(event: dict[str, Any]) -> None:
            # Bridge AgentRunner structured events without Chinese-string round-trip.
            if on_progress is None:
                return
            etype = str(event.get("type") or "status")
            tool = str(event.get("tool") or "") or None
            summary = str(event.get("summary") or "")
            if not summary:
                if tool:
                    preview = str(event.get("args_preview") or "")
                    summary = f"执行工具 {tool}" + (f"：{preview}" if preview else "")
                else:
                    summary = etype
            await on_progress(
                ProgressEvent(
                    summary=summary[:240],
                    type=etype,
                    turn=int(event["turn"]) if event.get("turn") is not None else None,
                    tool=tool,
                    args_preview=str(event["args_preview"]) if event.get("args_preview") else None,
                    result_preview=(
                        str(event["result_preview"]) if event.get("result_preview") else None
                    ),
                    detail=str(event["detail"]) if event.get("detail") else None,
                    input_tokens=(
                        int(event["input_tokens"]) if event.get("input_tokens") is not None else None
                    ),
                    output_tokens=(
                        int(event["output_tokens"])
                        if event.get("output_tokens") is not None
                        else None
                    ),
                    cached_tokens=(
                        int(event["cached_tokens"])
                        if event.get("cached_tokens") is not None
                        else None
                    ),
                )
            )

        try:
            if on_progress is not None:
                await on_progress(ProgressEvent(summary="正在启动多轮生成…", type="status"))
            result = await runner.run(
                plan,
                prior_gaps=prior_gaps,
                verify=True,
                run_smoke=run_smoke,
                on_turn=_on_turn if on_progress else None,
                on_event=_on_event if on_progress else None,
            )
            await _flush_goal_metadata_overlays(
                self._sessions,
                goal_uuid,
                dict(plan.get("goal_metadata") or {}),
                workspace_root=str(toolkit.root),
            )
        except UserAbortError as exc:
            draft_uri = sandbox.resolve().as_uri()
            raise DeliveryRejection(
                reasons=[f"USER_ABORT: {exc.reason}"],
                draft_uri=draft_uri,
                producer_ref=GENERATOR_REF,
                gap_kind="USER_ABORT",
                message="user aborted agent loop",
            ) from exc
        except ToolPermissionRequiredError as exc:
            draft_uri = sandbox.resolve().as_uri()
            raise DeliveryRejection(
                reasons=[
                    f"TOOL_PERMISSION_REQUIRED: {exc.tool_name}",
                    f"preview:{exc.args_preview}",
                ],
                draft_uri=draft_uri,
                producer_ref=GENERATOR_REF,
                gap_kind="TOOL_PERMISSION",
                message="tool permission required",
            ) from exc
        except AskUserRequiredError as exc:
            draft_uri = sandbox.resolve().as_uri()
            reasons = [f"ASK_USER_REQUIRED: {exc.question[:200]}"]
            envelope = dict(exc.envelope or {})
            if envelope.get("ask_type"):
                reasons.append(f"ask_type:{envelope['ask_type']}")
            if envelope.get("blocked_item_key"):
                reasons.append(f"blocked_item:{envelope['blocked_item_key']}")
            if envelope.get("gap_kind"):
                reasons.append(f"gap_kind_hint:{envelope['gap_kind']}")
            raise DeliveryRejection(
                reasons=reasons,
                draft_uri=draft_uri,
                producer_ref=GENERATOR_REF,
                gap_kind="ASK_USER",
                message="agent asked the human a question",
            ) from exc
        except PlanApproveRequiredError as exc:
            draft_uri = sandbox.resolve().as_uri()
            raise DeliveryRejection(
                reasons=[
                    "PLAN_APPROVE_REQUIRED: work plan awaiting human approve",
                    *[
                        f"plan:{item.get('id')}:{item.get('content')}"
                        for item in (exc.items or [])[:8]
                    ],
                ],
                draft_uri=draft_uri,
                producer_ref=GENERATOR_REF,
                gap_kind="PLAN_APPROVE",
                message="work plan needs human approve before write tools",
            ) from exc
        except (BudgetExhaustedError, ArtifactIncompleteError) as exc:
            from regent.config import get_settings
            from regent.agent.agent_runner import AgentRunResult
            from regent.agent.types import VerificationVerdict

            gates_mode = str(
                getattr(get_settings(), "delivery_product_gates_mode", "soft") or "soft"
            ).lower()
            soft_files = _text_files_from_sandbox(sandbox)
            # Ship-first: soft/off + runnable draft → deliver changeset (preview path)
            # instead of ASK_HUMAN burn loops on ARTIFACT_INCOMPLETE / BUDGET_EXHAUSTED.
            if (
                gates_mode in {"soft", "off"}
                and soft_files
                and _soft_draft_looks_runnable(soft_files)
            ):
                logger.warning(
                    "soft draft auto-deliver after %s (ship-first)",
                    type(exc).__name__,
                    extra={"file_count": len(soft_files), "gates_mode": gates_mode},
                )
                try:
                    from regent.agent.accepted_workspace import (
                        write_recoverable_workspace_snapshot,
                    )

                    write_recoverable_workspace_snapshot(
                        sandbox,
                        Path(get_settings().workspace_root),
                        reason=f"soft_auto_deliver_{type(exc).__name__.lower()}",
                        include_diagnostics=True,
                    )
                except Exception:
                    pass
                result = AgentRunResult(
                    files=soft_files,
                    model_ref="agentic-soft-draft",
                    input_tokens=0,
                    output_tokens=0,
                    submitted=False,
                    verification=VerificationVerdict(
                        verdict="FAIL",
                        summary=(
                            f"soft auto-deliver after {type(exc).__name__}; "
                            "preview soft-pass path (A0: not verified COMPLETE)"
                        ),
                        gaps=[],
                    ),
                )
            elif (
                gates_mode in {"soft", "off"}
                and soft_files
                and isinstance(exc, ArtifactIncompleteError)
            ):
                logger.warning(
                    "soft draft retained after %s (A0: not COMPLETE)",
                    type(exc).__name__,
                    extra={"file_count": len(soft_files), "gates_mode": gates_mode},
                )
                draft_uri = sandbox.resolve().as_uri()
                try:
                    from regent.agent.accepted_workspace import (
                        write_recoverable_workspace_snapshot,
                    )

                    draft_uri = write_recoverable_workspace_snapshot(
                        sandbox,
                        Path(get_settings().workspace_root),
                        reason=f"soft_draft_{type(exc).__name__.lower()}",
                        include_diagnostics=True,
                    )
                except Exception:
                    pass
                raise DeliveryRejection(
                    reasons=[
                        f"ARTIFACT_INCOMPLETE: soft draft retained ({len(soft_files)} files); "
                        "needs human confirm before continue (A0)"
                    ],
                    draft_uri=draft_uri,
                    producer_ref=GENERATOR_REF,
                    gap_kind="ARTIFACT_INCOMPLETE",
                )
            elif isinstance(exc, BudgetExhaustedError):
                # M1-4: diagnostics already on disk; never promote / never count as success.
                if not (sandbox / ".regent_budget_exhausted.json").exists():
                    (sandbox / ".regent_budget_exhausted.json").write_text(
                        json.dumps(
                            exc.diagnostic_manifest
                            or {
                                "primary_failure_code": "BUDGET_EXHAUSTED",
                                "reason": exc.reason,
                                "promote_allowed": False,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                # Promote sandbox → recoverable snapshot before sandbox can be reaped.
                draft_uri = sandbox.resolve().as_uri()
                try:
                    from regent.agent.accepted_workspace import (
                        write_recoverable_workspace_snapshot,
                    )

                    draft_uri = write_recoverable_workspace_snapshot(
                        sandbox,
                        Path(get_settings().workspace_root),
                        reason="budget_exhausted",
                        include_diagnostics=True,
                    )
                except Exception:
                    pass
                raise DeliveryRejection(
                    reasons=[f"BUDGET_EXHAUSTED: {exc.reason}"],
                    draft_uri=draft_uri,
                    producer_ref=GENERATOR_REF,
                    gap_kind="BUDGET_EXHAUSTED",
                    message="agent budget exhausted; diagnostics retained, promote forbidden",
                ) from exc
            else:
                raise DeliveryRejection(
                    reasons=[f"ARTIFACT_INCOMPLETE: {exc.reason}"],
                    draft_uri=sandbox.resolve().as_uri(),
                    producer_ref=GENERATOR_REF,
                    gap_kind="ARTIFACT_INCOMPLETE",
                    message="agent did not submit a complete artifact",
                ) from exc
        except (ModelTruncatedError, ToolCallInvalidError) as exc:
            # Persist whatever turns we have before fail-closed — otherwise FAILED
            # runs leave agent_transcripts empty and ops cannot diagnose TOOL_CALL_INVALID.
            partial = getattr(exc, "transcript", None)
            if partial is None and "result" in locals():
                partial = getattr(result, "transcript", None)
            if partial:
                try:
                    _write_transcript_sidecar(sandbox, partial)
                    generation_run_id = plan.get("generation_run_id")
                    if generation_run_id:
                        await self._transcripts.persist(
                            generation_run_id=uuid.UUID(str(generation_run_id)),
                            turns=partial,
                        )
                except Exception:
                    logger.exception(
                        "partial transcript persist failed after model tool error",
                        extra={"sandbox": str(sandbox)},
                    )
            code = getattr(exc, "failure_code", "UNKNOWN")
            raise DeliveryRejection(
                reasons=[f"{code}: {exc}"],
                draft_uri=sandbox.resolve().as_uri(),
                producer_ref=GENERATOR_REF,
                gap_kind=str(code),
                message=f"agent model failure ({code})",
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
            except Exception as exc:
                # CD-0.2: transcript loss must not be silent. Sidecar is already
                # written to disk (audit trail preserved); block the delivery
                # instead of continuing as if nothing happened.
                logger.exception(
                    "agent transcript DB persist failed; sidecar retained on disk",
                    extra={
                        "generation_run_id": str(generation_run_id),
                        "sandbox": str(sandbox),
                    },
                )
                raise DeliveryRejection(
                    reasons=[f"transcript-persist-failed: {exc}"[:400]],
                    draft_uri=sandbox.resolve().as_uri(),
                    producer_ref=GENERATOR_REF,
                    code=ErrorCode.TRANSCRIPT_PERSIST_FAILED,
                    retryable=True,
                    message="transcript DB persist failed; sidecar retained",
                ) from exc

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
            logger.exception(
                "project memory distill failed (best-effort; delivery continues)",
                extra={"goal_id": str(plan.get("goal_id") or "")},
            )

        planned = set(plan.get("planned_paths") or [])
        hyp = plan.get("hypothesis_decision_id") or plan.get("generation_run_id")
        if not hyp:
            hyp = uuid.uuid4()
        scope = uuid.UUID(str(hyp))

        if result.verification is not None and not result.verification.passed:
            from regent.config import get_settings
            from regent.application.delivery_success_policy import (
                partition_delivery_gap_codes,
            )

            gates_mode = str(
                getattr(get_settings(), "delivery_product_gates_mode", "soft") or "soft"
            ).lower()
            gap_codes = [g.code for g in (result.verification.gaps or [])]
            blocking_codes, soft_codes = partition_delivery_gap_codes(gap_codes)
            # Soft/off: retain draft but never COMPLETE (A0) — always ASK via rejection.
            soft_only = (
                gates_mode in {"soft", "off"}
                and bool(result.files)
                and not blocking_codes
            )
            draft_note = _persist_verification_draft(
                workspace_root=self._workspace_root,
                sandbox=sandbox,
                goal_id=plan.get("goal_id"),
                run_id=run_id,
                files=result.files,
            )
            draft_changes = 0
            try:
                draft_changes = len(
                    _materialize_incremental_changes(
                        base_files=base_files,
                        files=result.files,
                        planned=planned,
                        artifacts=self._artifacts,
                        scope=scope,
                    )
                )
            except Exception:  # noqa: BLE001 — draft artifacts best-effort
                logger.exception("draft artifact materialize failed (best-effort)")
                draft_changes = 0
            reject_reasons = [
                f"verification-agent: {reason}" for reason in gaps[:8]
            ] or [f"verification-agent: {result.verification.summary}"]
            if soft_only:
                reject_reasons = [
                    f"verification-soft: {c}" for c in soft_codes[:8]
                ] or reject_reasons
                logger.warning(
                    "soft draft after verification FAIL (A0: ASK not COMPLETE)",
                    extra={
                        "soft_gaps": soft_codes[:12],
                        "file_count": len(result.files),
                        "gates_mode": gates_mode,
                    },
                )
            reject_reasons.append(
                f"draft_files={len(result.files)} draft_artifacts={draft_changes}"
            )
            primary = str(
                (result.verification.smoke or {})
                .get("stages", {})
                .get("primary_failure_code")
                or (
                    soft_codes[0]
                    if soft_only and soft_codes
                    else (
                        result.verification.gaps[0].code
                        if result.verification.gaps
                        else "VERIFICATION_FAILED"
                    )
                )
            )
            # Ship-first: soft-only FAIL with runnable app → deliver for preview.
            if soft_only and _soft_draft_looks_runnable(result.files):
                logger.warning(
                    "soft verification FAIL auto-deliver runnable draft (ship-first)",
                    extra={
                        "soft_gaps": soft_codes[:12],
                        "file_count": len(result.files),
                        "primary": primary,
                        "gates_mode": gates_mode,
                    },
                )
            else:
                raise DeliveryRejection(
                    reasons=reject_reasons,
                    draft_uri=draft_note or None,
                    producer_ref=GENERATOR_REF,
                    gap_kind=primary,
                )

        # M2/M4-2: atomic accepted_workspace_snapshot after successful verification.
        accepted_meta: dict[str, Any] | None = None
        if result.verification is not None and result.verification.passed:
            try:
                profile = parse_runtime_profile_v1(
                    dict(plan.get("runtime_profile") or {})
                )
                profile_hash = profile.content_hash if profile else "none"
                verification_hash = str(
                    (result.verification.smoke or {})
                    .get("stages", {})
                    .get("verification_hash")
                    or ""
                )
                snap = write_accepted_workspace_snapshot(
                    sandbox,
                    self._workspace_root,
                    profile_hash=profile_hash,
                    verification_hash=verification_hash or "unhashed",
                )
                accepted_meta = snap.as_dict()
                (sandbox / ".regent_accepted_workspace.json").write_text(
                    json.dumps(accepted_meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:  # noqa: BLE001 — snapshot failure must not invent success
                logger.exception("accepted workspace snapshot failed")
                raise DeliveryRejection(
                    reasons=["accepted_workspace_snapshot_failed"],
                    draft_uri=sandbox.resolve().as_uri(),
                    producer_ref=GENERATOR_REF,
                    gap_kind="ARTIFACT_INCOMPLETE",
                )

        changes = _materialize_incremental_changes(
            base_files=base_files,
            files=result.files,
            planned=planned,
            artifacts=self._artifacts,
            scope=scope,
        )
        if not changes and result.files:
            from regent.config import get_settings

            gates_mode = str(
                getattr(get_settings(), "delivery_product_gates_mode", "soft") or "soft"
            ).lower()
            # Soft/off: planned-path filter must not erase a non-empty agent product.
            # Session resume often rewrites identical files → empty delta; still deliver.
            if gates_mode in {"soft", "off"}:
                logger.warning(
                    "soft-pass materialize force-include (planned/identical)",
                    extra={
                        "file_count": len(result.files),
                        "planned_count": len(planned),
                        "gates_mode": gates_mode,
                    },
                )
                changes = _materialize_incremental_changes(
                    base_files=base_files,
                    files=result.files,
                    planned=set(),
                    artifacts=self._artifacts,
                    scope=scope,
                    include_identical=True,
                )
        if not changes:
            raise DeliveryRejection(
                reasons=["empty-changeset: no materializable files after planned-path filter"],
                gap_kind="empty-changeset",
                producer_ref=GENERATOR_REF,
            )

        return GeneratedFileChangeSet(
            output=FileChangeSet(
                changes=changes,
                generator_ref=GENERATOR_REF,
                prompt_version=PROMPT_VERSION,
            ),
            model_ref=result.model_ref or "agentic",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            accepted_workspace=accepted_meta,
        )


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _persist_verification_draft(
    *,
    workspace_root: Path,
    sandbox: Path,
    goal_id: Any,
    run_id: str,
    files: dict[str, str],
) -> str:
    """Copy rejected-but-present agent output to a durable draft workspace."""
    import shutil

    if not files:
        return ""
    key = str(goal_id or run_id or "unknown")
    draft = workspace_root / "agentic_drafts" / key / run_id
    try:
        if draft.exists():
            shutil.rmtree(draft, ignore_errors=True)
        draft.mkdir(parents=True, exist_ok=True)
        if sandbox.exists():
            for path in sorted(sandbox.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(sandbox).as_posix()
                if rel.startswith(".regent"):
                    continue
                dest = draft / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        else:
            for rel, content in files.items():
                dest = draft / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        marker = draft / ".regent-verification-draft.json"
        marker.write_text(
            json.dumps(
                {
                    "goal_id": str(goal_id or ""),
                    "run_id": run_id,
                    "file_count": len(files),
                    "status": "verification_rejected",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return str(draft)
    except Exception:  # noqa: BLE001 — never block the rejection path
        logger.exception("verification draft persist failed (best-effort)")
        return ""


def _resolve_base(plan: dict[str, Any]) -> Path | None:
    raw = plan.get("base_workspace")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.exists() else None


def _ensure_session_workspace(sandbox: Path, base: Path | None) -> None:
    """Bind AgentRunner to the durable session tree — never wipe the root."""
    import shutil

    sandbox.mkdir(parents=True, exist_ok=True)
    if base is None or not base.exists():
        return
    if base.resolve() == sandbox.resolve():
        return
    # First boot only: seed from revise/accepted baseline when session is empty.
    has_files = any(p.is_file() for p in sandbox.rglob("*"))
    if has_files:
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


def _text_files_from_sandbox(sandbox: Path) -> dict[str, str]:
    """Collect UTF-8 product files from sandbox for soft-rescue delivery."""
    out: dict[str, str] = {}
    if not sandbox.exists():
        return out
    for path in sorted(sandbox.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(sandbox).as_posix()
        if rel.startswith(".regent") or "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        if not rel.lower().endswith(
            (
                ".html",
                ".css",
                ".js",
                ".py",
                ".md",
                ".txt",
                ".json",
                ".toml",
                ".yml",
                ".yaml",
                ".svg",
                ".sql",
            )
        ):
            continue
        try:
            out[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _soft_draft_looks_runnable(files: dict[str, str]) -> bool:
    """True when soft draft has a likely web/app entry worth previewing."""
    if not files:
        return False
    names = {str(k).replace("\\", "/").lower() for k in files}
    has_py = any(n.endswith(".py") for n in names)
    has_html = any(n.endswith(".html") for n in names)
    sample = "\n".join(str(v)[:2500] for v in list(files.values())[:24]).lower()
    framework = any(
        token in sample
        for token in (
            "flask",
            "fastapi",
            "from flask",
            "uvicorn",
            "@app.route",
            "app = flask",
            "http.server",
        )
    )
    return bool(has_py and (framework or has_html))


def _materialize_incremental_changes(
    *,
    base_files: dict[str, bytes],
    files: dict[str, str],
    planned: set[str],
    artifacts: FileArtifactStore,
    scope: uuid.UUID,
    include_identical: bool = False,
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
            if not include_identical:
                continue
            op = FileOperation.REPLACE
            prev_hash = digest
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
    from regent.application.planned_path_policy import is_allowed_extra_path

    return is_allowed_extra_path(relative)


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


_GOAL_METADATA_OVERLAY_KEYS = (
    "turn_checkpoint_log",
    "work_plan_progress_loop",
    "regent_events",
    "regent_quarantine",
    "quarantine_active",
    "last_side_question",
    "last_trust_posture",
)


async def _flush_goal_metadata_overlays(
    sessions: async_sessionmaker[AsyncSession] | None,
    goal_id: uuid.UUID | None,
    overlays: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> None:
    """Persist O-axis overlays from the agent plan back onto Goal.metadata_json."""
    if sessions is None or goal_id is None or not overlays:
        return
    from sqlalchemy.orm.attributes import flag_modified

    from regent.infrastructure.models import GoalModel

    patch = {k: overlays[k] for k in _GOAL_METADATA_OVERLAY_KEYS if k in overlays}
    if workspace_root:
        patch["workspace_root"] = workspace_root
    if not patch:
        return
    try:
        async with sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
            meta.update(patch)
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
    except Exception:
        logging.getLogger(__name__).warning(
            "failed to flush goal metadata overlays",
            extra={"goal_id": str(goal_id)},
            exc_info=True,
        )
