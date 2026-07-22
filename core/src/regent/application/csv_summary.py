import csv
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from regent.infrastructure.artifact_store import FileArtifactStore, StoredArtifact


@dataclass(frozen=True, slots=True)
class CsvSummaryEvidence:
    input_hash: str
    output_hash: str
    row_count: int
    valid_count: int
    invalid_count: int
    total_amount: str
    artifact: StoredArtifact


def execute_csv_summary(
    *,
    goal_id: uuid.UUID,
    input_path: Path,
    artifacts: FileArtifactStore,
) -> CsvSummaryEvidence:
    input_bytes = input_path.read_bytes()
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    row_count = 0
    valid_count = 0
    invalid_count = 0
    total = Decimal("0")

    with input_path.open("r", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            row_count += 1
            try:
                amount = Decimal(row["amount"])
            except (InvalidOperation, KeyError):
                invalid_count += 1
                continue
            valid_count += 1
            total += amount

    payload = {
        "row_count": row_count,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "total_amount": float(total),
    }
    output = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    stored = artifacts.put(goal_id, "output/summary.json", output)
    return CsvSummaryEvidence(
        input_hash=input_hash,
        output_hash=stored.content_hash,
        row_count=row_count,
        valid_count=valid_count,
        invalid_count=invalid_count,
        total_amount=str(total),
        artifact=stored,
    )
