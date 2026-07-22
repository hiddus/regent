import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    ExperimentManifestModel,
    ExperimentRunModel,
    ProductDecisionRecordModel,
)

router = APIRouter(prefix="/v1/experiments", tags=["experiments"])


@router.get("/{manifest_id}/status")
async def experiment_status(manifest_id: uuid.UUID, request: Request) -> dict[str, Any]:
    async with request.app.state.sessions() as session:
        manifest = await session.get(ExperimentManifestModel, manifest_id)
        if manifest is None:
            raise DomainError(ErrorCode.NOT_FOUND, "experiment manifest not found")
        completed = await session.scalar(
            select(func.count())
            .select_from(ExperimentRunModel)
            .where(ExperimentRunModel.manifest_id == manifest_id)
        )
        decision = await session.scalar(
            select(ProductDecisionRecordModel).where(
                ProductDecisionRecordModel.manifest_id == manifest_id
            )
        )
        expected = (
            len(manifest.manifest["tasks"])
            * len(manifest.manifest["modes"])
            * int(manifest.manifest["repetitions"])
        )
        return {
            "manifest_id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "status": manifest.status,
            "manifest_digest": manifest.digest,
            "manifest_signature": manifest.signature,
            "completed_runs": completed or 0,
            "expected_runs": expected,
            "decision": None
            if decision is None
            else {
                "id": decision.id,
                "decision": decision.decision,
                "rationale": decision.rationale,
                "metrics": decision.metrics,
                "evidence_digest": decision.evidence_digest,
                "signature": decision.signature,
            },
        }
