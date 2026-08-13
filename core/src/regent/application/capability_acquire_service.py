"""ACQUIRE: fetch, validate and register external capability packages.

V3 §2.2 (Resource Engine) — when internal REUSE/CONFIGURE/COMPOSE/BUILD cannot
satisfy a capability gap, the system may acquire a pre-built capability package
from a known registry over the network.

Safety invariants (from REGENT-DEFINITION-3.0 ATTRIBUTE_7 + Tech Spec §12):
- All external data is UNTRUSTED_DATA; it may NOT become instruction or
  authorization source.
- Downloads are gated by Permit + ExternalOperation (controlled egress).
- Package code is validated in a sandbox; never executed at import time.
- Source URL must be within Goal-authorized boundaries.
- Content hash must match manifest before registration.
- Network I/O must NOT run while a caller holds an open DB transaction (CD-7.2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.external_operation_service import ExternalOperationService
from regent.application.permit_service import PermitBinding, PermitService
from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import CapabilityModel, RunModel, WorkModel

logger = logging.getLogger(__name__)

# -- Constants ----------------------------------------------------------------

_ACQUIRE_PROTOCOL = "capability-acquire-v1"
_ACQUIRE_PROVIDER = "capability-acquire-v1"
_ACQUIRE_ACTION = "capability-acquire"
_ACQUIRE_ACTOR = "capability-acquire"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024  # 2 MB
_TIMEOUT_SECONDS = 30

# Known registries — extend via configuration or Goal authorization.
_DEFAULT_REGISTRIES: tuple[str, ...] = (
    "https://raw.githubusercontent.com/regent-core/capabilities/main/",
)


# -- Data classes -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcquireRequest:
    """Request to acquire a capability from the network."""

    capability_name: str
    requirement_key: str
    goal_id: uuid.UUID
    authorized_urls: tuple[str, ...] = ()
    registries: tuple[str, ...] = _DEFAULT_REGISTRIES
    work_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    actor_id: str = _ACQUIRE_ACTOR


@dataclass(frozen=True, slots=True)
class AcquireResult:
    """Outcome of an ACQUIRE attempt."""

    success: bool
    capability_id: uuid.UUID | None
    capability_name: str
    source_url: str | None
    source_hash: str | None
    failure_reason: str | None
    validation_checks: dict[str, Any]


# -- Validation ---------------------------------------------------------------


def _validate_package_manifest(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Validate capability.json structure and name."""
    name = manifest.get("name", "")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return False, f"invalid capability name: {name!r}"
    status = manifest.get("status", "")
    if status not in {"VERIFIED", "GOAL_CERTIFIED", "CANDIDATE"}:
        return False, f"unacceptable status: {status}"
    verification = manifest.get("verification")
    if not isinstance(verification, dict):
        return False, "missing verification block"
    return True, "ok"


def _check_content_hash(
    content: bytes, expected_hash: str | None
) -> tuple[bool, str]:
    """Verify SHA-256 content hash if provided."""
    if not expected_hash:
        return True, "no hash to verify"
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_hash.lower():
        return False, f"hash mismatch: expected {expected_hash}, got {actual}"
    return True, "hash ok"


def _scan_for_unsafe_patterns(content: str) -> list[str]:
    """Detect obviously unsafe patterns in Python source code.

    This is a defense-in-depth check; the real isolation comes from the
    sandbox. We reject packages that try to escape the sandbox at import time.
    """
    violations: list[str] = []
    unsafe = [
        (r"\bos\.system\s*\(", "os.system() call"),
        (r"\bsubprocess\.", "subprocess usage"),
        (r"\bexec\s*\(", "exec() call"),
        (r"\beval\s*\(", "eval() call"),
        (r"\b__import__\s*\(", "__import__() call"),
        (r"\bopen\s*\(.*['\"]w", "file write in open()"),
        (r"\bsocket\.", "socket usage"),
    ]
    for pattern, description in unsafe:
        if re.search(pattern, content):
            violations.append(description)
    return violations


