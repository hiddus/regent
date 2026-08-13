from __future__ import annotations

import uuid

import pytest
from regent.application.organization_experiment_service import (
    OrganizationExperimentService,
    OrganizationMutation,
    OrganizationMutationKind,
    StartOrganizationExperiment,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import OrganizationVersionModel
from regent.infrastructure.models import GoalModel, OrganizationModel


async def seed_organization(db_sessions):
    goal_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    version_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="operate",
                status="ACTIVE",
                version=0,
                created_by="test",
                correlation_id=uuid.uuid4(),
                metadata_json={},
            )
        )
        session.add(
            OrganizationModel(
                id=organization_id,
                goal_id=goal_id,
                strategy="FIXED_TEMPLATE",
                rationale="seed",
                status="ACTIVE",
                max_agents=4,
                current_version_id=version_id,
            )
        )
        session.add(
            OrganizationVersionModel(
                id=version_id,
                organization_id=organization_id,
                version=1,
                topology_json={"roles": [{"id": "pm"}]},
                status="ACTIVE",
            )
        )
    return goal_id, organization_id, version_id


def command(goal_id, organization_id, version_id, *kinds):
    return StartOrganizationExperiment(
        goal_id=goal_id,
        organization_id=organization_id,
        base_organization_version_id=version_id,
        candidate_topology={"roles": [{"id": "pm"}, {"id": "qa"}]},
        mutations=tuple(
            OrganizationMutation(kind=kind, payload={"role": "qa"}) for kind in kinds
        ),
        resource_lease_ref="reservation:shadow-1",
        actor="experimenter",
    )


@pytest.mark.asyncio
async def test_all_mutation_kinds_are_typed_and_persisted(db_sessions) -> None:
    goal_id, organization_id, version_id = await seed_organization(db_sessions)
    service = OrganizationExperimentService(db_sessions)
    experiment = await service.start(
        command(goal_id, organization_id, version_id, *tuple(OrganizationMutationKind))
    )
    assert experiment.status == "SHADOW"
    assert experiment.version == 1
    assert {row["kind"] for row in experiment.mutations_json} == {
        kind.value for kind in OrganizationMutationKind
    }
    assert experiment.rollback_organization_version_id == version_id


@pytest.mark.asyncio
async def test_shadow_must_be_evaluated_before_adoption(db_sessions) -> None:
    goal_id, organization_id, version_id = await seed_organization(db_sessions)
    service = OrganizationExperimentService(db_sessions)
    experiment = await service.start(
        command(goal_id, organization_id, version_id, OrganizationMutationKind.ADD_ROLE)
    )
    with pytest.raises(DomainError) as exc:
        await service.adopt(
            experiment.id,
            candidate_organization_version_id=version_id,
        )
    assert exc.value.code is ErrorCode.INVALID_STATE


@pytest.mark.asyncio
async def test_evaluate_then_adopt_records_candidate_and_rollback(db_sessions) -> None:
    goal_id, organization_id, version_id = await seed_organization(db_sessions)
    service = OrganizationExperimentService(db_sessions)
    experiment = await service.start(
        command(goal_id, organization_id, version_id, OrganizationMutationKind.REPLACE_MODEL)
    )
    evaluated = await service.evaluate(experiment.id, evaluation={"score": 0.8})
    assert evaluated.status == "EVALUATED"
    candidate_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            OrganizationVersionModel(
                id=candidate_id,
                organization_id=organization_id,
                version=2,
                predecessor_id=version_id,
                topology_json={"roles": [{"id": "pm"}, {"id": "qa"}]},
                status="PENDING",
            )
        )
    adopted = await service.adopt(
        experiment.id,
        candidate_organization_version_id=candidate_id,
    )
    assert adopted.status == "ADOPTED"
    assert adopted.candidate_organization_version_id == candidate_id
    assert adopted.rollback_organization_version_id == version_id


@pytest.mark.asyncio
async def test_evaluate_then_reject(db_sessions) -> None:
    goal_id, organization_id, version_id = await seed_organization(db_sessions)
    service = OrganizationExperimentService(db_sessions)
    experiment = await service.start(
        command(goal_id, organization_id, version_id, OrganizationMutationKind.RETIRE)
    )
    await service.evaluate(experiment.id, evaluation={"safe": True, "score": 0.1})
    rejected = await service.reject(experiment.id)
    assert rejected.status == "REJECTED"
