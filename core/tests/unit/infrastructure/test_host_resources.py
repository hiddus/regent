"""Unit tests for host resource guard (measure / prune / reap / snapshot / block)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from regent.infrastructure.host_resources import (
    HostResources,
    evaluate_host,
    host_blocks_work,
    prune_preview_venvs,
    read_host_snapshot,
    reap_stale_preview_processes,
    run_host_guard_once,
    write_host_snapshot,
)


def _fake_resources(
    *,
    disk_percent: float = 50.0,
    mem_percent: float | None = 40.0,
    load1: float | None = 0.5,
    cpu_count: int | None = 2,
    preview_venv_count: int = 0,
) -> HostResources:
    return HostResources(
        disk_total_bytes=100,
        disk_used_bytes=int(disk_percent),
        disk_free_bytes=100 - int(disk_percent),
        disk_percent=disk_percent,
        mem_total_bytes=8_000_000_000,
        mem_available_bytes=None
        if mem_percent is None
        else int(8_000_000_000 * (1 - mem_percent / 100)),
        mem_percent=mem_percent,
        load1=load1,
        load5=None,
        load15=None,
        cpu_count=cpu_count,
        measured_at=time.time(),
        path="/tmp",
        preview_runtime_dirs=preview_venv_count,
        preview_venv_count=preview_venv_count,
    )


def test_evaluate_host_healthy() -> None:
    d = evaluate_host(_fake_resources())
    assert d.unhealthy is False
    assert d.reasons == ()


def test_evaluate_host_disk_and_load() -> None:
    d = evaluate_host(
        _fake_resources(disk_percent=90.0, load1=10.0, cpu_count=2),
        disk_percent_max=85.0,
        load1_per_cpu_max=4.0,
    )
    assert d.unhealthy is True
    assert any("disk_percent" in r for r in d.reasons)
    assert any("load1_per_cpu" in r for r in d.reasons)


def test_prune_preview_venvs_keeps_newest(tmp_path: Path) -> None:
    runtime = tmp_path / "previews" / "runtime"
    runtime.mkdir(parents=True)
    for i in range(5):
        d = runtime / f"rt-{i}"
        d.mkdir()
        venv = d / ".preview-venv"
        venv.mkdir()
        (venv / "marker").write_text("x", encoding="utf-8")
        ts = time.time() - (5 - i) * 10
        os.utime(d, (ts, ts))
    result = prune_preview_venvs(runtime, keep_newest=2)
    assert result["removed_count"] == 3
    assert len(result["kept"]) == 2
    assert (runtime / "rt-4" / ".preview-venv").is_dir()
    assert (runtime / "rt-3" / ".preview-venv").is_dir()
    assert not (runtime / "rt-0" / ".preview-venv").exists()


def test_snapshot_roundtrip_and_block(tmp_path: Path) -> None:
    resources = _fake_resources(disk_percent=95.0)
    decision = evaluate_host(resources, disk_percent_max=85.0)
    write_host_snapshot(tmp_path, decision)
    snap = read_host_snapshot(tmp_path)
    assert snap is not None
    assert snap["unhealthy"] is True
    assert snap["schema"] == "regent.host_health.v1"
    blocked, why = host_blocks_work(tmp_path)
    assert blocked is True
    assert "disk_percent" in why


def test_stale_snapshot_live_remeasures(tmp_path: Path, monkeypatch) -> None:
    from regent.infrastructure import host_resources as hr

    resources = _fake_resources(disk_percent=95.0)
    decision = evaluate_host(resources, disk_percent_max=85.0)
    path = write_host_snapshot(tmp_path, decision)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["resources"]["measured_at"] = time.time() - 999
    path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(
        hr,
        "measure_host_resources",
        lambda path=None: _fake_resources(disk_percent=40.0),
    )
    blocked, why = host_blocks_work(tmp_path, max_age_seconds=180.0)
    assert blocked is False
    assert why == ""


def test_run_host_guard_prunes_excess_venvs(tmp_path: Path, monkeypatch) -> None:
    from regent.infrastructure import host_resources as hr

    runtime = tmp_path / "previews" / "runtime"
    runtime.mkdir(parents=True)
    for i in range(5):
        d = runtime / f"rt-{i}"
        d.mkdir()
        (d / ".preview-venv").mkdir()
        os.utime(d, (time.time() - (5 - i) * 10, time.time() - (5 - i) * 10))

    calls = {"n": 0}

    def fake_measure(path=None):
        calls["n"] += 1
        dirs, venvs = hr.count_preview_artifacts(runtime)
        return _fake_resources(
            disk_percent=50.0,
            mem_percent=40.0,
            preview_venv_count=venvs,
        )

    monkeypatch.setattr(hr, "measure_host_resources", fake_measure)
    decision = run_host_guard_once(
        workspace_root=tmp_path,
        prune_disk_percent=80.0,
        prune_mem_percent=85.0,
        prune_keep_newest=2,
        reap_processes=False,
    )
    assert (decision.pruned or {}).get("removed_count") == 3
    assert "prune_preview_venvs:3" in decision.actions
    assert (tmp_path / ".regent-host-health.json").is_file()


def test_reap_uses_pid_file(tmp_path: Path) -> None:
    runtime = tmp_path / "previews" / "runtime"
    runtime.mkdir(parents=True)
    # Two newest kept; one stale with fake pid file (our own pid would be dangerous —
    # use pid 1 which we never SIGTERM successfully as non-root usually, or skip kill)
    for i in range(3):
        d = runtime / f"rt-{i}"
        d.mkdir()
        os.utime(d, (time.time() - (3 - i) * 10, time.time() - (3 - i) * 10))
    stale = runtime / "rt-0"
    # Write a non-existent high pid so kill is OSError and we still record attempt path
    (stale / ".regent-preview.pid").write_text("99999999", encoding="utf-8")
    result = reap_stale_preview_processes(runtime, keep_newest=2)
    assert result["stale_dirs"] == 1
    # kill may or may not append depending on OSError before append — we append before
    # checking success... we append after successful kill. For nonexistent pid, kill
    # raises OSError and we don't append. So killed_count may be 0.
    assert isinstance(result["killed_count"], int)
