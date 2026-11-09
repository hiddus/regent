"""Execute certified Hive PM→Dev→QA around agentic generation runs.

App-project Goals previously only *offered* durable AgentTasks; this module
claims and completes them so multi-agent is real on the product path.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.agent_task_service import AgentTaskService
from regent.application.hive_runtime import HiveTaskChain
from regent.infrastructure.models import GoalModel
from regent.model.provider import ModelProvider

logger = logging.getLogger(__name__)

# Generation wall can be ~15m; keep Dev lease ahead of that with heartbeats.
GENERATION_HIVE_LEASE_SECONDS = 1800


class GenerationHivePmPlan(BaseModel):
    """PM output injected into the Dev AgentRunner session."""

    execution_plan: list[str] = Field(default_factory=list)
    acceptance_focus: list[str] = Field(default_factory=list)
    # Concrete, testable criteria — not slogans / outline bullets.
    detailed_acceptance_criteria: list[str] = Field(default_factory=list)
    multi_agent_work_split: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    progress_summary: str = Field(min_length=1)


class GenerationHiveQaReview(BaseModel):
    accepted: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    gaps: list[str] = Field(default_factory=list)
    # Structure-only Hive QA must leave content verification to Live Preview.
    pending_live_verification: bool = True


class GenerationHiveLiveContentReview(BaseModel):
    """Product+Tech live review after Preview is up (authoritative for content)."""

    accepted: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    gaps: list[str] = Field(default_factory=list)
    product_notes: list[str] = Field(default_factory=list)
    tech_notes: list[str] = Field(default_factory=list)


@dataclass
class GenerationHiveHandle:
    chain: HiveTaskChain
    tasks: AgentTaskService
    actor: str
    goal_id: uuid.UUID
    generation_run_id: uuid.UUID
    pm_plan: dict[str, Any] | None = None
    _dev_lease_token: str | None = None
    _dev_worker: str = ""
    _qa_done: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def _note(self, role: str, status: str, **extra: Any) -> None:
        self.events.append({"role": role, "status": status, **extra})


async def begin_generation_hive(
    sessions: async_sessionmaker[AsyncSession],
    *,
    provider: ModelProvider,
    goal_id: uuid.UUID,
    generation_run_id: uuid.UUID,
    chain: HiveTaskChain,
    actor: str,
    goal_input: str,
    acceptance_hints: dict[str, Any] | None = None,
) -> GenerationHiveHandle:
    """Run PM, inject steer into goal metadata, claim+start Dev."""
    tasks = AgentTaskService(sessions, lease_seconds=GENERATION_HIVE_LEASE_SECONDS)
    handle = GenerationHiveHandle(
        chain=chain,
        tasks=tasks,
        actor=actor,
        goal_id=goal_id,
        generation_run_id=generation_run_id,
    )
    worker_pm = f"{actor}:hive-pm"
    worker_dev = f"{actor}:hive-dev"
    handle._dev_worker = worker_dev

    if chain.pm_task is not None:
        try:
            pm_lease = await tasks.claim_task(chain.pm_task.id, worker_id=worker_pm)
            await tasks.start_task(
                chain.pm_task.id, lease_token=pm_lease.lease_token or ""
            )
            pm_resp = await provider.generate_structured(
                system_prompt=(
                    "You are the Product (PM) agent in a certified Regent durable hive "
                    "(pm-dev-independent-qa-v1). You own goal attainment detail — NOT outlines. "
                    "Produce an execution plan for Dev AND detailed_acceptance_criteria with "
                    "concrete, testable items (API paths, min counts, field substance, journeys). "
                    "Each detailed_acceptance_criteria item must be verifiable on Live Preview. "
                    "Emphasize multi-agent domain design when the goal involves "
                    "country-level and pairwise crosswalk maintainers. "
                    "For compliance/catalog Crosswalk goals, acceptance MUST require: "
                    "US and SG each >=10 rule points with title+statute/source+obligation/risk "
                    "where obligation text is operable handbook detail (not a one-line slogan); "
                    "US-SG and SG-US each >=10 handbook steps with trigger+action+evidence+owner+priority "
                    "with enough prose to execute; "
                    "Live Preview API self-check before claiming done; "
                    "Product/Tech/Test/UX Delivery Role Swarm must pass. "
                    "Do not write product code. Do not accept shell-only or outline-only demos."
                ),
                user_prompt=json.dumps(
                    {
                        "goal": goal_input,
                        "acceptance_hints": acceptance_hints or {},
                        "generation_run_id": str(generation_run_id),
                        "hard_content_gates": {
                            "min_country_points": 10,
                            "min_crosswalk_steps": 10,
                            "min_obligation_chars": 80,
                            "min_step_action_chars": 40,
                            "required_point_fields": [
                                "title",
                                "statute|source",
                                "obligations|body|scenario",
                                "risk",
                            ],
                            "required_step_fields": [
                                "trigger",
                                "action|check",
                                "evidence",
                                "owner",
                                "priority",
                            ],
                            "delivery_roles_required": [
                                "product",
                                "tech",
                                "test",
                                "ux",
                                "ops",
                            ],
                            "delivery_roles_template": "delivery-roles-v1",
                            "forbid_outline_only": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                response_model=GenerationHivePmPlan,
            )
            handle.pm_plan = pm_resp.output.model_dump()
            await _inject_pm_plan_into_goal(
                sessions,
                goal_id=goal_id,
                generation_run_id=generation_run_id,
                pm_plan=handle.pm_plan,
            )
            await tasks.complete_task(
                chain.pm_task.id,
                lease_token=pm_lease.lease_token or "",
                result_ref=f"gen:{generation_run_id}:pm",
            )
            handle._note("pm", "SUCCEEDED", summary=handle.pm_plan.get("progress_summary"))
        except Exception:
            logger.warning(
                "generation hive PM phase failed (non-fatal)",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
            handle._note("pm", "FAILED")

    try:
        dev_lease = await tasks.claim_task(chain.dev_task.id, worker_id=worker_dev)
        await tasks.start_task(
            chain.dev_task.id, lease_token=dev_lease.lease_token or ""
        )
        handle._dev_lease_token = dev_lease.lease_token
        handle._note("dev", "RUNNING")
    except Exception:
        logger.warning(
            "generation hive Dev claim failed (non-fatal)",
            extra={"goal_id": str(goal_id)},
            exc_info=True,
        )
        handle._note("dev", "CLAIM_FAILED")

    return handle


async def heartbeat_generation_hive_dev(handle: GenerationHiveHandle) -> None:
    if not handle._dev_lease_token:
        return
    try:
        view = await handle.tasks.heartbeat(
            handle.chain.dev_task.id,
            lease_token=handle._dev_lease_token,
            worker_id=handle._dev_worker,
        )
        handle._dev_lease_token = view.lease_token or handle._dev_lease_token
    except Exception:
        logger.debug(
            "generation hive Dev heartbeat skipped",
            extra={"goal_id": str(handle.goal_id)},
            exc_info=True,
        )


async def complete_generation_hive_dev(
    handle: GenerationHiveHandle,
    *,
    ok: bool,
) -> None:
    if not handle._dev_lease_token:
        # Still cancel orphaned QA if Dev never claimed but generation failed.
        if not ok:
            await _cancel_orphaned_qa(handle, reason="generation_failed_before_dev_claim")
        return
    result_ref = f"gen:{handle.generation_run_id}:dev"
    try:
        if ok:
            await handle.tasks.complete_task(
                handle.chain.dev_task.id,
                lease_token=handle._dev_lease_token,
                result_ref=result_ref,
            )
            handle._note("dev", "SUCCEEDED")
        else:
            await handle.tasks.fail_task(
                handle.chain.dev_task.id,
                lease_token=handle._dev_lease_token,
                error_code="GENERATION_FAILED",
                retryable=True,
            )
            handle._note("dev", "FAILED_RETRYABLE")
            await _cancel_orphaned_qa(handle, reason="generation_failed")
    except Exception:
        logger.warning(
            "generation hive Dev complete failed",
            extra={"goal_id": str(handle.goal_id)},
            exc_info=True,
        )
        handle._note("dev", "COMPLETE_FAILED")
        if not ok:
            await _cancel_orphaned_qa(handle, reason="generation_failed_after_dev_complete_error")


async def _cancel_orphaned_qa(handle: GenerationHiveHandle, *, reason: str) -> None:
    if handle._qa_done:
        return
    try:
        await handle.tasks.cancel_task(handle.chain.qa_task.id, reason=reason)
        handle._note("qa", "CANCELLED", reason=reason)
        handle._qa_done = True
    except Exception:
        logger.debug(
            "generation hive QA cancel skipped",
            extra={"goal_id": str(handle.goal_id), "reason": reason},
            exc_info=True,
        )


async def run_generation_hive_qa(
    handle: GenerationHiveHandle,
    *,
    sessions: async_sessionmaker[AsyncSession],
    provider: ModelProvider,
    goal_input: str,
    generation_summary: str,
) -> GenerationHiveQaReview | None:
    if handle._qa_done:
        return None
    worker_qa = f"{handle.actor}:hive-qa"
    try:
        qa_lease = await handle.tasks.claim_task(
            handle.chain.qa_task.id, worker_id=worker_qa
        )
        await handle.tasks.start_task(
            handle.chain.qa_task.id, lease_token=qa_lease.lease_token or ""
        )
        qa_resp = await provider.generate_structured(
            system_prompt=(
                "You are the independent STRUCTURE QA agent in a certified Regent hive. "
                "Judge ONLY scaffolding: app entry, templates/static, domain/seed modules. "
                "You do NOT certify product content depth or Live Preview APIs. "
                "Set pending_live_verification=true always for catalog/compliance goals. "
                "Set accepted=true only when structure markers look sufficient for Dev handoff; "
                "list residual structure gaps. "
                "NEVER claim content depth (points/steps) is verified from file paths alone. "
                "If seed.py/domain modules are missing for a content goal, accepted=false. "
                "You must not be the producer."
            ),
            user_prompt=json.dumps(
                {
                    "goal": goal_input,
                    "pm_plan": handle.pm_plan,
                    "generation_summary": generation_summary[:4000],
                    "review_scope": "structure_only",
                },
                ensure_ascii=False,
            ),
            response_model=GenerationHiveQaReview,
        )
        review = qa_resp.output
        # Hard invariant: structure QA cannot close live content verification.
        if not review.pending_live_verification:
            review = review.model_copy(update={"pending_live_verification": True})
        await handle.tasks.complete_task(
            handle.chain.qa_task.id,
            lease_token=qa_lease.lease_token or "",
            result_ref=f"gen:{handle.generation_run_id}:qa",
        )
        handle._qa_done = True
        handle._note(
            "qa",
            "SUCCEEDED",
            accepted=review.accepted,
            score=review.score,
            pending_live_verification=review.pending_live_verification,
        )
        await _stamp_qa_on_goal(
            sessions,
            goal_id=handle.goal_id,
            generation_run_id=handle.generation_run_id,
            review=review.model_dump(),
            hive_events=handle.events,
        )
        return review
    except Exception:
        logger.warning(
            "generation hive QA phase failed (non-fatal)",
            extra={"goal_id": str(handle.goal_id)},
            exc_info=True,
        )
        handle._note("qa", "FAILED")
        return None


def decide_live_content_review(live_qa: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Product+Tech verdict from Live Preview QA (no LLM)."""
    checks = list((live_qa or {}).get("checks") or [])
    failed = [c for c in checks if isinstance(c, dict) and not c.get("passed")]
    failed_names = [str(c.get("name") or "") for c in failed]
    mechanical_passed = bool((live_qa or {}).get("passed"))
    if not mechanical_passed:
        return {
            "accepted": False,
            "score": 0.25,
            "reason": (
                "Live Preview product QA failed — Hive refuses content acceptance. "
                f"Failed checks: {', '.join(failed_names[:8]) or 'unknown'}."
            ),
            "gaps": failed_names[:12],
            "product_notes": [
                "内容/导航/深度未过 Live Preview 门槛，禁止以结构 QA 放行。",
            ],
            "tech_notes": [
                str(c.get("detail") or c.get("name") or "")[:240] for c in failed[:6]
            ],
            "source": "deterministic_live_preview_qa",
        }
    return {
        "accepted": True,
        "score": 0.9,
        "reason": (
            "Live Preview product QA passed (home/nav/style/content-depth). "
            "Hive Product+Tech accept content for this deployment."
        ),
        "gaps": [],
        "product_notes": [
            "Live Preview 深度与导航已过机械门槛。",
        ],
        "tech_notes": [
            str(c.get("detail") or "")[:200]
            for c in checks
            if str(c.get("name") or "") == "preview-content-depth"
        ],
        "source": "deterministic_live_preview_qa",
    }


