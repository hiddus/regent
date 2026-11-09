"""Tests for evolvable environment-heal memory + action preference."""

from __future__ import annotations

import os
import time
from pathlib import Path

from regent.application.environment_heal_memory import (
    load_heal_memory,
    record_heal_outcome,
    read_ops_lessons,
)
from regent.application.harness_evolution import map_gaps_to_skills
from regent.infrastructure.environment_heal_registry import (
    list_heal_actions,
    run_selected_heal_actions,
)
from regent.infrastructure.host_resources import HostResources, run_host_guard_once


def _resources(*, venvs: int = 5, disk: float = 50.0) -> HostResources:
    return HostResources(
        disk_total_bytes=100,
        disk_used_bytes=int(disk),
        disk_free_bytes=100 - int(disk),
        disk_percent=disk,
        mem_total_bytes=8_000_000_000,
        mem_available_bytes=4_000_000_000,
        mem_percent=50.0,
        load1=0.2,
        load5=None,
        load15=None,
        cpu_count=2,
        measured_at=time.time(),
        path="/tmp",
        preview_runtime_dirs=venvs,
        preview_venv_count=venvs,
    )


def test_list_heal_actions_allowlisted() -> None:
    ids = {a["id"] for a in list_heal_actions()}
    assert "prune_preview_venvs" in ids
    assert "reap_stale_previews" in ids


def test_record_heal_outcome_grows_lessons(tmp_path: Path) -> None:
    memory = record_heal_outcome(
        tmp_path,
        reasons_before=["disk_percent=93.0 >= 85.0"],
        action_ids=["prune_preview_venvs", "reap_stale_previews"],
        improved=True,
        metrics_before={"disk_percent": 93.0, "mem_percent": 90.0, "preview_venv_count": 40},
        metrics_after={"disk_percent": 50.0, "mem_percent": 70.0, "preview_venv_count": 8},
    )
    assert memory["preferences"]
    assert memory["preferences"][0]["reason_prefix"] == "disk_percent"
    lessons = read_ops_lessons(tmp_path)
    assert "prune_preview_venvs" in lessons
    assert "MUST" in lessons
    # Idempotent marker
    record_heal_outcome(
        tmp_path,
        reasons_before=["disk_percent=93.0 >= 85.0"],
        action_ids=["prune_preview_venvs", "reap_stale_previews"],
        improved=True,
        metrics_before={"disk_percent": 93.0, "mem_percent": 90.0, "preview_venv_count": 40},
        metrics_after={"disk_percent": 50.0, "mem_percent": 70.0, "preview_venv_count": 8},
    )
    assert lessons.count("<!-- heal:disk_percent") == read_ops_lessons(tmp_path).count(
        "<!-- heal:disk_percent"
    )


def test_registry_prefers_memory_order(tmp_path: Path, monkeypatch) -> None:
    from regent.infrastructure import environment_heal_registry as reg

    calls: list[str] = []

    def fake_reap(ws, ctx):
        calls.append("reap")
        return {"killed_count": 1}

    def fake_prune(ws, ctx):
        calls.append("prune")
        return {"removed_count": 2}

    # Patch repair callables on builtin actions via monkeypatch on module functions used by closures
    monkeypatch.setattr(reg, "_repair_reap", fake_reap)
    monkeypatch.setattr(reg, "_repair_prune", fake_prune)
    # Rebuild actions with patched repairs
    from regent.infrastructure.environment_heal_registry import HealAction

    actions = (
        HealAction(
            id="reap_stale_previews",
            title="r",
            risk_tier="medium",
            description="d",
            detect=lambda resources, ctx: True,
            repair=fake_reap,
        ),
        HealAction(
            id="prune_preview_venvs",
            title="p",
            risk_tier="low",
            description="d",
            detect=lambda resources, ctx: True,
            repair=fake_prune,
        ),
    )
    monkeypatch.setattr(reg, "BUILTIN_HEAL_ACTIONS", actions)
    run_selected_heal_actions(
        workspace_root=tmp_path,
        resources=_resources(venvs=20),
        ctx={"prune_keep_newest": 8},
        memory_prefs=[
            {
                "reason_prefix": "disk_percent",
                "prefer_actions": ["prune_preview_venvs", "reap_stale_previews"],
                "successes": 2,
            }
        ],
    )
    assert calls[0] == "prune"


def test_host_gaps_map_to_ops_environment() -> None:
    mapped = map_gaps_to_skills(
        ["HOST_RESOURCE", "disk_percent=93.0 >= 85.0", "preview_venv_count=72"]
    )
    assert "ops-environment" in mapped


def test_run_host_guard_learns_from_excess_venvs(tmp_path: Path) -> None:
    runtime = tmp_path / "previews" / "runtime"
    runtime.mkdir(parents=True)
    for i in range(5):
        d = runtime / f"rt-{i}"
        d.mkdir()
        (d / ".preview-venv").mkdir()
        os.utime(d, (time.time() - (5 - i) * 10, time.time() - (5 - i) * 10))
    decision = run_host_guard_once(
        workspace_root=tmp_path,
        prune_keep_newest=2,
        prune_disk_percent=80.0,
        prune_mem_percent=85.0,
        reap_processes=False,
    )
    assert (decision.pruned or {}).get("removed_count") == 3
    mem = load_heal_memory(tmp_path)
    assert mem.get("incidents")
    assert "prune_preview_venvs" in read_ops_lessons(tmp_path) or mem.get("preferences")
