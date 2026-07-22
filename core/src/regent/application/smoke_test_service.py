"""R7 post-deployment smoke test service.

Performs an HTTP reachability probe and records an internal observation.
Internal smoke signals must not satisfy product metric gates.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.observation_service import ObservationInput, ObservationService
from regent.config import get_settings

logger = logging.getLogger(__name__)

HTTP_PROBE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class SmokeTestResult:
    """Result of a post-deployment smoke test."""

    passed: bool
    endpoint: str
    checks: list[dict[str, Any]]
    errors: list[str]
    observation_id: uuid.UUID | None = None


class DeploymentSmokeTestService:
    """Post-deployment smoke test for R7 experiential gate."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run_smoke_test(
        self,
        goal_id: uuid.UUID,
        deployment_id: uuid.UUID,
        endpoint: str,
        *,
        actor: str = "regent-core",
    ) -> SmokeTestResult:
        """Run post-deployment smoke test and record an internal observation."""
        checks: list[dict[str, Any]] = []
        errors: list[str] = []

        if not endpoint or endpoint == "N/A":
            errors.append("deployment endpoint is not configured")
            checks.append(
                {"check": "endpoint_configured", "passed": False, "detail": "no endpoint"}
            )
        else:
            checks.append(
                {
                    "check": "endpoint_configured",
                    "passed": True,
                    "detail": f"endpoint: {endpoint}",
                }
            )

        if endpoint and endpoint.startswith(("http://", "https://")):
            checks.append(
                {"check": "endpoint_url_format", "passed": True, "detail": "valid URL scheme"}
            )
            http_ok, http_detail = await self._probe_endpoint(endpoint)
            checks.append(
                {
                    "check": "http_reachable",
                    "passed": http_ok,
                    "detail": http_detail,
                }
            )
            if not http_ok:
                errors.append(f"endpoint is not reachable: {http_detail}")
        elif endpoint and endpoint.startswith("/"):
            # Relative preview path: probe via local API
            absolute = f"http://regent-api:8000{endpoint}"
            checks.append(
                {
                    "check": "endpoint_url_format",
                    "passed": True,
                    "detail": f"relative path resolved to {absolute}",
                }
            )
            http_ok, http_detail = await self._probe_endpoint(absolute)
            checks.append(
                {
                    "check": "http_reachable",
                    "passed": http_ok,
                    "detail": http_detail,
                }
            )
            if not http_ok:
                errors.append(f"endpoint is not reachable: {http_detail}")
        elif endpoint:
            checks.append(
                {
                    "check": "endpoint_url_format",
                    "passed": False,
                    "detail": f"invalid URL: {endpoint}",
                }
            )
            errors.append(f"endpoint has invalid URL format: {endpoint}")

        passed = len(errors) == 0
        observation_id: uuid.UUID | None = None

        try:
            observation_id = await self._record_observation(
                goal_id=goal_id,
                deployment_id=deployment_id,
                passed=passed,
                endpoint=endpoint,
                checks=checks,
                errors=errors,
                actor=actor,
            )
        except Exception:
            logger.exception(
                "failed to record smoke test observation",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
            )

        return SmokeTestResult(
            passed=passed,
            endpoint=endpoint,
            checks=checks,
            errors=errors,
            observation_id=observation_id,
        )

    @staticmethod
    async def _probe_endpoint(endpoint: str) -> tuple[bool, str]:
        """Issue a real HTTP GET and require a successful response."""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=HTTP_PROBE_TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(endpoint)
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code}"
            return True, f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            return False, str(exc)

    async def _record_observation(
        self,
        *,
        goal_id: uuid.UUID,
        deployment_id: uuid.UUID,
        passed: bool,
        endpoint: str,
        checks: list[dict[str, Any]],
        errors: list[str],
        actor: str,
    ) -> uuid.UUID | None:
        """Persist smoke test result as an internal signed observation."""
        settings = get_settings()
        if settings.observation_signing_key is None:
            logger.warning(
                "smoke test observation skipped: signing key not configured",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
            )
            return None

        metric_value = {
            "value": 1.0 if passed else 0.0,
            "smoke_test": True,
            "checks": checks,
            "errors": errors,
            "endpoint": endpoint,
            "actor": actor,
        }
        item = ObservationInput(
            event_id=f"smoke:{deployment_id}",
            goal_id=goal_id,
            metric_name="smoke_pass",
            metric_value=metric_value,
            source="preview-smoke",
            definition_version="v1",
            is_bot=False,
            is_internal=True,
            observed_at=datetime.now(UTC),
        )
        service = ObservationService(
            self._sessions, settings.observation_signing_key.get_secret_value()
        )
        observation_id = await service.ingest(item, service.sign(item))
        logger.info(
            "smoke test observation",
            extra={
                "goal_id": str(goal_id),
                "deployment_id": str(deployment_id),
                "observation_id": str(observation_id),
                "passed": passed,
                "endpoint": endpoint,
                "check_count": len(checks),
                "error_count": len(errors),
                "actor": actor,
                "metric_name": "smoke_pass",
                "is_internal": True,
            },
        )
        return observation_id
