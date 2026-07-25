"""P2-2 Runtime Profile registry (fail-closed)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import RuntimeProfileModel

BOOTSTRAP_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "python-web-v1",
        "version": "1",
        "status": "CERTIFIED",
        "abi_json": {"language": "python", "asgi": True, "port": 8080},
        "sandbox_image": "regent-python-web-v1-sandbox:1",
        "resolver_image": "regent-python-web-v1-resolver:1",
    },
    {
        "name": "static-web-v1",
        "version": "1",
        "status": "CERTIFIED",
        "abi_json": {"language": "static", "static_files": True},
        "sandbox_image": None,
        "resolver_image": None,
    },
    {
        "name": "node-web-v1",
        "version": "1",
        "status": "DRAFT",
        "abi_json": {"language": "node", "http": True},
        "sandbox_image": None,
        "resolver_image": None,
    },
    {
        "name": "python-data-v1",
        "version": "1",
        "status": "DRAFT",
        "abi_json": {"language": "python", "batch": True},
        "sandbox_image": None,
        "resolver_image": None,
    },
)


@dataclass(frozen=True, slots=True)
class UpsertRuntimeProfile:
    name: str
    version: str
    status: str
    abi_json: dict[str, Any]
    sandbox_image: str | None
    resolver_image: str | None
    actor: str


class RuntimeProfileService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def seed_bootstrap(self, *, actor: str = "regent-core") -> int:
        created = 0
        for item in BOOTSTRAP_PROFILES:
            try:
                await self.upsert(
                    UpsertRuntimeProfile(
                        name=str(item["name"]),
                        version=str(item["version"]),
                        status=str(item["status"]),
                        abi_json=dict(item["abi_json"]),
                        sandbox_image=item.get("sandbox_image"),
                        resolver_image=item.get("resolver_image"),
                        actor=actor,
                    )
                )
                created += 1
            except DomainError:
                continue
        return created

    async def upsert(self, command: UpsertRuntimeProfile) -> RuntimeProfileModel:
        if command.status not in {"DRAFT", "CERTIFIED", "DEPRECATED", "REVOKED"}:
            raise DomainError(ErrorCode.INVALID_STATE, "invalid runtime profile status")
        digest = canonical_hash(
            {
                "name": command.name,
                "version": command.version,
                "abi": command.abi_json,
                "sandbox_image": command.sandbox_image,
                "resolver_image": command.resolver_image,
            }
        )
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(RuntimeProfileModel).where(
                    RuntimeProfileModel.name == command.name,
                    RuntimeProfileModel.version == command.version,
                )
            )
            if existing is not None:
                if existing.status == "REVOKED":
                    raise DomainError(ErrorCode.INVALID_STATE, "revoked profile is immutable")
                existing.status = command.status
                existing.abi_json = command.abi_json
                existing.sandbox_image = command.sandbox_image
                existing.resolver_image = command.resolver_image
                existing.content_hash = digest
                await session.flush()
                return existing
            model = RuntimeProfileModel(
                id=uuid.uuid4(),
                name=command.name,
                version=command.version,
                status=command.status,
                abi_json=command.abi_json,
                sandbox_image=command.sandbox_image,
                resolver_image=command.resolver_image,
                content_hash=digest,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def require_certified(self, name: str, version: str = "1") -> RuntimeProfileModel:
        async with self._sessions() as session:
            model = await session.scalar(
                select(RuntimeProfileModel).where(
                    RuntimeProfileModel.name == name,
                    RuntimeProfileModel.version == version,
                )
            )
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"runtime profile not found: {name}")
            if model.status != "CERTIFIED":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"runtime profile {name}@{version} is {model.status} (fail-closed)",
                )
            return model

    async def list_profiles(self) -> list[RuntimeProfileModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(RuntimeProfileModel).order_by(
                        RuntimeProfileModel.name, RuntimeProfileModel.version
                    )
                )
            )
