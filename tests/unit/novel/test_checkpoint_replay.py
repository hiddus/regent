from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from regent.novel.application import direction
from regent.novel.application.directing_protocol import (
    DIRECTED_ARCHITECTURES,
    production_protocol,
)
from regent.novel.application.production import logical_call_key


@pytest.fixture(scope="module")
def checkpoint_contract() -> dict:
    path = (
        Path(__file__).parents[2]
        / "fixtures"
        / "novel"
        / "checkpoints"
        / "v1"
        / "manifest.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_four_executor_protocol_contract(checkpoint_contract: dict) -> None:
    for expected in checkpoint_contract["executors"].values():
        run = SimpleNamespace(
            generation_context={"architecture_version": expected["architecture"]}
        )
        assert expected["architecture"] in DIRECTED_ARCHITECTURES
        assert production_protocol(run) == expected["protocol"]


def test_command_and_logical_call_contract(checkpoint_contract: dict) -> None:
    sp = {
        "scene_index": 0,
        "scene_plan": {"cards": [{"scene_id": "s1"}]},
        "scene_repairs:s1": 2,
        "creative_repairs": 1,
    }
    production = {
        "scene_index": 1,
        "takes": [
            {
                "take_no": 2,
                "turn": 3,
                "revisions": 1,
                "round_actions": [],
            }
        ],
        "decisions": [{}],
        "script_protocol": sp,
        # 冻结的 v1 checkpoint 契约：旧调用键不含 vrep0
        "call_key_version": 1,
    }
    run = SimpleNamespace(input_version=4)
    scene_id = direction._command_id(production, run, "VALIDATE")
    script_id = direction._script_scene_command_id(
        run, production, sp, "WRITE_SCENE"
    )
    assert scene_id == checkpoint_contract["command_contracts"]["scene_validate"]
    assert (
        script_id
        == checkpoint_contract["command_contracts"]["script_scene_write"]
    )
    assert (
        logical_call_key(
            "run1", scene_id, "", "scene1:take2:VALIDATE"
        )
        == checkpoint_contract["command_contracts"]["logical_call"]
    )


def test_legacy_checkpoint_defaults_to_scene(checkpoint_contract: dict) -> None:
    assert production_protocol({}) == checkpoint_contract["checkpoint_contracts"][
        "legacy_json"
    ]["default_protocol"]


def test_checkpoint_manifest_freezes_acceptance_fields(
    checkpoint_contract: dict,
) -> None:
    frozen = set(checkpoint_contract["immutable_fields"])
    assert "production.phase" in frozen
    assert "production.calls[].purpose" in frozen
    assert "run.input_version" in frozen
    assert "run.generation_context.validated_content_hash" in frozen