async def run_hive_live_content_review(
    sessions: async_sessionmaker[AsyncSession],
    *,
    provider: ModelProvider | None,
    goal_id: uuid.UUID,
    goal_input: str,
    preview_url: str,
    live_qa: dict[str, Any],
) -> dict[str, Any]:
    """Product+Tech content review against Live Preview QA evidence (authoritative)."""
    review = decide_live_content_review(live_qa)

    # Optional LLM enrichment when provider available — never override fail→pass.
    if review.get("accepted") and provider is not None:
        try:
            llm = await provider.generate_structured(
                system_prompt=(
                    "You are combined Product+Tech reviewers after Live Preview QA. "
                    "Mechanical QA already passed. Confirm residual product/tech risks. "
                    "You may lower score and add gaps, but if you set accepted=false "
                    "you must cite concrete gaps. Do not invent API failures."
                ),
                user_prompt=json.dumps(
                    {
                        "goal": goal_input[:6000],
                        "preview_url": preview_url,
                        "live_qa": live_qa,
                    },
                    ensure_ascii=False,
                )[:12000],
                response_model=GenerationHiveLiveContentReview,
            )
            out = llm.output
            review = {
                "accepted": bool(out.accepted),
                "score": float(out.score),
                "reason": out.reason,
                "gaps": list(out.gaps)[:12],
                "product_notes": list(out.product_notes)[:8],
                "tech_notes": list(out.tech_notes)[:8],
                "source": "live_preview_qa+llm",
            }
        except Exception:
            logger.warning(
                "hive live content LLM review failed; keeping deterministic pass",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )

    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is not None:
            meta = dict(goal.metadata_json or {})
            hive = dict(meta.get("hive_generation") or {})
            hive["phase"] = "live_content_review_done"
            hive["live_content_review"] = review
            # Overturn prior structure-only accept when live rejects.
            if not review.get("accepted"):
                hive["content_gate"] = "REJECTED_BY_LIVE"
                prev = dict(hive.get("qa_review") or {})
                if prev.get("accepted"):
                    prev["superseded_by_live"] = True
                    prev["pending_live_verification"] = True
                    hive["qa_review"] = prev
            else:
                hive["content_gate"] = "ACCEPTED_BY_LIVE"
            meta["hive_generation"] = hive
            meta["hive_live_content_review"] = review
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
    return review


