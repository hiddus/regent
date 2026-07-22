"""Evidence source connectors for product discovery."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from regent.application.p1_ports import EvidenceSourceRequest, EvidenceSourceSnapshot
from regent.infrastructure.artifact_store import FileArtifactStore


class InMemoryEvidenceSourceConnector:
    """Deterministic connector for orchestration tests; performs no network side effects."""

    def __init__(self, snapshots: Iterable[EvidenceSourceSnapshot]) -> None:
        self._snapshots = tuple(snapshots)
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        return list(self._snapshots)


class GoalIntentEvidenceConnector:
    """Persist the goal query as an immutable SourceSnapshot artifact.

    P1 treats the confirmed user Goal text as first-class evidence of declared
    intent. This is not model commons knowledge: content is hashed, stored under
    the artifact store, and cited by discovery via evidence UUIDs.
    """

    def __init__(self, artifacts: FileArtifactStore) -> None:
        self._artifacts = artifacts
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        query = request.query.strip()
        if not query:
            return []

        scope = uuid.uuid5(uuid.NAMESPACE_URL, f"regent:evidence:{request.correlation_id}")
        captured_at = datetime.now(UTC).isoformat()
        # Hash only stable intent content so repeated fetches are idempotent.
        stable_payload = {
            "kind": "goal-intent",
            "query": query,
            "source_types": list(request.source_types),
            "correlation_id": request.correlation_id,
        }
        content = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        stored = self._artifacts.put(scope, f"evidence/{digest[:2]}/{digest}.json", content)
        return [
            EvidenceSourceSnapshot(
                source_uri=f"regent://goal-intent/{request.correlation_id}",
                captured_at=captured_at,
                content_artifact_uri=stored.uri,
                content_hash=stored.content_hash,
                metadata={
                    "connector": "goal-intent-v1",
                    "kind": "goal-intent",
                    "byte_size": stored.size,
                    "budget": dict(request.budget),
                },
            )
        ]


class CompositeEvidenceSourceConnector:
    """Fan out to multiple connectors and merge snapshots."""

    def __init__(self, connectors: list[object]) -> None:
        self._connectors = list(connectors)

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        snapshots: list[EvidenceSourceSnapshot] = []
        seen: set[str] = set()
        for connector in self._connectors:
            for item in await connector.fetch(request):  # type: ignore[attr-defined]
                if item.content_hash in seen:
                    continue
                seen.add(item.content_hash)
                snapshots.append(item)
        return snapshots
