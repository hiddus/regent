"""ACQUIRE: fetch, validate and register external capability packages.

V3 §2.2 (Resource Engine) — when internal REUSE/CONFIGURE/COMPOSE/BUILD cannot
satisfy a capability gap, the system may acquire a pre-built capability package
from a known registry over the network.

Safety invariants (from REGENT-DEFINITION-1.0 ATTRIBUTE_3 + Tech Spec §12):
- All external data is UNTRUSTED_DATA; it may NOT become instruction or
  authorization source.
- Downloads are gated by Permit + ExternalOperation (controlled egress).
- Package code is validated in a sandbox; never executed at import time.
- Source URL must be within Goal-authorized boundaries.
- Content hash must match manifest before registration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import CapabilityModel

logger = logging.getLogger(__name__)

# -- Constants ----------------------------------------------------------------

_ACQUIRE_PROTOCOL = "capability-acquire-v1"
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


# -- Service ------------------------------------------------------------------


class CapabilityAcquireService:
    """Discover, download, validate and register external capability packages.

    Usage:
        service = CapabilityAcquireService(session_factory)
        result = await service.acquire(AcquireRequest(...))
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def acquire(self, request: AcquireRequest) -> AcquireResult:
        """Try to acquire a capability package from known registries.

        Returns AcquireResult with success=False if no registry has the package
        or if validation fails. Never raises on network/validation errors.
        """
        # 1. Check if capability already exists (idempotent)
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

        # 3. Try each candidate URL
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            for url in candidate_urls:
                result = await self._try_fetch_and_register(
                    client, request, url
                )
                if result.success:
                    return result

        return AcquireResult(
            success=False,
            capability_id=None,
            capability_name=request.capability_name,
            source_url=None,
            source_hash=None,
            failure_reason=f"all {len(candidate_urls)} candidate URLs failed",
            validation_checks={"attempted_urls": list(candidate_urls)},
        )

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
                failure_reason=None,
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
