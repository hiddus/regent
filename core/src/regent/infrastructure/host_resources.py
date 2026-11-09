"""Host resource measurement, preview GC, orphan reap, and health snapshots.
Regent historically only had DB/outbox health and manual ops scripts. Overnight
preview deploy loops filled disk with ``.preview-venv`` trees and left many
Flask preview processes alive on a 1.6Gi host until kswapd thrash stalled
SSH/API. This module is the in-process detect + repair path.
"""
from __future__ import annotations
import json
import logging
import os
import signal
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
SNAPSHOT_NAME = ".regent-host-health.json"
PID_FILE = ".regent-preview.pid"
PORT_FILE = ".regent-preview-port"
@dataclass(frozen=True)
class HostResources:
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    disk_percent: float
    mem_total_bytes: int | None
    mem_available_bytes: int | None
    mem_percent: float | None
    load1: float | None
    load5: float | None
    load15: float | None
    cpu_count: int | None
    measured_at: float
    path: str
    preview_runtime_dirs: int = 0
    preview_venv_count: int = 0
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
@dataclass(frozen=True)
class HostGuardDecision:
    unhealthy: bool
    reasons: tuple[str, ...]
    resources: HostResources
    pruned: dict[str, Any] | None = None
    reaped: dict[str, Any] | None = None
    actions: tuple[str, ...] = ()
    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "unhealthy": self.unhealthy,
            "reasons": list(self.reasons),
            "resources": self.resources.as_dict(),
            "actions": list(self.actions),
        }
        if self.pruned is not None:
            out["pruned"] = self.pruned
        if self.reaped is not None:
            out["reaped"] = self.reaped
        return out
def _read_meminfo() -> tuple[int | None, int | None]:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None, None
    total = available = None
    try:
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None, None
    return total, available
def _read_load() -> tuple[float | None, float | None, float | None]:
    try:
        load1, load5, load15 = os.getloadavg()
        return float(load1), float(load5), float(load15)
    except (AttributeError, OSError):
        return None, None, None
def preview_runtime_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root).resolve() / "previews" / "runtime"
def count_preview_artifacts(runtime_root: str | Path) -> tuple[int, int]:
    root = Path(runtime_root)
    if not root.is_dir():
        return 0, 0
    dirs = [p for p in root.iterdir() if p.is_dir()]
    venvs = sum(1 for d in dirs if (d / ".preview-venv").exists())
    return len(dirs), venvs
def measure_host_resources(path: str | Path | None = None) -> HostResources:
    root = Path(path or "/").resolve()
    try:
        usage = shutil.disk_usage(str(root))
    except OSError:
        usage = shutil.disk_usage("/")
        root = Path("/")
    total_mem, avail_mem = _read_meminfo()
    mem_pct = None
    if total_mem and avail_mem is not None and total_mem > 0:
        mem_pct = round(100.0 * (1.0 - (avail_mem / total_mem)), 2)
    load1, load5, load15 = _read_load()
    cpu = os.cpu_count()
    disk_pct = round(100.0 * usage.used / usage.total, 2) if usage.total else 0.0
    runtime = preview_runtime_root(root)
    dirs, venvs = count_preview_artifacts(runtime)
    return HostResources(
        disk_total_bytes=int(usage.total),
        disk_used_bytes=int(usage.used),
        disk_free_bytes=int(usage.free),
        disk_percent=disk_pct,
        mem_total_bytes=total_mem,
        mem_available_bytes=avail_mem,
        mem_percent=mem_pct,
        load1=load1,
        load5=load5,
        load15=load15,
        cpu_count=cpu,
        measured_at=time.time(),
        path=str(root),
        preview_runtime_dirs=dirs,
        preview_venv_count=venvs,
    )
def evaluate_host(
    resources: HostResources,
    *,
    disk_percent_max: float = 85.0,
    mem_percent_max: float = 92.0,
    load1_per_cpu_max: float = 4.0,
    preview_venv_max: int | None = None,
) -> HostGuardDecision:
    reasons: list[str] = []
    if resources.disk_percent >= disk_percent_max:
        reasons.append(f"disk_percent={resources.disk_percent} >= {disk_percent_max}")
    if resources.mem_percent is not None and resources.mem_percent >= mem_percent_max:
        reasons.append(f"mem_percent={resources.mem_percent} >= {mem_percent_max}")
    cpu = resources.cpu_count or 1
    if resources.load1 is not None and (resources.load1 / max(cpu, 1)) >= load1_per_cpu_max:
        reasons.append(
            f"load1_per_cpu={resources.load1 / max(cpu, 1):.2f} >= {load1_per_cpu_max}"
        )
    if preview_venv_max is not None and resources.preview_venv_count > preview_venv_max:
        reasons.append(
            f"preview_venv_count={resources.preview_venv_count} > {preview_venv_max}"
        )
    return HostGuardDecision(
        unhealthy=bool(reasons),
        reasons=tuple(reasons),
        resources=resources,
    )
