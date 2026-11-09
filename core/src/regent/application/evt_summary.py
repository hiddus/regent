import hashlib
import json
import uuid
import zlib
from dataclasses import dataclass

from regent.infrastructure.artifact_store import FileArtifactStore, StoredArtifact


@dataclass(frozen=True, slots=True)
class EvtSummaryEvidence:
    input_hash: str
    output_hash: str
    valid_count: int
    invalid_count: int
    artifact: StoredArtifact


def execute_evt_summary(
    *, goal_id: uuid.UUID, input_text: str, artifacts: FileArtifactStore
) -> EvtSummaryEvidence:
    input_bytes = input_text.encode("utf-8")
    valid_count = 0
    invalid_count = 0
    for raw_line in input_text.splitlines():
        if not raw_line.strip():
            continue
        fields = raw_line.split("|")
        if len(fields) != 4:
            invalid_count += 1
            continue
        payload, supplied_crc = "|".join(fields[:3]), fields[3].strip().lower()
        expected_crc = f"{zlib.crc32(payload.encode('utf-8')) & 0xFFFFFFFF:08x}"
        if supplied_crc == expected_crc:
            valid_count += 1
        else:
            invalid_count += 1
    output = json.dumps(
        {"valid_count": valid_count, "invalid_count": invalid_count},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    stored = artifacts.put(goal_id, "output/evt-summary.json", output)
    return EvtSummaryEvidence(
        input_hash=hashlib.sha256(input_bytes).hexdigest(),
        output_hash=stored.content_hash,
        valid_count=valid_count,
        invalid_count=invalid_count,
        artifact=stored,
    )
