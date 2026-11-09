"""Bootstrap delivery-review-v1 capability package."""

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

CAPABILITY_NAME = "delivery-review-v1"


@dataclass(frozen=True, slots=True)
class DeliveryReviewCapabilityPackage:
    name: str
    status: str
    description: str
    verification: dict[str, Any]


@lru_cache
def load_delivery_review_capability_package() -> DeliveryReviewCapabilityPackage:
    path = (
        Path(__file__).resolve().parent.parent
        / "capabilities_bootstrap"
        / "delivery_review_v1.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return DeliveryReviewCapabilityPackage(
        name=str(raw["name"]),
        status=str(raw.get("status") or "VERIFIED"),
        description=str(raw.get("description") or ""),
        verification=dict(raw.get("verification") or {}),
    )


async def ensure_delivery_review_capability(
    sessions: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Ensure delivery-review-v1 exists in the certified capability pool."""
    package = load_delivery_review_capability_package()
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
                verification=dict(package.verification),
            )
        )
        await session.flush()
        return capability_id