def prune_preview_venvs(
    runtime_root: str | Path,
    *,
    keep_newest: int = 8,
) -> dict[str, Any]:
    """Delete ``.preview-venv`` under older runtime dirs; keep newest N by mtime."""
    root = Path(runtime_root)
    removed: list[str] = []
    kept: list[str] = []
    freed_hint = 0
    if not root.is_dir():
        return {
            "removed": removed,
            "kept": kept,
            "freed_bytes_hint": 0,
            "root": str(root),
            "removed_count": 0,
        }
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for idx, d in enumerate(dirs):
        venv = d / ".preview-venv"
        if idx < max(0, int(keep_newest)):
            kept.append(d.name)
            continue
        if venv.exists():
            try:
                size = 0
                for i, f in enumerate(venv.rglob("*")):
                    if i > 5000:
                        break
                    if f.is_file():
                        try:
                            size += f.stat().st_size
                        except OSError:
                            pass
                shutil.rmtree(venv, ignore_errors=True)
                removed.append(d.name)
                freed_hint += size
            except OSError as exc:
                logger.warning(
                    "preview venv prune failed",
                    extra={"path": str(venv), "error": str(exc)},
                )
    return {
        "removed": removed,
        "kept": kept,
        "freed_bytes_hint": freed_hint,
        "root": str(root),
        "removed_count": len(removed),
    }
