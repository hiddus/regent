"""R7 post-deployment smoke test service.

Performs an HTTP reachability probe and records an internal observation.
Internal smoke signals must not satisfy product metric gates.

Phase 4.3: Extended with optional browser journey verification (G6).
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
from regent.infrastructure.browser_journey import (
    BrowserJourneyRunner,
    JourneyStep,
    JourneyStepKind,
)

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
    journey_passed: bool | None = None
    journey_detail: str = ""


class DeploymentSmokeTestService:
    """Post-deployment smoke test for R7 experiential gate."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        journey_runner: BrowserJourneyRunner | None = None,
    ) -> None:
        self._sessions = sessions
        self._journey_runner = journey_runner

    async def run_smoke_test(
        self,
        goal_id: uuid.UUID,
        deployment_id: uuid.UUID,
        endpoint: str,
        *,
        actor: str = "regent-core",
        journey_steps: list[JourneyStep] | None = None,
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

        # Phase 4.3: Browser journey verification (G6)
        journey_passed: bool | None = None
        journey_detail = ""
        if passed and endpoint and self._journey_runner is not None:
            steps = journey_steps or [
                JourneyStep(
                    kind=JourneyStepKind.PAGE_LOAD,
                    value=endpoint,
                    description="Load the preview page",
                ),
                JourneyStep(
                    kind=JourneyStepKind.ELEMENT_EXISTS,
                    selector="main, #root, #app, body",
                    description="Verify main content area exists",
                ),
            ]
            try:
                journey_result = await self._journey_runner.run_journey(endpoint, steps)
                journey_passed = journey_result.passed
                journey_detail = (
                    f"{journey_result.passed_steps}/{journey_result.total_steps} steps passed"
                )
                if not journey_passed:
                    errors.append(f"browser journey failed: {journey_detail}")
                    passed = False
                checks.append({
                    "check": "browser_journey",
                    "passed": journey_passed,
                    "detail": journey_detail,
                })
            except Exception as exc:
                journey_detail = f"journey error: {exc}"
                checks.append({
                    "check": "browser_journey",
                    "passed": False,
                    "detail": journey_detail,
                })
                errors.append(journey_detail)
                passed = False

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
            journey_passed=journey_passed,
            journey_detail=journey_detail,
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
