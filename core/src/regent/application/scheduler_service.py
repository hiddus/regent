"""P2-1 multi-goal scheduler (appendix §13 minimal slice)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.external_operation_service import ExternalOperationService
from regent.application.p1_contracts import canonical_hash
from regent.application.permit_service import PermitBinding, PermitService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.scheduler_states import LedgerEntryType, QueueEntryState, ReservationState
from regent.infrastructure.models import (
    BudgetLedgerEntryModel,
    ExecutionQueueEntryModel,
    ExternalOperationModel,
    GoalModel,
    GoalPriorityPolicyModel,
    PreemptionRecordModel,
    ResourceQuotaModel,
    ResourceReservationModel,
    RunModel,
    SchedulerCheckpointModel,
    SchedulingDecisionModel,
    WorkModel,
)

SCHEDULER_EO_PROVIDER = "scheduler-dispatch-v1"

DEFAULT_POLICY_VERSION = "goal-priority-v1"
DEFAULT_PRICE_BOOK = "price-book-v1"
DEFAULT_AGING_PER_MINUTE = 1


@dataclass(frozen=True, slots=True)
class EnqueueWork:
    goal_id: uuid.UUID
    work_id: uuid.UUID | None
    org_key: str
    base_priority: int = 0
    resource_request: dict[str, int] | None = None
    actor: str = "regent-core"


@dataclass(frozen=True, slots=True)
class EnsureQuota:
    org_key: str
    resource_name: str
    limit_amount: int
    price_book_version: str = DEFAULT_PRICE_BOOK


@dataclass(frozen=True, slots=True)
class ScheduleOnce:
    org_key: str
    actor: str
    price_book_version: str = DEFAULT_PRICE_BOOK
    policy_version: str = DEFAULT_POLICY_VERSION


def compute_aging_score(
    base_priority: int,
    enqueued_at: datetime,
    *,
    now: datetime | None = None,
    aging_per_minute: int = DEFAULT_AGING_PER_MINUTE,
) -> int:
    """aging_score = base_priority + f(wait_time); stable for deterministic schedule."""
    clock = now or datetime.now(UTC)
    if enqueued_at.tzinfo is None:
        enqueued_at = enqueued_at.replace(tzinfo=UTC)
    wait_minutes = max(0, int((clock - enqueued_at).total_seconds() // 60))
    return int(base_priority) + wait_minutes * int(aging_per_minute)


class SchedulerService:
    """Enqueue, age-sort, atomically reserve, and persist replayable decisions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_policy(
        self,
        *,
        version: str = DEFAULT_POLICY_VERSION,
        aging_per_minute: int = DEFAULT_AGING_PER_MINUTE,
    ) -> GoalPriorityPolicyModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(GoalPriorityPolicyModel).where(GoalPriorityPolicyModel.version == version)
            )
            if existing is not None:
                return existing
            model = GoalPriorityPolicyModel(
                id=uuid.uuid4(),
                version=version,
                params_json={"aging_per_minute": aging_per_minute, "anti_starvation": True},
            )
            session.add(model)
            await session.flush()
            return model

    async def ensure_quota(self, command: EnsureQuota) -> ResourceQuotaModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ResourceQuotaModel).where(
                    ResourceQuotaModel.org_key == command.org_key,
                    ResourceQuotaModel.resource_name == command.resource_name,
                    ResourceQuotaModel.price_book_version == command.price_book_version,
                )
            )
            if existing is not None:
                if existing.limit_amount != command.limit_amount:
                    existing.limit_amount = command.limit_amount
                return existing
            model = ResourceQuotaModel(
                id=uuid.uuid4(),
                org_key=command.org_key,
                resource_name=command.resource_name,
                price_book_version=command.price_book_version,
                limit_amount=command.limit_amount,
                held_amount=0,
            )
            session.add(model)
            await session.flush()
            return model

    async def enqueue(self, command: EnqueueWork) -> ExecutionQueueEntryModel:
        async with self._sessions() as session, session.begin():
            if await session.get(GoalModel, command.goal_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            if command.work_id is not None and (
                await session.get(WorkModel, command.work_id) is None
            ):
                raise DomainError(ErrorCode.NOT_FOUND, "work not found")
            now = datetime.now(UTC)
            entry = ExecutionQueueEntryModel(
                id=uuid.uuid4(),
                goal_id=command.goal_id,
                work_id=command.work_id,
                org_key=command.org_key,
                status=QueueEntryState.QUEUED.value,
                base_priority=command.base_priority,
                aging_score=compute_aging_score(command.base_priority, now, now=now),
                enqueued_at=now,
                resource_request=dict(command.resource_request or {"cpu": 1}),
                metadata_json={"enqueued_by": command.actor},
            )
            session.add(entry)
            await session.flush()
            return entry

    async def schedule_once(self, command: ScheduleOnce) -> SchedulingDecisionModel:
        await self.ensure_policy(version=command.policy_version)
        async with self._sessions() as session, session.begin():
            policy = await session.scalar(
                select(GoalPriorityPolicyModel).where(
                    GoalPriorityPolicyModel.version == command.policy_version
                )
            )
            if policy is None:
                raise DomainError(ErrorCode.INVALID_STATE, "priority policy missing")
            aging_per_minute = int(
                policy.params_json.get("aging_per_minute", DEFAULT_AGING_PER_MINUTE)
            )
            now = datetime.now(UTC)

            entries = list(
                await session.scalars(
                    select(ExecutionQueueEntryModel)
                    .where(
                        ExecutionQueueEntryModel.org_key == command.org_key,
                        ExecutionQueueEntryModel.status == QueueEntryState.QUEUED.value,
                    )
                    .with_for_update()
                )
            )
            for entry in entries:
                entry.aging_score = compute_aging_score(
                    entry.base_priority,
                    entry.enqueued_at,
                    now=now,
                    aging_per_minute=aging_per_minute,
                )
            entries.sort(
                key=lambda item: (-item.aging_score, item.enqueued_at.isoformat(), str(item.id))
            )

            quotas = list(
                await session.scalars(
                    select(ResourceQuotaModel)
                    .where(
                        ResourceQuotaModel.org_key == command.org_key,
                        ResourceQuotaModel.price_book_version == command.price_book_version,
                    )
                    .with_for_update()
                )
            )
            quota_by_name = {q.resource_name: q for q in quotas}

            queue_snapshot = [
                {
                    "id": str(e.id),
                    "goal_id": str(e.goal_id),
                    "work_id": str(e.work_id) if e.work_id else None,
                    "base_priority": e.base_priority,
                    "aging_score": e.aging_score,
                    "enqueued_at": e.enqueued_at.isoformat(),
                    "resource_request": e.resource_request,
                }
                for e in entries
            ]
            quota_snapshot = [
                {
                    "resource_name": q.resource_name,
                    "limit_amount": q.limit_amount,
                    "held_amount": q.held_amount,
                    "price_book_version": q.price_book_version,
                }
                for q in sorted(quotas, key=lambda item: item.resource_name)
            ]
            input_snapshot = {
                "org_key": command.org_key,
                "policy_version": command.policy_version,
                "price_book_version": command.price_book_version,
                "decided_at": now.isoformat(),
                "queue": queue_snapshot,
                "quotas": quota_snapshot,
            }
            queue_hash = canonical_hash({"queue": queue_snapshot})
            quota_hash = canonical_hash({"quotas": quota_snapshot})

            selected: ExecutionQueueEntryModel | None = None

            for candidate in entries:
                request = {
                    str(k): int(v)
                    for k, v in dict(candidate.resource_request or {}).items()
                    if int(v) > 0
                }
                if not request:
                    request = {"cpu": 1}
                if not self._can_reserve(quota_by_name, request):
                    continue
                # Atomic multi-resource hold in this transaction
                held: list[tuple[ResourceQuotaModel, int]] = []
                ok = True
                for name, amount in sorted(request.items()):
                    quota = quota_by_name.get(name)
                    if quota is None or quota.held_amount + amount > quota.limit_amount:
                        ok = False
                        break
                    held.append((quota, amount))
                if not ok:
                    continue
                for quota, amount in held:
                    quota.held_amount += amount
                selected = candidate
                break

            decision_id = uuid.uuid4()
            if selected is None:
                output = {
                    "selected_queue_entry_id": None,
                    "reservations": [],
                    "reason": "no_schedulable_entry_or_insufficient_quota",
                }
                decision = SchedulingDecisionModel(
                    id=decision_id,
                    policy_version=command.policy_version,
                    price_book_version=command.price_book_version,
                    queue_snapshot_hash=queue_hash,
                    quota_snapshot_hash=quota_hash,
                    input_snapshot_json=input_snapshot,
                    output_json=output,
                    random_seed=None,
                    created_by=command.actor,
                )
                session.add(decision)
                await session.flush()
                return decision

            selected.status = QueueEntryState.SCHEDULED.value
            request = {
                str(k): int(v)
                for k, v in dict(selected.resource_request or {}).items()
                if int(v) > 0
            } or {"cpu": 1}
            # Persist decision before reservations (FK).
            output = {
                "selected_queue_entry_id": str(selected.id),
                "goal_id": str(selected.goal_id),
                "work_id": str(selected.work_id) if selected.work_id else None,
                "reservations": [],
                "reason": "scheduled",
            }
            decision = SchedulingDecisionModel(
                id=decision_id,
                policy_version=command.policy_version,
                price_book_version=command.price_book_version,
                queue_snapshot_hash=queue_hash,
                quota_snapshot_hash=quota_hash,
                input_snapshot_json=input_snapshot,
                output_json=output,
                random_seed=None,
                created_by=command.actor,
            )
            session.add(decision)
            await session.flush()

            reservations = []
            for name, amount in sorted(request.items()):
                reservation = ResourceReservationModel(
                    id=uuid.uuid4(),
                    queue_entry_id=selected.id,
                    scheduling_decision_id=decision.id,
                    org_key=command.org_key,
                    resource_name=name,
                    amount=amount,
                    price_book_version=command.price_book_version,
                    status=ReservationState.HELD.value,
                )
                session.add(reservation)
                reservations.append(reservation)
                session.add(
                    BudgetLedgerEntryModel(
                        id=uuid.uuid4(),
                        org_key=command.org_key,
                        price_book_version=command.price_book_version,
                        entry_type=LedgerEntryType.DEBIT.value,
                        amount=amount,
                        reason=f"reserve:{name}",
                        ref_type="resource_reservation",
                        ref_id=str(reservation.id),
                        created_by=command.actor,
                    )
                )
            decision.output_json = {
                **output,
                "reservations": [
                    {
                        "id": str(r.id),
                        "resource_name": r.resource_name,
                        "amount": r.amount,
                        "status": r.status,
                    }
                    for r in reservations
                ],
            }
            await session.flush()
            return decision

    async def release_reservation(
        self, reservation_id: uuid.UUID, *, actor: str
    ) -> ResourceReservationModel:
        async with self._sessions() as session, session.begin():
            reservation = await session.get(
                ResourceReservationModel, reservation_id, with_for_update=True
            )
            if reservation is None:
                raise DomainError(ErrorCode.NOT_FOUND, "reservation not found")
            if reservation.status != ReservationState.HELD.value:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"reservation not HELD ({reservation.status})",
                )
            quota = await session.scalar(
                select(ResourceQuotaModel)
                .where(
                    ResourceQuotaModel.org_key == reservation.org_key,
                    ResourceQuotaModel.resource_name == reservation.resource_name,
                    ResourceQuotaModel.price_book_version == reservation.price_book_version,
                )
                .with_for_update()
            )
            if quota is None:
                raise DomainError(ErrorCode.INVALID_STATE, "quota row missing")
            if quota.held_amount < reservation.amount:
                raise DomainError(ErrorCode.INVALID_STATE, "held_amount underflow")
            quota.held_amount -= reservation.amount
            reservation.status = ReservationState.RELEASED.value
            session.add(
                BudgetLedgerEntryModel(
                    id=uuid.uuid4(),
                    org_key=reservation.org_key,
                    price_book_version=reservation.price_book_version,
                    entry_type=LedgerEntryType.CREDIT.value,
                    amount=reservation.amount,
                    reason=f"release:{reservation.resource_name}",
                    ref_type="resource_reservation",
                    ref_id=str(reservation.id),
                    created_by=actor,
                )
            )
            await session.flush()
            return reservation

    async def get_decision(self, decision_id: uuid.UUID) -> SchedulingDecisionModel:
        async with self._sessions() as session:
            model = await session.get(SchedulingDecisionModel, decision_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "scheduling decision not found")
            return model

    async def list_queue(self, org_key: str) -> list[ExecutionQueueEntryModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(ExecutionQueueEntryModel)
                    .where(ExecutionQueueEntryModel.org_key == org_key)
                    .order_by(
                        ExecutionQueueEntryModel.aging_score.desc(),
                        ExecutionQueueEntryModel.enqueued_at.asc(),
                    )
                )
            )

    @staticmethod
    def _can_reserve(quota_by_name: dict[str, ResourceQuotaModel], request: dict[str, int]) -> bool:
        for name, amount in request.items():
            quota = quota_by_name.get(name)
            if quota is None:
                return False
            if quota.held_amount + amount > quota.limit_amount:
                return False
        return True

    @staticmethod
    def replay_hashes(decision: SchedulingDecisionModel) -> dict[str, str]:
        """Recompute snapshot hashes from stored input (audit replay, no side effects)."""
        snap = decision.input_snapshot_json or {}
        return {
            "queue_snapshot_hash": canonical_hash({"queue": snap.get("queue") or []}),
            "quota_snapshot_hash": canonical_hash({"quotas": snap.get("quotas") or []}),
            "matches_stored": (
                canonical_hash({"queue": snap.get("queue") or []}) == decision.queue_snapshot_hash
                and canonical_hash({"quotas": snap.get("quotas") or []})
                == decision.quota_snapshot_hash
            ),
        }

    async def list_active_org_keys(self) -> list[str]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ExecutionQueueEntryModel.org_key)
                .where(ExecutionQueueEntryModel.status == QueueEntryState.QUEUED.value)
                .distinct()
            )
            return list(rows)

    async def tick(self, *, org_key: str, actor: str = "worker:scheduler") -> dict[str, Any]:
        """Worker loop step: age+schedule → checkpoint → optional Permit for selected work."""
        decision = await self.schedule_once(ScheduleOnce(org_key=org_key, actor=actor))
        checkpoint = await self.save_checkpoint(
            org_key=org_key, decision_id=decision.id, actor=actor
        )
        selected_id = (decision.output_json or {}).get("selected_queue_entry_id")
        if not selected_id:
            return {
                "decision_id": str(decision.id),
                "checkpoint_id": str(checkpoint.id),
                "selected": False,
                "reason": (decision.output_json or {}).get("reason"),
            }
        work_raw = (decision.output_json or {}).get("work_id")
        work_id = uuid.UUID(str(work_raw)) if work_raw else None
        permit_id: str | None = None
        if work_id is not None:
            permit_id = await self._request_dispatch_permit(
                work_id=work_id,
                decision_id=decision.id,
                actor=actor,
            )
        return {
            "decision_id": str(decision.id),
            "checkpoint_id": str(checkpoint.id),
            "selected": True,
            "entry_id": str(selected_id),
            "work_id": str(work_id) if work_id else None,
            "permit_id": permit_id,
        }

    async def _request_dispatch_permit(
        self,
        *,
        work_id: uuid.UUID,
        decision_id: uuid.UUID,
        actor: str,
    ) -> str | None:
        async with self._sessions() as session:
            work = await session.get(WorkModel, work_id)
            if work is None:
                return None
            run = await session.scalar(
                select(RunModel)
                .where(
                    RunModel.work_id == work_id,
                    RunModel.status.in_(("CREATED", "PERMIT_PENDING", "QUEUED", "RUNNING")),
                )
                .order_by(RunModel.created_at.desc())
            )
            if run is None:
                return None
            goal_id = work.goal_id
            run_id = run.id
        permit_id = await PermitService(self._sessions).request(
            PermitBinding(
                goal_id=goal_id,
                work_id=work_id,
                run_id=run_id,
                actor_id=actor,
                action="scheduler.dispatch",
                target=f"work:{work_id}",
                parameters={"scheduling_decision_id": str(decision_id)},
                data_scope={"work_id": str(work_id)},
                network_scope={},
                resource_limit={},
                risk_level="LOW",
                valid_until=datetime.now(UTC) + timedelta(hours=1),
                idempotency_key=f"scheduler-dispatch:{decision_id}:{work_id}",
            )
        )
        return str(permit_id)

    async def preempt(
        self,
        *,
        org_key: str,
        queue_entry_id: uuid.UUID,
        reason: str,
        actor: str,
    ) -> PreemptionRecordModel:
        """Safe preempt: refuse when goal has DISPATCHING ExternalOperation."""
        async with self._sessions() as session, session.begin():
            victim = await session.get(
                ExecutionQueueEntryModel, queue_entry_id, with_for_update=True
            )
            if victim is None or victim.org_key != org_key:
                raise DomainError(ErrorCode.NOT_FOUND, "queue entry not found")
            if victim.status != QueueEntryState.SCHEDULED.value:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"entry not preemptible ({victim.status})",
                )
            dispatching = await session.scalar(
                select(ExternalOperationModel.id)
                .where(
                    ExternalOperationModel.goal_id == victim.goal_id,
                    ExternalOperationModel.status == "DISPATCHING",
                )
                .limit(1)
            )
            if dispatching is not None:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "unsafe preempt: ExternalOperation DISPATCHING for goal",
                )
            held = list(
                await session.scalars(
                    select(ResourceReservationModel)
                    .where(
                        ResourceReservationModel.queue_entry_id == victim.id,
                        ResourceReservationModel.status == ReservationState.HELD.value,
                    )
                    .with_for_update()
                )
            )
            reservation_id: uuid.UUID | None = None
            for reservation in held:
                quota = await session.scalar(
                    select(ResourceQuotaModel)
                    .where(
                        ResourceQuotaModel.org_key == reservation.org_key,
                        ResourceQuotaModel.resource_name == reservation.resource_name,
                        ResourceQuotaModel.price_book_version == reservation.price_book_version,
                    )
                    .with_for_update()
                )
                if quota is None:
                    raise DomainError(ErrorCode.INVALID_STATE, "quota row missing")
                if quota.held_amount < reservation.amount:
                    raise DomainError(ErrorCode.INVALID_STATE, "held_amount underflow")
                quota.held_amount -= reservation.amount
                reservation.status = ReservationState.PREEMPTED.value
                reservation_id = reservation.id
                session.add(
                    BudgetLedgerEntryModel(
                        id=uuid.uuid4(),
                        org_key=reservation.org_key,
                        price_book_version=reservation.price_book_version,
                        entry_type=LedgerEntryType.CREDIT.value,
                        amount=reservation.amount,
                        reason=f"preempt:{reservation.resource_name}",
                        ref_type="resource_reservation",
                        ref_id=str(reservation.id),
                        created_by=actor,
                    )
                )
            victim.status = QueueEntryState.QUEUED.value
            victim.metadata_json = {
                **dict(victim.metadata_json or {}),
                "last_preempt_reason": reason,
                "last_preempt_by": actor,
            }
            record = PreemptionRecordModel(
                id=uuid.uuid4(),
                queue_entry_id=victim.id,
                reservation_id=reservation_id,
                reason=reason,
                safe=True,
                created_by=actor,
            )
            session.add(record)
            await session.flush()
            return record

    async def save_checkpoint(
        self,
        *,
        org_key: str,
        decision_id: uuid.UUID,
        actor: str,
    ) -> SchedulerCheckpointModel:
        async with self._sessions() as session, session.begin():
            decision = await session.get(SchedulingDecisionModel, decision_id)
            if decision is None:
                raise DomainError(ErrorCode.NOT_FOUND, "scheduling decision not found")
            snap = decision.input_snapshot_json or {}
            if snap.get("org_key") and snap.get("org_key") != org_key:
                raise DomainError(ErrorCode.INVALID_STATE, "org_key mismatch for decision")
            model = SchedulerCheckpointModel(
                id=uuid.uuid4(),
                org_key=org_key,
                scheduling_decision_id=decision.id,
                input_snapshot_hash=canonical_hash(snap),
                created_by=actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def resume_from_checkpoint(
        self, checkpoint_id: uuid.UUID, *, actor: str
    ) -> ExecutionQueueEntryModel:
        async with self._sessions() as session, session.begin():
            cp = await session.get(SchedulerCheckpointModel, checkpoint_id)
            if cp is None:
                raise DomainError(ErrorCode.NOT_FOUND, "checkpoint not found")
            decision = await session.get(SchedulingDecisionModel, cp.scheduling_decision_id)
            if decision is None:
                raise DomainError(ErrorCode.NOT_FOUND, "decision for checkpoint missing")
            selected = (decision.output_json or {}).get("selected_queue_entry_id")
            if not selected:
                raise DomainError(ErrorCode.INVALID_STATE, "checkpoint has no selected entry")
            entry = await session.get(
                ExecutionQueueEntryModel, uuid.UUID(str(selected)), with_for_update=True
            )
            if entry is None:
                raise DomainError(ErrorCode.NOT_FOUND, "queue entry not found")
            entry.status = QueueEntryState.QUEUED.value
            entry.aging_score = max(entry.aging_score, entry.base_priority + 100)
            entry.metadata_json = {
                **dict(entry.metadata_json or {}),
                "resumed_from_checkpoint": str(checkpoint_id),
                "resumed_by": actor,
            }
            await session.flush()
            return entry

    async def list_dispatching_external_ops(
        self, *, goal_id: uuid.UUID | None = None
    ) -> list[ExternalOperationModel]:
        async with self._sessions() as session:
            stmt = select(ExternalOperationModel).where(
                ExternalOperationModel.status == "DISPATCHING"
            )
            if goal_id is not None:
                stmt = stmt.where(ExternalOperationModel.goal_id == goal_id)
            return list(
                await session.scalars(stmt.order_by(ExternalOperationModel.created_at.asc()))
            )

    # -------------------------------------------------------------------
    # P2-A: Scheduler + ExternalOperation integration
    # -------------------------------------------------------------------

    async def dispatch_with_eo(
        self,
        command: ScheduleOnce,
        *,
        operation_key: str | None = None,
        provider: str = SCHEDULER_EO_PROVIDER,
    ) -> dict[str, Any]:
        """Schedule once, then create a real ExternalOperation row (G0).

        Returns decision/eo status. Creates Permit → claim → prepare → begin_dispatch
        so the EO is DISPATCHING and visible to preempt_with_eo_check.
        """
        decision = await self.schedule_once(command)
        output = dict(decision.output_json or {})
        selected_id = output.get("selected_queue_entry_id")
        if not selected_id:
            return {
                "decision_id": str(decision.id),
                "eo_id": None,
                "status": "not_scheduled",
                "reason": output.get("reason"),
            }

        work_raw = output.get("work_id")
        goal_raw = output.get("goal_id")
        if not work_raw or not goal_raw:
            return {
                "decision_id": str(decision.id),
                "eo_id": None,
                "status": "scheduled_without_eo",
                "reason": "selected entry missing work_id/goal_id for EO binding",
                "entry_id": str(selected_id),
            }

        work_id = uuid.UUID(str(work_raw))
        goal_id = uuid.UUID(str(goal_raw))
        op_key = operation_key or f"scheduler-dispatch:{decision.id}"
        eo_provider = provider or SCHEDULER_EO_PROVIDER

        existing = await ExternalOperationService(self._sessions).get_by_operation_key(op_key)
        if existing is not None:
            await self._persist_eo_binding(
                decision_id=decision.id,
                output=output,
                eo_id=existing.id,
                operation_key=op_key,
                provider=existing.provider,
                request_digest=existing.request_digest,
            )
            return {
                "decision_id": str(decision.id),
                "eo_id": str(existing.id),
                "eo_operation_key": op_key,
                "eo_provider": existing.provider,
                "status": "dispatched_with_eo",
                "idempotent": True,
            }

        permit_id_raw = await self._request_dispatch_permit(
            work_id=work_id,
            decision_id=decision.id,
            actor=command.actor,
        )
        if permit_id_raw is None:
            return {
                "decision_id": str(decision.id),
                "eo_id": None,
                "status": "scheduled_without_eo",
                "reason": "no active run for work; cannot bind EO permit",
                "entry_id": str(selected_id),
            }

        permit_id = uuid.UUID(permit_id_raw)
        permits = PermitService(self._sessions)
        claimed = await permits.claim(permit_id, actor_id=command.actor)
        payload = {
            "decision_id": str(decision.id),
            "queue_entry_id": str(selected_id),
            "goal_id": str(goal_id),
            "work_id": str(work_id),
            "org_key": command.org_key,
        }
        eos = ExternalOperationService(self._sessions)
        prepared = await eos.prepare(
            operation_key=op_key,
            provider=eo_provider,
            action="scheduler.dispatch",
            permit_id=claimed.id,
            local_fencing_token=claimed.nonce,
            payload=payload,
            goal_id=goal_id,
            causation_id=str(decision.id),
        )
        dispatching = await eos.begin_dispatch(
            prepared.id,
            worker_lease_token=f"scheduler:{command.actor}:{decision.id}",
            expected_fencing_token=claimed.nonce,
        )
        await self._persist_eo_binding(
            decision_id=decision.id,
            output=output,
            eo_id=dispatching.id,
            operation_key=op_key,
            provider=eo_provider,
            request_digest=None,
        )
        return {
            "decision_id": str(decision.id),
            "eo_id": str(dispatching.id),
            "eo_operation_key": op_key,
            "eo_provider": eo_provider,
            "status": "dispatched_with_eo",
            "eo_status": dispatching.status,
        }

    async def _persist_eo_binding(
        self,
        *,
        decision_id: uuid.UUID,
        output: dict[str, Any],
        eo_id: uuid.UUID,
        operation_key: str,
        provider: str,
        request_digest: str | None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            decision = await session.get(SchedulingDecisionModel, decision_id)
            if decision is None:
                raise DomainError(ErrorCode.NOT_FOUND, "scheduling decision not found")
            digest = request_digest
            if digest is None:
                eo = await session.get(ExternalOperationModel, eo_id)
                digest = eo.request_digest if eo is not None else ""
            decision.output_json = {
                **output,
                "eo_binding": {
                    "eo_id": str(eo_id),
                    "operation_key": operation_key,
                    "provider": provider,
                    "request_digest": digest,
                    "bound": True,
                },
            }

    async def preempt_with_eo_check(
        self,
        *,
        org_key: str,
        target_goal_id: uuid.UUID,
        actor: str = "worker:scheduler",
        reason: str = "priority_preemption",
    ) -> dict[str, Any]:
        """Preempt a SCHEDULED entry for goal, refuse if goal has DISPATCHING EO."""
        dispatching_ops = await self.list_dispatching_external_ops(goal_id=target_goal_id)
        if dispatching_ops:
            return {
                "preempted": False,
                "reason": "target has DISPATCHING external operations",
                "blocking_ops_count": len(dispatching_ops),
                "blocking_op_ids": [str(op.id) for op in dispatching_ops],
            }

        async with self._sessions() as session:
            entry = await session.scalar(
                select(ExecutionQueueEntryModel)
                .where(
                    ExecutionQueueEntryModel.org_key == org_key,
                    ExecutionQueueEntryModel.goal_id == target_goal_id,
                    ExecutionQueueEntryModel.status == QueueEntryState.SCHEDULED.value,
                )
                .order_by(ExecutionQueueEntryModel.enqueued_at.desc())
                .limit(1)
            )
        if entry is None:
            return {
                "preempted": False,
                "reason": "no SCHEDULED queue entry for goal",
            }

        try:
            record = await self.preempt(
                org_key=org_key,
                queue_entry_id=entry.id,
                reason=reason,
                actor=actor,
            )
            return {
                "preempted": True,
                "preempted_entry_id": str(record.queue_entry_id),
                "preemption_record_id": str(record.id),
            }
        except DomainError as exc:
            return {
                "preempted": False,
                "reason": str(exc),
            }