def _pids_with_cwd_under(roots: list[Path]) -> list[tuple[int, str]]:
    """Linux: find PIDs whose cwd is under one of ``roots``."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    resolved = []
    for r in roots:
        try:
            resolved.append(r.resolve())
        except OSError:
            continue
    found: list[tuple[int, str]] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue
        for root in resolved:
            try:
                cwd.relative_to(root)
            except ValueError:
                continue
            found.append((int(entry.name), str(cwd)))
            break
    return found
def reap_stale_preview_processes(
    runtime_root: str | Path,
    *,
    keep_newest: int = 8,
) -> dict[str, Any]:
    """Stop preview processes belonging to older runtime workspaces.
    Prefer ``.regent-preview.pid``; fall back to ``/proc/<pid>/cwd`` matching.
    Never touches the newest ``keep_newest`` runtime dirs.
    """
    root = Path(runtime_root)
    killed: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"killed": killed, "killed_count": 0, "root": str(root)}
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    stale = dirs[max(0, int(keep_newest)) :]
    if not stale:
        return {"killed": killed, "killed_count": 0, "root": str(root), "stale_dirs": 0}
    # Pid files first.
    for d in stale:
        pid_path = d / PID_FILE
        if not pid_path.is_file():
            continue
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append({"pid": pid, "dir": d.name, "via": "pid_file"})
        except OSError:
            pass
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
    # Orphans with cwd still under stale dirs (worker restart left processes).
    for pid, cwd in _pids_with_cwd_under(stale):
        if any(k["pid"] == pid for k in killed):
            continue
        # Skip our own process.
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append({"pid": pid, "cwd": cwd, "via": "proc_cwd"})
        except OSError:
            pass
    time.sleep(0.3)
    # Force kill survivors.
    for item in list(killed):
        pid = int(item["pid"])
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            item["forced"] = True
        except OSError:
            pass
    return {
        "killed": killed,
        "killed_count": len(killed),
        "root": str(root),
        "stale_dirs": len(stale),
    }
def snapshot_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).resolve() / SNAPSHOT_NAME
def write_host_snapshot(
    workspace_root: str | Path,
    decision: HostGuardDecision,
) -> Path:
    path = snapshot_path(workspace_root)
    payload = {
        **decision.as_dict(),
        "schema": "regent.host_health.v1",
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
def read_host_snapshot(workspace_root: str | Path) -> dict[str, Any] | None:
    path = snapshot_path(workspace_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
def host_blocks_work(
    workspace_root: str | Path,
    *,
    max_age_seconds: float = 180.0,
    disk_percent_max: float = 85.0,
    mem_percent_max: float = 92.0,
    load1_per_cpu_max: float = 4.0,
) -> tuple[bool, str]:
    """Return (blocked, reason). Prefer fresh snapshot; else live-measure."""
    snap = read_host_snapshot(workspace_root)
    if snap:
        measured = float((snap.get("resources") or {}).get("measured_at") or 0)
        fresh = measured and (time.time() - measured) <= max_age_seconds
        if fresh:
            if snap.get("unhealthy"):
                reasons = snap.get("reasons") or []
                return True, "; ".join(str(r) for r in reasons[:4]) or "host_unhealthy"
            return False, ""
    # Stale/missing snapshot: live check so burn cannot slip through.
    decision = evaluate_host(
        measure_host_resources(workspace_root),
        disk_percent_max=disk_percent_max,
        mem_percent_max=mem_percent_max,
        load1_per_cpu_max=load1_per_cpu_max,
    )
    if decision.unhealthy:
        return True, "; ".join(decision.reasons[:4]) or "host_unhealthy"
    return False, ""
def run_host_guard_once(
    *,
    workspace_root: str | Path,
    disk_percent_max: float = 85.0,
    mem_percent_max: float = 92.0,
    load1_per_cpu_max: float = 4.0,
    prune_keep_newest: int = 8,
    prune_disk_percent: float = 80.0,
    prune_mem_percent: float = 85.0,
    reap_processes: bool = True,
) -> HostGuardDecision:
    """Detect → allowlisted heal registry → re-measure → learn → snapshot."""
    from regent.application.environment_heal_memory import (
        load_heal_memory,
        read_ops_lessons,
        record_heal_outcome,
    )
    from regent.infrastructure.environment_heal_registry import run_selected_heal_actions

    measure_path = Path(workspace_root).resolve()
    first = measure_host_resources(measure_path)
    pre_decision = evaluate_host(
        first,
        disk_percent_max=disk_percent_max,
        mem_percent_max=mem_percent_max,
        load1_per_cpu_max=load1_per_cpu_max,
        preview_venv_max=prune_keep_newest * 2,
    )
    ctx = {
        "prune_keep_newest": prune_keep_newest,
        "prune_disk_percent": prune_disk_percent,
        "prune_mem_percent": prune_mem_percent,
    }
    memory = load_heal_memory(measure_path)
    lessons = read_ops_lessons(measure_path)
    enabled = None if reap_processes else {"prune_preview_venvs"}
    ran = run_selected_heal_actions(
        workspace_root=measure_path,
        resources=first,
        ctx=ctx,
        lessons_text=lessons,
        memory_prefs=list(memory.get("preferences") or []),
        enabled_ids=enabled,
    )
    actions: list[str] = []
    pruned: dict[str, Any] | None = None
    reaped: dict[str, Any] | None = None
    action_ids: list[str] = []
    for item in ran:
        if not item.get("ok"):
            actions.append(f"{item.get('id')}:error")
            continue
        action_ids.append(str(item["id"]))
        outcome = item.get("outcome") or {}
        if item["id"] == "prune_preview_venvs":
            pruned = outcome
            if outcome.get("removed_count"):
                actions.append(f"prune_preview_venvs:{outcome['removed_count']}")
        elif item["id"] == "reap_stale_previews":
            reaped = outcome
            if outcome.get("killed_count"):
                actions.append(f"reap_stale_previews:{outcome['killed_count']}")
        else:
            actions.append(str(item["id"]))

    resources = measure_host_resources(measure_path) if ran else first
    decision = evaluate_host(
        resources,
        disk_percent_max=disk_percent_max,
        mem_percent_max=mem_percent_max,
        load1_per_cpu_max=load1_per_cpu_max,
        preview_venv_max=prune_keep_newest * 2,
    )
    # Learn when we ran repairs and metrics improved.
    if action_ids:
        before_m = {
            "disk_percent": first.disk_percent,
            "mem_percent": first.mem_percent,
            "preview_venv_count": first.preview_venv_count,
        }
        after_m = {
            "disk_percent": resources.disk_percent,
            "mem_percent": resources.mem_percent,
            "preview_venv_count": resources.preview_venv_count,
        }
        improved = (
            (pre_decision.unhealthy and not decision.unhealthy)
            or resources.disk_percent < first.disk_percent - 0.05
            or (
                first.mem_percent is not None
                and resources.mem_percent is not None
                and resources.mem_percent < first.mem_percent - 0.5
            )
            or resources.preview_venv_count < first.preview_venv_count
        )
        reasons_for_learn = list(pre_decision.reasons) or [
            r
            for r, flag in (
                (f"disk_percent={first.disk_percent}", first.disk_percent >= prune_disk_percent),
                (
                    f"mem_percent={first.mem_percent}",
                    first.mem_percent is not None and first.mem_percent >= prune_mem_percent,
                ),
                (
                    f"preview_venv_count={first.preview_venv_count}",
                    first.preview_venv_count > prune_keep_newest,
                ),
            )
            if flag
        ]
        try:
            record_heal_outcome(
                measure_path,
                reasons_before=reasons_for_learn,
                action_ids=action_ids,
                improved=improved,
                metrics_before=before_m,
                metrics_after=after_m,
            )
        except Exception:  # noqa: BLE001
            logger.exception("environment heal memory update failed")

    decision = HostGuardDecision(
        unhealthy=decision.unhealthy,
        reasons=decision.reasons,
        resources=decision.resources,
        pruned=pruned,
        reaped=reaped,
        actions=tuple(actions),
    )
    write_host_snapshot(workspace_root, decision)
    return decision