async def _inject_pm_plan_into_goal(
    sessions: async_sessionmaker[AsyncSession],
    *,
    goal_id: uuid.UUID,
    generation_run_id: uuid.UUID,
    pm_plan: dict[str, Any],
) -> None:
    lines = [
        "【Hive PM 计划 — 多 Agent 执行】",
        str(pm_plan.get("progress_summary") or "").strip(),
    ]
    for label, key in (
        ("执行步骤", "execution_plan"),
        ("验收焦点", "acceptance_focus"),
        ("多 Agent 分工", "multi_agent_work_split"),
        ("风险", "risk_notes"),
    ):
        items = pm_plan.get(key) or []
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items[:12])
    steer = "\n".join(line for line in lines if line).strip()
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return
        meta = dict(goal.metadata_json or {})
        meta["session_steer_brief"] = steer[:4000]
        meta["hive_generation"] = {
            "generation_run_id": str(generation_run_id),
            "pm_plan": pm_plan,
            "template_id": "pm-dev-independent-qa-v1",
            "phase": "dev_running",
        }
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")


async def _stamp_qa_on_goal(
    sessions: async_sessionmaker[AsyncSession],
    *,
    goal_id: uuid.UUID,
    generation_run_id: uuid.UUID,
    review: dict[str, Any],
    hive_events: list[dict[str, Any]],
) -> None:
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return
        meta = dict(goal.metadata_json or {})
        hive = dict(meta.get("hive_generation") or {})
        hive.update(
            {
                "generation_run_id": str(generation_run_id),
                "qa_review": review,
                "events": hive_events[-20:],
                "phase": "qa_done",
            }
        )
        meta["hive_generation"] = hive
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
