"""Delivery batch decomposition and state helpers (Phases A–D).

Any non-trivial Goal is split into ordered batches. Each batch is generated
against the prior merged workspace, verified in isolation, then merged.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.infrastructure.models import DeliveryBatchModel

BATCH_PLANNED = "PLANNED"
BATCH_GENERATING = "GENERATING"
BATCH_VERIFYING = "VERIFYING"
BATCH_MERGED = "MERGED"
BATCH_REJECTED = "REJECTED"
BATCH_CANCELLED = "CANCELLED"

_SCAFFOLD_NAMES = {
    "requirements.txt",
    "readme.md",
    "pyproject.toml",
    "package.json",
    "dockerfile",
    ".env.example",
}
_BACKEND_RE = re.compile(r"(^|/)(src/.*\.py|app\.py|main\.py|wsgi\.py|asgi\.py)$", re.I)
_FRONTEND_RE = re.compile(
    r"(^|/)(index\.html|.*\.(html|css|js)|static/.*|templates/.*)$", re.I
)


@dataclass(frozen=True, slots=True)
class DeliveryBatchSpec:
    ordinal: int
    key: str
    title: str
    scope_paths: tuple[str, ...]
    acceptance: dict[str, Any] = field(default_factory=dict)
    is_final: bool = False
    milestone_key: str = ""
    milestone_ordinal: int | None = None


def propose_delivery_batches(
    planned_paths: list[str],
    component_plan: list[dict[str, Any]] | None = None,
    *,
    milestone_key: str = "",
    milestone_ordinal: int | None = None,
    milestone_title: str | None = None,
    acceptance: dict[str, Any] | None = None,
    force_incremental: bool = True,
) -> list[DeliveryBatchSpec]:
    """Split planned paths into ordered delivery batches.

    Heuristic (Claude Code style workstreams):
    1. scaffold — lockfiles / README / root config
    2. backend — Python entrypoints and src/
    3. frontend — HTML/CSS/JS/static/templates
    4. remainder — anything else
    5. optional component-named slices when component_plan has named modules

    Always produces ≥2 batches when force_incremental and there are ≥2 paths
    spanning different layers; single-file Goals stay one batch.
    """
    paths = _normalize_paths(planned_paths)
    if not paths:
        paths = ["src/app.py", "index.html", "requirements.txt", "README.md"]

    groups: list[tuple[str, str, list[str]]] = []
    scaffold = [p for p in paths if p.lower().split("/")[-1] in _SCAFFOLD_NAMES]
    backend = [p for p in paths if p not in scaffold and _BACKEND_RE.search(p)]
    frontend = [
        p for p in paths if p not in scaffold and p not in backend and _FRONTEND_RE.search(p)
    ]
    rest = [p for p in paths if p not in scaffold and p not in backend and p not in frontend]

    if scaffold:
        groups.append(("scaffold", "项目脚手架与依赖清单", scaffold))
    if backend:
        groups.append(("backend", "后端业务与入口", backend))
    if frontend:
        groups.append(("frontend", "前端界面与静态资源", frontend))
    if rest:
        groups.append(("support", "支撑文件", rest))

    # Enrich with component_plan names when they map to distinct path prefixes.
    for item in component_plan or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.lower() in {"app", "web", "api"}:
            continue
        prefix = str(item.get("path") or item.get("root") or f"src/{name}").replace("\\", "/")
        matched = [p for p in paths if p == prefix or p.startswith(prefix.rstrip("/") + "/")]
        if len(matched) >= 2:
            key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "component"
            # Pull matched paths out of existing groups into a dedicated slice.
            groups = [
                (gk, gt, [p for p in gps if p not in matched]) for gk, gt, gps in groups
            ]
            groups = [(gk, gt, gps) for gk, gt, gps in groups if gps]
            groups.append((f"comp-{key}", f"组件：{name}", matched))

    if not groups:
        groups = [("all", "整包交付", list(paths))]

    # Force incremental when multiple layers exist.
    if force_incremental and len(groups) == 1 and len(paths) >= 3:
        mid = max(1, len(paths) // 2)
        groups = [
            ("slice-a", "第一批交付文件", paths[:mid]),
            ("slice-b", "第二批交付文件", paths[mid:]),
        ]

    ms_prefix = milestone_key or "goal"
    ms_label = milestone_title or (f"阶段{milestone_ordinal}" if milestone_ordinal else "目标")
    base_acceptance = dict(acceptance or {})
    specs: list[DeliveryBatchSpec] = []
    for idx, (key, title, scope) in enumerate(groups, start=1):
        is_final = idx == len(groups)
        batch_acceptance = {
            **base_acceptance,
            "acceptance_scope": "batch_subset" if not is_final else base_acceptance.get(
                "acceptance_scope", "batch_final"
            ),
            "batch_key": key,
            "batch_ordinal": idx,
            "batch_title": title,
            "forbid_full_goal_claim": not is_final,
            "batch_run_smoke": is_final,
        }
        specs.append(
            DeliveryBatchSpec(
                ordinal=idx,
                key=f"{ms_prefix}-{key}",
                title=f"{ms_label} · {title}",
                scope_paths=tuple(scope),
                acceptance=batch_acceptance,
                is_final=is_final,
                milestone_key=milestone_key,
                milestone_ordinal=milestone_ordinal,
            )
        )
    return specs


def _normalize_paths(planned_paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in planned_paths:
        path = str(raw or "").replace("\\", "/").lstrip("./")
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


async def persist_batch_plan(
    session: AsyncSession,
    *,
    goal_id: uuid.UUID,
    app_project_id: uuid.UUID,
    generation_run_id: uuid.UUID | None,
    specs: list[DeliveryBatchSpec],
    correlation_id: str,
    attempt: int = 1,
) -> list[DeliveryBatchModel]:
    """Insert PLANNED batch rows for a generation attempt."""
    rows: list[DeliveryBatchModel] = []
    for spec in specs:
        row = DeliveryBatchModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            app_project_id=app_project_id,
            generation_run_id=generation_run_id,
            milestone_key=spec.milestone_key or "",
            milestone_ordinal=spec.milestone_ordinal,
            batch_ordinal=spec.ordinal,
            batch_key=spec.key,
            title=spec.title,
            status=BATCH_PLANNED,
            version=0,
            attempt=attempt,
            is_final=spec.is_final,
            scope_paths=list(spec.scope_paths),
            acceptance_json=dict(spec.acceptance),
            verification_json={},
            summary_json={},
            correlation_id=correlation_id,
            metadata_json={"derivation": "path-layer-v1"},
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def load_batches_for_run(
    session: AsyncSession, generation_run_id: uuid.UUID
) -> list[DeliveryBatchModel]:
    result = await session.scalars(
        select(DeliveryBatchModel)
        .where(DeliveryBatchModel.generation_run_id == generation_run_id)
        .order_by(DeliveryBatchModel.batch_ordinal.asc())
    )
    return list(result)


def transition_batch(row: DeliveryBatchModel, new_status: str, **fields: Any) -> None:
    allowed = {
        BATCH_PLANNED: {BATCH_GENERATING, BATCH_CANCELLED},
        BATCH_GENERATING: {BATCH_VERIFYING, BATCH_REJECTED, BATCH_CANCELLED},
        BATCH_VERIFYING: {BATCH_MERGED, BATCH_REJECTED},
        BATCH_MERGED: set(),
        BATCH_REJECTED: {BATCH_PLANNED},  # retry reopens
        BATCH_CANCELLED: set(),
    }
    current = row.status
    if new_status not in allowed.get(current, set()) and new_status != current:
        raise ValueError(f"invalid batch transition {current} → {new_status}")
    row.status = new_status
    row.version = int(row.version or 0) + 1
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
