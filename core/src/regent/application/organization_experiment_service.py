"""Sandbox organization experiments with typed, auditable mutations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import OrganizationVersionModel
from regent.infrastructure.models import (
    OrganizationExperimentModel,
    OrganizationModel,
)


class OrganizationMutationKind(StrEnum):
    ADD_ROLE = "ADD_ROLE"
    SPLIT_ROLE = "SPLIT_ROLE"
    REPLACE_MODEL = "REPLACE_MODEL"
    CHANGE_EDGE = "CHANGE_EDGE"
    SPAWN_CHALLENGER = "SPAWN_CHALLENGER"
    MERGE = "MERGE"
    RETIRE = "RETIRE"


@dataclass(frozen=True)
class OrganizationMutation:
    kind: OrganizationMutationKind
    payload: dict[str, Any]


@dataclass(frozen=True)
class StartOrganizationExperiment:
    goal_id: uuid.UUID
    organization_id: uuid.UUID
    base_organization_version_id: uuid.UUID
    candidate_topology: dict[str, Any]
    mutations: tuple[OrganizationMutation, ...]
    resource_lease_ref: str
    actor: str


class OrganizationExperimentService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(
        self, command: StartOrganizationExperiment
    ) -> OrganizationExperimentModel:
        if not command.mutations:
            raise ValueError("organization experiment requires at least one mutation")
        if not command.resource_lease_ref.strip():
            raise ValueError("resource_lease_ref is required")
        if not command.candidate_topology:
            raise ValueError("candidate_topology is required")
        async with self._sessions() as session, session.begin():
            organization = await session.get(OrganizationModel, command.organization_id)
            if organization is None or organization.goal_id != command.goal_id:
                raise DomainError(ErrorCode.NOT_FOUND, "organization not found for goal")
            base = await session.get(
                OrganizationVersionModel, command.base_organization_version_id
            )
            if base is None or base.organization_id != command.organization_id:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "base organization version does not belong to organization",
                )
            latest = await session.scalar(
                select(func.max(OrganizationExperimentModel.version)).where(
                    OrganizationExperimentModel.organization_id == command.organization_id
                )
            )
            model = OrganizationExperimentModel(
                id=uuid.uuid4(),
                goal_id=command.goal_id,
                organization_id=command.organization_id,
                version=int(latest or 0) + 1,
                base_organization_version_id=command.base_organization_version_id,
                candidate_topology_json=dict(command.candidate_topology),
                mutations_json=[
                    {"kind": mutation.kind.value, "payload": dict(mutation.payload)}
                    for mutation in command.mutations
                ],
                resource_lease_ref=command.resource_lease_ref,
                execution_mode="SANDBOX",
                status="SHADOW",
                rollback_organization_version_id=command.base_organization_version_id,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def evaluate(
        self, experiment_id: uuid.UUID, *, evaluation: dict[str, Any]
    ) -> OrganizationExperimentModel:
        if not evaluation:
            raise ValueError("evaluation evidence is required")
        async with self._sessions() as session, session.begin():
            experiment = await self._get(session, experiment_id)
            self._require_status(experiment, "SHADOW")
            experiment.evaluation_json = dict(evaluation)
            experiment.status = "EVALUATED"
            experiment.evaluated_at = datetime.now(UTC)
            await session.flush()
            return experiment

    async def adopt(
        self,
        experiment_id: uuid.UUID,
        *,
        candidate_organization_version_id: uuid.UUID,
    ) -> OrganizationExperimentModel:
        async with self._sessions() as session, session.begin():
            experiment = await self._get(session, experiment_id)
            self._require_status(experiment, "EVALUATED")
            candidate = await session.get(
                OrganizationVersionModel, candidate_organization_version_id
            )
            if candidate is None or candidate.organization_id != experiment.organization_id:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "candidate organization version does not belong to experiment organization",
                )
            if candidate.id == experiment.base_organization_version_id:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT,
                    "adoption requires a new candidate organization version",
                )
            experiment.candidate_organization_version_id = candidate.id
            experiment.status = "ADOPTED"
            experiment.decided_at = datetime.now(UTC)
            await session.flush()
            return experiment

    async def reject(self, experiment_id: uuid.UUID) -> OrganizationExperimentModel:
        async with self._sessions() as session, session.begin():
            experiment = await self._get(session, experiment_id)
            self._require_status(experiment, "EVALUATED")
            experiment.status = "REJECTED"
            experiment.decided_at = datetime.now(UTC)
            await session.flush()
            return experiment

    @staticmethod
    async def _get(
        session: AsyncSession, experiment_id: uuid.UUID
    ) -> OrganizationExperimentModel:
        experiment = await session.get(OrganizationExperimentModel, experiment_id)
        if experiment is None:
            raise DomainError(ErrorCode.NOT_FOUND, "organization experiment not found")
        return experiment

    @staticmethod
    def _require_status(experiment: OrganizationExperimentModel, expected: str) -> None:
        if experiment.status != expected:
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"expected {expected}, got {experiment.status}",
            )
