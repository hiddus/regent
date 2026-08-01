"""Unit tests for GenerationService planned_paths expand persistence."""

from __future__ import annotations

import uuid

from regent.application.generation_service import GenerationService
from regent.infrastructure.models import GenerationPlanModel


def test_expand_plan_contract_persists_scaffold_paths() -> None:
    plan = GenerationPlanModel(
        id=uuid.uuid4(),
        requirement_revision_id=uuid.uuid4(),
        capability_resolution_plan_id=uuid.uuid4(),
        status="FROZEN",
        version=1,
        input_digest="a" * 64,
        contract_json={
            "planned_paths": ["src/app.py"],
            "acceptance_contract": {"goal_scale": "SMALL"},
        },
        architecture_summary="x",
        component_plan=[],
        created_by="test",
        correlation_id="c",
    )
    GenerationService._expand_plan_contract(plan)
    paths = plan.contract_json["planned_paths"]
    assert "src/app.py" in paths
    assert "requirements.txt" in paths
    assert "static/style.css" in paths
    assert plan.version == 2


def test_expand_plan_contract_idempotent() -> None:
    plan = GenerationPlanModel(
        id=uuid.uuid4(),
        requirement_revision_id=uuid.uuid4(),
        capability_resolution_plan_id=uuid.uuid4(),
        status="FROZEN",
        version=1,
        input_digest="b" * 64,
        contract_json={
            "planned_paths": ["src/app.py"],
            "acceptance_contract": {"goal_scale": "SMALL"},
        },
        architecture_summary="x",
        component_plan=[],
        created_by="test",
        correlation_id="c",
    )
    GenerationService._expand_plan_contract(plan)
    version = plan.version
    GenerationService._expand_plan_contract(plan)
    assert plan.version == version
