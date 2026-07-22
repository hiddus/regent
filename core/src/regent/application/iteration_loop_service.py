"""R8 iteration loop service.

Connects PreviewDeploymentSucceeded to the Observation → GateEvaluation →
IterationDecision → (CONTINUE | REVISE | STOP) feedback loop.

For REVISE decisions, creates a new Work item and DiscoveryRound to re-enter
the execution chain.
"""

import hashlib
import json
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.execution_events import (
    DISCOVERY_ROUND_REQUESTED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.feedback_service import (
    Aggregation,
    BindMetricDefinition,
    Comparison,
    FeedbackService,
    MetricDefinition,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    ConversationMessageModel,
    ConversationModel,
    DiscoveryRoundModel,
    GoalModel,
    GoalSpecModel,
    IterationDecisionModel,
    WorkModel,
)

logger = logging.getLogger(__name__)


class IterationLoopService:
    """Orchestrates the R8 observation → decision → iteration loop."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._feedback = FeedbackService(sessions)

    async def bind_default_metrics(
        self,
        goal_id: uuid.UUID,
        deployment_id: uuid.UUID,
        *,
        actor: str = "regent-core",
    ) -> list[uuid.UUID]:
        """Bind default P1 metric definitions after a successful deployment.

        Creates a minimal set of metric bindings for real external observations:
        - task_completion_count: at least 1 real user task completion
        Internal smoke observations must not satisfy these gates.
        """
        binding_ids: list[uuid.UUID] = []

        default_metrics = [
            MetricDefinition(
                metric_key="task_completion_count",
                definition_version="v1",
                observation_source="product-analytics",
                value_field="value",
                aggregation=Aggregation.COUNT,
                comparison=Comparison.GTE,
                threshold=1.0,
                minimum_samples=1,
                exclude_bots=True,
                exclude_internal=True,
            ),
        ]

        for metric in default_metrics:
            try:
                model = await self._feedback.bind_metric(
                    BindMetricDefinition(
                        goal_id=goal_id,
                        deployment_id=deployment_id,
                        definition=metric,
                        actor=actor,
                    )
                )
                binding_ids.append(model.id)
            except DomainError:
                logger.info(
                    "metric already bound",
                    extra={"goal_id": str(goal_id), "metric": metric.metric_key},
                )

        return binding_ids

    async def handle_revise(
        self,
        iteration_decision_id: uuid.UUID,
        *,
        actor: str = "regent-core",
    ) -> uuid.UUID:
        """Handle a REVISE iteration decision by creating a new DiscoveryRound.

        Returns the new DiscoveryRound ID.
        """
        async with self._sessions() as session, session.begin():
            decision = await session.get(IterationDecisionModel, iteration_decision_id)
            if decision is None:
                raise DomainError(ErrorCode.NOT_FOUND, "iteration decision not found")
            if decision.decision != "REVISE":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"expected REVISE decision, got {decision.decision}",
                )

            goal_id = decision.goal_id
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            if goal.status != "ACTIVE":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"goal must be ACTIVE for REVISE, got {goal.status}",
                )

            project_id = goal.app_project_id
            if project_id is None:
                raise DomainError(ErrorCode.INVALID_STATE, "goal has no app project")

            project = await session.get(AppProjectModel, project_id)
            if project is None or project.status != "ACTIVE":
                raise DomainError(ErrorCode.INVALID_STATE, "app project is not ACTIVE")

            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            if spec is None or spec.status != "FROZEN":
                raise DomainError(ErrorCode.INVALID_STATE, "goal spec is not FROZEN")

            # Create new Work item for the revision
            work_id = uuid.uuid4()
            session.add(
                WorkModel(
                    id=work_id,
                    goal_id=goal_id,
                    purpose=f"REVISE: {decision.primary_hypothesis}",
                    input_refs=[
                        {
                            "type": "iteration_decision",
                            "id": str(decision.id),
                            "primary_hypothesis": decision.primary_hypothesis,
                        }
                    ],
                    acceptance_criteria={
                        "revision_of": str(decision.id),
                        "primary_hypothesis": decision.primary_hypothesis,
                    },
                    dependency_ids=[],
                    priority=100,
                    budget={"max_model_calls": 10},
                    correlation_id=goal.correlation_id,
                    metadata_json={
                        "revision": True,
                        "iteration_decision_id": str(decision.id),
                    },
                )
            )

            # Create new DiscoveryRound
            idempotency_key = make_idempotency_key(
                "revise-discovery", goal_id, str(decision.id)
            )
            next_round = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(DiscoveryRoundModel.round), 0)).where(
                            DiscoveryRoundModel.goal_id == goal_id
                        )
                    )
                    or 0
                )
                + 1
            )

            snapshot = {
                "goal_id": str(goal.id),
                "goal_version": goal.version,
                "spec_version": spec.version,
                "constraints": spec.explicit_constraints,
                "success_criteria": spec.success_criteria,
                "revision": True,
                "iteration_decision_id": str(decision.id),
                "primary_hypothesis": decision.primary_hypothesis,
            }
            snapshot_hash = hashlib.sha256(
                json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()

            discovery_round = DiscoveryRoundModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                round=next_round,
                status="REQUESTED",
                version=0,
                input_snapshot_hash=snapshot_hash,
                budget={"max_sources": 5, "max_tokens": 50_000},
                policy_version="discovery-v1",
                idempotency_key=idempotency_key,
                created_by=actor,
                correlation_id=str(goal.correlation_id),
            )
            session.add(discovery_round)

            # Emit DiscoveryRoundRequested
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=DISCOVERY_ROUND_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "discovery_round_id": str(discovery_round.id),
                        "round": next_round,
                        "actor": actor,
                        "idempotency_key": idempotency_key,
                        "revision": True,
                        "iteration_decision_id": str(decision.id),
                    },
                    idempotency_key=idempotency_key,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)

            # Append conversation event
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.app_project_id == project_id
                )
            )
            if conversation is not None:
                last = await session.scalar(
                    select(ConversationMessageModel.ordinal)
                    .where(
                        ConversationMessageModel.conversation_id == conversation.id
                    )
                    .order_by(ConversationMessageModel.ordinal.desc())
                    .limit(1)
                )
                session.add(
                    ConversationMessageModel(
                        id=uuid.uuid4(),
                        conversation_id=conversation.id,
                        ordinal=(last or 0) + 1,
                        role="EVENT",
                        message_type="REVISE_ITERATION",
                        content=(
                            f"REVISE decision triggered. "
                            f"Starting new discovery round {next_round}."
                        ),
                        metadata_json={
                            "goal_id": str(goal_id),
                            "iteration_decision_id": str(decision.id),
                            "discovery_round_id": str(discovery_round.id),
                            "primary_hypothesis": decision.primary_hypothesis,
                        },
                        created_by="regent-core",
                    )
                )

            await session.flush()
            logger.info(
                "REVISE iteration: new discovery round created",
                extra={
                    "goal_id": str(goal_id),
                    "decision_id": str(decision.id),
                    "round_id": str(discovery_round.id),
                    "round": next_round,
                },
            )
            return discovery_round.id
