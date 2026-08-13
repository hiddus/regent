"""Apply host-resource decisions onto ACTIVE goals (soft-pause burn)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.infrastructure.host_resources import HostGuardDecision
from regent.infrastructure.models import GoalModel

logger = logging.getLogger(__name__)


async def soft_pause_active_goals_for_host(
    sessions: async_sessionmaker[AsyncSession],
    *,
    decision: HostGuardDecision,
    limit: int = 32,
) -> dict[str, Any]:
    """Stamp ops_soft_pause on ACTIVE goals so generation/deploy loops stop burning."""
    if not decision.unhealthy:
        return {"paused": 0, "goal_ids": []}
    reason = "; ".join(decision.reasons[:4]) or "host_unhealthy"
    paused: list[str] = []
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(GoalModel)
                .where(GoalModel.status == "ACTIVE")
                .order_by(GoalModel.updated_at.desc())
                .limit(limit)
            )
        ).all()
        for goal in rows:
            meta = dict(goal.metadata_json or {})
            existing = meta.get("ops_soft_pause")
            if isinstance(existing, dict) and existing.get("gap_kind") == "HOST_RESOURCE":
                continue
            stage = str(meta.get("execution_stage") or "")
            # Don't clobber ACHIEVED-adjacent; only interrupt burn paths.
            if stage in {"ACHIEVED", "COMPLETE"}:
                continue
            meta["execution_stage"] = "DELIVERY_SOFT_PAUSE"
            meta["awaiting_human_intervention"] = False
            meta["delivery_gap_kind"] = "HOST_RESOURCE"
            meta["delivery_gap_reasons"] = [
                f"HOST_RESOURCE: {reason}",
                "Host auto-guard soft-paused generation/preview to protect the machine.",
            ]
            meta["ops_soft_pause"] = {
                "at": datetime.now(UTC).isoformat(),
                "reason": "host_resource_guard",
                "gap_kind": "HOST_RESOURCE",
                "attempts": int(meta.get("delivery_gap_total_attempts") or 0),
                "host_reasons": list(decision.reasons[:6]),
            }
            meta["session_steer_brief"] = (
                "【主机资源熔断】磁盘/内存/负载过高，Regent 已自动 soft-pause。"
                "环境恢复后发送 CONTINUE 再跑；不要在主机不健康时空转。"
            )[:4000]
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
            paused.append(str(goal.id))
        if paused:
            await session.commit()
    if paused:
        logger.warning(
            "host guard soft-paused active goals",
            extra={"count": len(paused), "reason": reason},
        )
    return {"paused": len(paused), "goal_ids": paused, "reason": reason}


async def tick_host_resource_guard(
    sessions: async_sessionmaker[AsyncSession] | None,
    *,
    workspace_root: str,
    disk_percent_max: float,
    mem_percent_max: float,
    load1_per_cpu_max: float,
    prune_keep_newest: int,
    prune_disk_percent: float,
    prune_mem_percent: float = 85.0,
    reap_processes: bool = True,
) -> dict[str, Any]:
    from regent.infrastructure.host_resources import run_host_guard_once

    decision = run_host_guard_once(
        workspace_root=workspace_root,
        disk_percent_max=disk_percent_max,
        mem_percent_max=mem_percent_max,
        load1_per_cpu_max=load1_per_cpu_max,
        prune_keep_newest=prune_keep_newest,
        prune_disk_percent=prune_disk_percent,
        prune_mem_percent=prune_mem_percent,
        reap_processes=reap_processes,
    )
    out: dict[str, Any] = {"decision": decision.as_dict()}
    if decision.unhealthy and sessions is not None:
        out["soft_pause"] = await soft_pause_active_goals_for_host(
            sessions, decision=decision
        )
    return out
