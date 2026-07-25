"""Freeze guard for REGENT-DEFINITION-1.0 — CI fails on drift or second normative copy."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEF_PATH = ROOT / "docs" / "definitions" / "REGENT-DEFINITION-1.0.txt"
HASH_PATH = ROOT / "docs" / "definitions" / "REGENT-DEFINITION-1.0.sha256"
PRD_PATH = ROOT / "Regent-PRD-v2.md"
TECH_PATH = ROOT / "Regent-Technical-Spec-v2.md"

DEFINITION_ID = "REGENT-DEFINITION-1.0"


def normalize_definition_bytes(raw: bytes) -> bytes:
    text = unicodedata.normalize("NFC", raw.decode("utf-8"))
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return ("\n".join(lines) + "\n").encode("utf-8")


def content_hash(path: Path) -> str:
    return hashlib.sha256(normalize_definition_bytes(path.read_bytes())).hexdigest()


def extract_definition_text_field(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"^DEFINITION_TEXT=\n(.+?)(?=\n\nATTRIBUTE_|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "DEFINITION_TEXT block missing"
    return match.group(1).strip()


def test_definition_id_and_hash_file_match() -> None:
    assert DEF_PATH.is_file(), "canonical definition file missing"
    assert HASH_PATH.is_file(), "canonical hash file missing"
    assert DEFINITION_ID in DEF_PATH.read_text(encoding="utf-8")
    expected = HASH_PATH.read_text(encoding="utf-8").strip().split()[0]
    assert content_hash(DEF_PATH) == expected


def test_prd_quotes_canonical_definition_verbatim() -> None:
    body = extract_definition_text_field(DEF_PATH)
    prd = PRD_PATH.read_text(encoding="utf-8")
    assert DEFINITION_ID in prd
    assert body in prd, "PRD §1.1 must quote DEFINITION_TEXT verbatim from canonical file"
    assert "docs/definitions/REGENT-DEFINITION-1.0.txt" in prd


def test_tech_spec_references_without_redefining() -> None:
    tech = TECH_PATH.read_text(encoding="utf-8")
    assert DEFINITION_ID in tech
    body = extract_definition_text_field(DEF_PATH)
    # Tech spec may mention the ID but must not embed the full normative paragraph.
    assert body not in tech, "Technical Spec must reference definition, not copy DEFINITION_TEXT"


def test_no_second_normative_definition_file() -> None:
    forbidden = list(ROOT.rglob("*DEFINITION*.txt")) + list(ROOT.rglob("*DEFINITION*.md"))
    allowed = {
        DEF_PATH.resolve(),
        (ROOT / "docs" / "definitions" / "README.md").resolve(),
    }
    extras = [
        path
        for path in forbidden
        if path.is_file()
        and path.resolve() not in allowed
        and "REGENT-DEFINITION" in path.name
        and "node_modules" not in path.parts
        and ".venv" not in path.parts
    ]
    assert extras == [], f"extra definition artifacts: {extras}"