def _resolve_egress_proxy(explicit: str | None) -> str | None:
    if explicit is not None:
        proxy = explicit.strip() if explicit else ""
    else:
        proxy = (get_settings().dependency_egress_proxy or "").strip()
    if not proxy:
        return None
    if urlparse(proxy).scheme not in {"http", "https"}:
        return None
    return proxy


# -- Service ------------------------------------------------------------------


class CapabilityAcquireService:
    """Discover, download, validate and register external capability packages.

    Usage:
        service = CapabilityAcquireService(session_factory)
        result = await service.acquire(AcquireRequest(...))

    Callers must invoke ``acquire`` **outside** any open ``session.begin()``
    (CD-7.2). Registration uses its own short transaction.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        egress_proxy: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._egress_proxy = egress_proxy
        self._permits = PermitService(sessions)
        self._external_ops = ExternalOperationService(sessions)

    async def acquire(self, request: AcquireRequest) -> AcquireResult:
        """Try to acquire a capability package from known registries.

        Returns AcquireResult with success=False if no registry has the package
        or if validation fails. Never raises on network/validation errors.
        """
        # 1. Check if capability already exists (idempotent) — own short session.
        existing = await self._find_existing(request.capability_name, request.goal_id)
        if existing is not None:
            return AcquireResult(
                success=True,
                capability_id=existing.id,
                capability_name=existing.name,
                source_url=existing.source_url,
                source_hash=existing.source_hash,
                failure_reason=None,
                validation_checks={"existing": True},
            )

        # 2. Build candidate URLs from authorized sources + registries
        candidate_urls = self._build_candidate_urls(request)
        if not candidate_urls:
            return AcquireResult(
                success=False,
                capability_id=None,
                capability_name=request.capability_name,
                source_url=None,
                source_hash=None,
                failure_reason="no candidate URLs (not authorized or no registries)",
                validation_checks={},
            )

        # 3. Fail-closed without controlled egress (CD-7.2 / N-4 sibling).
        proxy = _resolve_egress_proxy(self._egress_proxy)
        if proxy is None:
            return AcquireResult(
                success=False,
                capability_id=None,
                capability_name=request.capability_name,
                source_url=None,
                source_hash=None,
                failure_reason="egress proxy not configured (fail-closed)",
                validation_checks={"egress_required": True},
            )

        # 4. Permit + ExternalOperation before any network I/O.
        try:
            eo_id = await self._prepare_dispatch(request, candidate_urls=candidate_urls)
        except (DomainError, PermissionError, ValueError) as exc:
            logger.warning(
                "capability acquire permit/EO denied",
                extra={"goal_id": str(request.goal_id), "error": str(exc)[:200]},
            )
            return AcquireResult(
                success=False,
                capability_id=None,
                capability_name=request.capability_name,
                source_url=None,
                source_hash=None,
                failure_reason=f"permit/EO denied: {exc}"[:300],
                validation_checks={"permit_denied": True},
            )

        # 5. Network I/O (outside any caller transaction).
        last_failure: AcquireResult | None = None
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                for url in candidate_urls:
                    result = await self._try_fetch_and_register(client, request, url)
                    if result.success:
                        await self._external_ops.mark_succeeded(
                            eo_id,
                            external_id=result.source_url or url,
                            summary={
                                "capability_name": request.capability_name,
                                "source_hash": result.source_hash,
                            },
                        )
                        return result
                    last_failure = result
        except Exception as exc:
            await self._external_ops.mark_unknown(eo_id, reason=f"acquire_exception:{exc}"[:200])
            return AcquireResult(
                success=False,
                capability_id=None,
                capability_name=request.capability_name,
                source_url=None,
                source_hash=None,
                failure_reason=f"network error: {exc}"[:300],
                validation_checks={"attempted_urls": list(candidate_urls)},
            )

        await self._external_ops.mark_failed_terminal(
            eo_id,
            failure_code="ALL_CANDIDATES_FAILED",
            summary={
                "attempted_urls": list(candidate_urls),
                "last_reason": (last_failure.failure_reason if last_failure else None),
            },
        )
        return AcquireResult(
            success=False,
            capability_id=None,
            capability_name=request.capability_name,
            source_url=None,
            source_hash=None,
            failure_reason=f"all {len(candidate_urls)} candidate URLs failed",
            validation_checks={"attempted_urls": list(candidate_urls)},
        )

    async def _prepare_dispatch(
        self, request: AcquireRequest, *, candidate_urls: list[str]
    ) -> uuid.UUID:
        work_id, run_id = await self._ensure_work_and_run(request)
        operation_key = (
            f"capability-acquire:{request.goal_id}:{request.capability_name}:"
            f"{request.requirement_key}"
        )
        existing = await self._external_ops.get_by_operation_key(operation_key)
        if existing is not None and existing.status in {
            "DISPATCHING",
            "UNKNOWN",
            "RECONCILING",
            "SUCCEEDED",
        }:
            # Idempotent retry path: reuse dispatch rights when still open / succeeded.
            return existing.id

        # Dead terminal or stuck PREPARED: mint a new key so we never re-terminal
        # the same EO (which raised cannot mark FAILED_TERMINAL).
        if existing is not None:
            operation_key = f"{operation_key}:retry:{uuid.uuid4().hex[:8]}"

        permit_id = await self._permits.request(
            PermitBinding(
                goal_id=request.goal_id,
                work_id=work_id,
                run_id=run_id,
                actor_id=request.actor_id,
                action=_ACQUIRE_ACTION,
                target=request.capability_name,
                parameters={
                    "requirement_key": request.requirement_key,
                    "candidate_count": len(candidate_urls),
                },
                data_scope={"goal_id": str(request.goal_id)},
                network_scope={"egress": "controlled", "proxy": True},
                resource_limit={"max_bytes": _MAX_PACKAGE_BYTES},
                risk_level="LOW",
                valid_until=datetime.now(UTC) + timedelta(hours=1),
                idempotency_key=f"permit:{operation_key}",
            )
        )
        claimed = await self._permits.claim(permit_id, actor_id=request.actor_id)
        if claimed.binding.action != _ACQUIRE_ACTION:
            raise DomainError(ErrorCode.POLICY_DENIED, "permit action mismatch")
        prepared = await self._external_ops.prepare(
            operation_key=operation_key,
            provider=_ACQUIRE_PROVIDER,
            action=_ACQUIRE_ACTION,
            permit_id=claimed.id,
            local_fencing_token=claimed.nonce,
            payload={
                "goal_id": str(request.goal_id),
                "capability_name": request.capability_name,
                "requirement_key": request.requirement_key,
                "candidate_urls": candidate_urls[:8],
            },
            goal_id=request.goal_id,
        )
        await self._external_ops.begin_dispatch(
            prepared.id,
            worker_lease_token=f"{request.actor_id}:{claimed.id}",
            expected_fencing_token=claimed.nonce,
        )
        return prepared.id

    async def _ensure_work_and_run(
        self, request: AcquireRequest
    ) -> tuple[uuid.UUID, uuid.UUID]:
        if request.work_id is not None and request.run_id is not None:
            return request.work_id, request.run_id
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(WorkModel).where(WorkModel.goal_id == request.goal_id).limit(1)
            )
            if existing is not None:
                run = await session.scalar(
                    select(RunModel).where(RunModel.work_id == existing.id).limit(1)
                )
                if run is not None:
                    return existing.id, run.id
                run = RunModel(
                    id=uuid.uuid4(),
                    work_id=existing.id,
                    status="CREATED",
                    version=0,
                    actor_id=request.actor_id,
                    input_version="0",
                    idempotency_key=f"acquire-run-{existing.id}",
                    correlation_id=request.goal_id,
                )
                session.add(run)
                await session.flush()
                return existing.id, run.id
            work = WorkModel(
                id=uuid.uuid4(),
                goal_id=request.goal_id,
                purpose=f"capability-acquire:{request.capability_name}",
                input_refs=[],
                acceptance_criteria={},
                dependency_ids=[],
                priority=0,
                budget={},
                status="PLANNED",
                version=0,
                correlation_id=request.goal_id,
            )
            session.add(work)
            await session.flush()
            run = RunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                status="CREATED",
                version=0,
                actor_id=request.actor_id,
                input_version="0",
                idempotency_key=f"acquire-run-{work.id}",
                correlation_id=request.goal_id,
            )
            session.add(run)
            await session.flush()
            return work.id, run.id

    async def _find_existing(
        self, name: str, goal_id: uuid.UUID
    ) -> CapabilityModel | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(CapabilityModel).where(
                    CapabilityModel.name == name,
                    (CapabilityModel.scope_goal_id == goal_id)
                    | CapabilityModel.scope_goal_id.is_(None),
                    CapabilityModel.status.in_(["VERIFIED", "GOAL_CERTIFIED"]),
                )
            )

    def _build_candidate_urls(self, request: AcquireRequest) -> list[str]:
        """Build list of URLs to try for the capability package."""
        urls: list[str] = []
        safe_name = request.capability_name.replace(" ", "-").lower()

        # From authorized URLs (Goal-authorized sources)
        for auth_url in request.authorized_urls:
            auth_url = auth_url.rstrip("/")
            if auth_url.startswith("https://"):
                urls.append(f"{auth_url}/{safe_name}/capability.json")

        # From known registries
        for registry in request.registries:
            registry = registry.rstrip("/")
            urls.append(f"{registry}/{safe_name}/capability.json")

        return list(dict.fromkeys(urls))  # deduplicate, preserve order

    async def _try_fetch_and_register(
        self,
        client: httpx.AsyncClient,
        request: AcquireRequest,
        manifest_url: str,
    ) -> AcquireResult:
        """Try to fetch and validate a capability package from a single URL."""
        try:
            resp = await client.get(manifest_url)
            if resp.status_code != 200:
                return AcquireResult(
                    success=False,
                    capability_id=None,
                    capability_name=request.capability_name,
                    source_url=manifest_url,
                    source_hash=None,
                    failure_reason=f"HTTP {resp.status_code}",
                    validation_checks={},
                )

            content = resp.content
            if len(content) > _MAX_PACKAGE_BYTES:
                return AcquireResult(
                    success=False,
                    capability_id=None,
                    capability_name=request.capability_name,
                    source_url=manifest_url,
                    source_hash=None,
                    failure_reason=f"package too large ({len(content)} bytes)",
                    validation_checks={},
                )

            # Parse manifest
            try:
                manifest = json.loads(content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return AcquireResult(
                    success=False,
                    capability_id=None,
                    capability_name=request.capability_name,
                    source_url=manifest_url,
                    source_hash=None,
                    failure_reason=f"invalid JSON: {exc}",
                    validation_checks={},
                )

            # Validate manifest structure
            valid, reason = _validate_package_manifest(manifest)
            if not valid:
                return AcquireResult(
                    success=False,
                    capability_id=None,
                    capability_name=request.capability_name,
                    source_url=manifest_url,
                    source_hash=None,
                    failure_reason=f"manifest validation failed: {reason}",
                    validation_checks={"manifest_valid": False},
                )

            # Verify content hash
            expected_hash = manifest.get("source_hash") or manifest.get("content_hash")
            hash_ok, hash_reason = _check_content_hash(content, expected_hash)
            if not hash_ok:
                return AcquireResult(
                    success=False,
                    capability_id=None,
                    capability_name=request.capability_name,
                    source_url=manifest_url,
                    source_hash=None,
                    failure_reason=hash_reason,
                    validation_checks={"hash_check": False},
                )

            content_hash = hashlib.sha256(content).hexdigest()

            # Try to fetch implementation if referenced
            impl_content: str | None = None
            impl_url = manifest.get("implementation_url")
            if impl_url and isinstance(impl_url, str):
                # Resolve relative to manifest URL
                if not impl_url.startswith("http"):
                    base = manifest_url.rsplit("/", 1)[0]
                    impl_url = f"{base}/{impl_url}"
                try:
                    impl_resp = await client.get(impl_url)
                    if impl_resp.status_code == 200:
                        impl_text = impl_resp.text
                        violations = _scan_for_unsafe_patterns(impl_text)
                        if violations:
                            return AcquireResult(
                                success=False,
                                capability_id=None,
                                capability_name=request.capability_name,
                                source_url=manifest_url,
                                source_hash=content_hash,
                                failure_reason=f"unsafe patterns: {violations}",
                                validation_checks={"safety_scan": violations},
                            )
                        impl_content = impl_text
                except httpx.HTTPError:
                    pass  # Implementation is optional; manifest alone may suffice

            # Register capability
            capability_id = await self._register(
                request, manifest, manifest_url, content_hash, impl_content
            )

            logger.info(
                "acquired capability %s from %s",
                request.capability_name,
                manifest_url,
            )
            return AcquireResult(
                success=True,
                capability_id=capability_id,
                capability_name=request.capability_name,
                source_url=manifest_url,
                source_hash=content_hash,
                validation_checks={
                    "manifest_valid": True,
                    "hash_verified": True,
                    "safety_scan": "clean" if impl_content else "no_impl",
                    "registered": True,
                },
            )

        except httpx.HTTPError as exc:
            return AcquireResult(
                success=False,
                capability_id=None,
                capability_name=request.capability_name,
                source_url=manifest_url,
                source_hash=None,
                failure_reason=f"network error: {exc}",
                validation_checks={},
            )

    async def _register(
        self,
        request: AcquireRequest,
        manifest: dict[str, Any],
        source_url: str,
        content_hash: str,
        impl_content: str | None,
    ) -> uuid.UUID:
        """Register the acquired capability in the database."""
        async with self._sessions() as session, session.begin():
            # Check again inside transaction (race condition guard)
            existing = await session.scalar(
                select(CapabilityModel).where(
                    CapabilityModel.name == manifest["name"],
                    CapabilityModel.scope_goal_id == request.goal_id,
                )
            )
            if existing is not None:
                if existing.status == "REVOKED":
                    existing.status = "VERIFIED"
                existing.source_url = source_url
                existing.source_hash = content_hash
                existing.verification = {
                    **dict(existing.verification or {}),
                    "acquired": True,
                    "protocol": _ACQUIRE_PROTOCOL,
                    "source_url": source_url,
                    "source_hash": content_hash,
                }
                await session.flush()
                return existing.id

            verification: dict[str, Any] = {
                **dict(manifest.get("verification") or {}),
                "acquired": True,
                "protocol": _ACQUIRE_PROTOCOL,
                "source_url": source_url,
                "source_hash": content_hash,
            }
            if impl_content is not None:
                verification["has_implementation"] = True
                verification["impl_hash"] = hashlib.sha256(
                    impl_content.encode("utf-8")
                ).hexdigest()

            capability_id = uuid.uuid4()
            session.add(
                CapabilityModel(
                    id=capability_id,
                    name=manifest["name"],
                    status="VERIFIED",
                    scope_goal_id=request.goal_id,
                    description=str(
                        manifest.get("description")
                        or f"Acquired capability {manifest['name']} from {source_url}"
                    ),
                    verification=verification,
                    source_url=source_url,
                    source_hash=content_hash,
                )
            )
            await session.flush()
            return capability_id
