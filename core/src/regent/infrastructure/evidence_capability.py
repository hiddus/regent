"""Bootstrap and load certified evidence connector capability packages."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import CapabilityModel

CAPABILITY_NAME = "allowlisted-http-source-v1"


@dataclass(frozen=True, slots=True)
class EvidenceConnectorCapabilityPackage:
    name: str
    status: str
    description: str
    verification: dict[str, Any]
    default_feeds: tuple[str, ...]


@lru_cache
def load_allowlisted_http_capability_package() -> EvidenceConnectorCapabilityPackage:
    path = (
        Path(__file__).resolve().parent.parent
        / "capabilities_bootstrap"
        / "allowlisted_http_source_v1.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    feeds = tuple(
        str(item).strip()
        for item in (raw.get("default_feeds") or [])
        if str(item).strip()
    )
    return EvidenceConnectorCapabilityPackage(
        name=str(raw["name"]),
        status=str(raw.get("status") or "VERIFIED"),
        description=str(raw.get("description") or ""),
        verification=dict(raw.get("verification") or {}),
        default_feeds=feeds,
    )


async def ensure_allowlisted_http_capability(
    sessions: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Ensure the connector exists in the certified capability pool (idempotent)."""
    package = load_allowlisted_http_capability_package()
    async with sessions() as session, session.begin():
        existing = await session.scalar(
            select(CapabilityModel).where(
                CapabilityModel.name == package.name,
                CapabilityModel.scope_goal_id.is_(None),
            )
        )
        if existing is not None:
            if existing.status == "REVOKED":
                existing.status = package.status
            existing.description = package.description
            existing.verification = {
                **dict(existing.verification or {}),
                **package.verification,
                "default_feeds": list(package.default_feeds),
            }
            await session.flush()
            return existing.id
        capability_id = uuid.uuid4()
        session.add(
            CapabilityModel(
                id=capability_id,
                name=package.name,
                status=package.status,
                scope_goal_id=None,
                description=package.description,
                verification={
                    **package.verification,
                    "default_feeds": list(package.default_feeds),
                },
            )
        )
        await session.flush()
        return capability_id
