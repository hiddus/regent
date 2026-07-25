"""P3-B: Experiment platform + production deployment + self-improvement tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from regent.application.experiment_platform import ExperimentPlatform


class TestExperimentPlatform:
    """P3-B: Champion/Challenger experiment platform."""

    def test_create_experiment(self) -> None:
        """Create an experiment with champion and challenger variants."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test-exp",
            champion_variant="v1-single-agent",
            challenger_variant="v2-multi-agent",
            traffic_split=0.3,
            min_samples=10,
        )
        assert config.name == "test-exp"
        assert config.champion_variant == "v1-single-agent"
        assert config.challenger_variant == "v2-multi-agent"
        assert config.traffic_split == 0.3

    def test_record_and_analyze_inconclusive(self) -> None:
        """Analysis is INCONCLUSIVE when samples < min_samples."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test",
            champion_variant="champ",
            challenger_variant="chall",
            min_samples=30,
        )
        # Record only a few results
        for i in range(5):
            platform.record_result(config.experiment_id, variant="champ", metric="pass_rate", value=0.6)
            platform.record_result(config.experiment_id, variant="chall", metric="pass_rate", value=0.7)

        result = platform.analyze(config.experiment_id)
        assert result.recommendation == "INCONCLUSIVE"
        assert result.samples_champion == 5
        assert result.samples_challenger == 5

    def test_analyze_promote_challenger(self) -> None:
        """Challenger is promoted when significantly better."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test",
            champion_variant="champ",
            challenger_variant="chall",
            min_samples=10,
        )
        # Champion: 40% pass rate, Challenger: 80% pass rate
        for i in range(50):
            platform.record_result(
                config.experiment_id, variant="champ", metric="pass_rate",
                value=1.0 if i < 20 else 0.0,
            )
            platform.record_result(
                config.experiment_id, variant="chall", metric="pass_rate",
                value=1.0 if i < 40 else 0.0,
            )

        result = platform.analyze(config.experiment_id)
        assert result.challenger_score > result.champion_score
        assert result.significant is True
        assert result.recommendation == "PROMOTE_CHALLENGER"

    def test_analyze_keep_champion(self) -> None:
        """Champion is kept when significantly better."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test",
            champion_variant="champ",
            challenger_variant="chall",
            min_samples=10,
        )
        # Champion: 90% pass rate, Challenger: 30% pass rate
        for i in range(50):
            platform.record_result(
                config.experiment_id, variant="champ", metric="pass_rate",
                value=1.0 if i < 45 else 0.0,
            )
            platform.record_result(
                config.experiment_id, variant="chall", metric="pass_rate",
                value=1.0 if i < 15 else 0.0,
            )

        result = platform.analyze(config.experiment_id)
        assert result.champion_score > result.challenger_score
        assert result.significant is True
        assert result.recommendation == "KEEP_CHAMPION"

    def test_allocate_traffic_deterministic(self) -> None:
        """Traffic allocation is deterministic for same request_id."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test",
            champion_variant="champ",
            challenger_variant="chall",
            traffic_split=0.5,
        )
        v1 = platform.allocate_traffic(config.experiment_id, request_id="req-123")
        v2 = platform.allocate_traffic(config.experiment_id, request_id="req-123")
        assert v1 == v2  # deterministic

    def test_allocate_traffic_distribution(self) -> None:
        """Traffic allocation roughly matches the split ratio."""
        platform = ExperimentPlatform()
        config = platform.create_experiment(
            name="test",
            champion_variant="champ",
            challenger_variant="chall",
            traffic_split=0.3,
        )
        chall_count = sum(
            1 for i in range(1000)
            if platform.allocate_traffic(
                config.experiment_id, request_id=f"req-{i}",
            ) == "chall"
        )
        # Should be roughly 30% (allow wide margin)
        assert 150 < chall_count < 450


class TestProductionDeployment:
    """P3-B: Production deployment requires independent approval."""

    @pytest.mark.asyncio
    async def test_production_deployment_requires_approval(self) -> None:
        """Production deployment is rejected without approval."""
        from regent.application.release_service import ReleaseService

        mock_sessions = MagicMock()
        mock_session = AsyncMock()
        mock_sessions.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessions.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_candidate = MagicMock()
        mock_candidate.status = "APPROVED"
        mock_session.get = AsyncMock(return_value=mock_candidate)

        mock_provider = MagicMock()
        svc = ReleaseService(mock_sessions, mock_provider)

        result = await svc.request_production_deployment(
            uuid.uuid4(),
            actor="test-user",
            approval_id=None,  # no approval
        )
        assert result["status"] == "REJECTED"
        assert "approval required" in result["reason"]

    @pytest.mark.asyncio
    async def test_production_deployment_accepted_with_approval(self) -> None:
        """Production deployment is accepted with approval."""
        from regent.application.release_service import ReleaseService

        mock_sessions = MagicMock()
        mock_session = AsyncMock()
        mock_sessions.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessions.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_candidate = MagicMock()
        mock_candidate.status = "APPROVED"
        mock_session.get = AsyncMock(return_value=mock_candidate)

        mock_provider = MagicMock()
        svc = ReleaseService(mock_sessions, mock_provider)

        approval_id = uuid.uuid4()
        result = await svc.request_production_deployment(
            uuid.uuid4(),
            actor="test-user",
            approval_id=approval_id,
            strategy="canary",
            canary_percentage=20,
        )
        assert result["status"] == "ACCEPTED"
        assert result["strategy"] == "canary"
        assert result["canary_percentage"] == 20
