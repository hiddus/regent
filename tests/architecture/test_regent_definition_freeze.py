"""Product authority freeze for Novel Engine (not legacy Regent Definition 3.0).

Old REGENT-DEFINITION-* files live only under docs/archive/. Active product
sources are Novel-Engine-PRD / Tech-Spec / Plan plus Regent Core boundary PRD.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NOVEL_PRD = ROOT / "Novel-Engine-PRD.md"
NOVEL_TECH = ROOT / "Novel-Engine-Tech-Spec.md"
NOVEL_PLAN = ROOT / "Novel-Engine-Plan.md"
CORE_PRD = ROOT / "Regent-PRD.md"
CORE_TECH = ROOT / "Regent-Technical-Spec.md"

ACTIVE_DEF_DIR = ROOT / "docs" / "definitions"
LEGACY_DEF_DIR = ROOT / "docs" / "archive" / "legacy-regent-2026" / "definitions"


def test_novel_engine_product_sources_exist() -> None:
    assert NOVEL_PRD.is_file(), f"missing {NOVEL_PRD}"
    assert NOVEL_TECH.is_file(), f"missing {NOVEL_TECH}"
    assert NOVEL_PLAN.is_file(), f"missing {NOVEL_PLAN}"
    assert CORE_PRD.is_file(), f"missing {CORE_PRD}"
    assert CORE_TECH.is_file(), f"missing {CORE_TECH}"


def test_core_prd_states_novel_is_only_external_product() -> None:
    text = CORE_PRD.read_text(encoding="utf-8")
    assert "Novel Engine" in text
    assert "唯一对外产品" in text or "唯一对外" in text


def test_legacy_definitions_are_archived_not_active() -> None:
    """Canonical 3.0 must not be revived under docs/definitions/."""
    if ACTIVE_DEF_DIR.is_dir():
        active = list(ACTIVE_DEF_DIR.glob("REGENT-DEFINITION-*.txt"))
        assert active == [], (
            "legacy definitions must not live under docs/definitions/; "
            f"found {[p.name for p in active]}"
        )
    # Archive may or may not be present in slim checkouts; if present, keep it there.
    if LEGACY_DEF_DIR.is_dir():
        archived = list(LEGACY_DEF_DIR.glob("REGENT-DEFINITION-3.0.txt"))
        assert archived, "expected archived REGENT-DEFINITION-3.0.txt under legacy path"


def test_novel_prd_does_not_require_legacy_definition_path() -> None:
    novel = NOVEL_PRD.read_text(encoding="utf-8")
    assert "docs/definitions/REGENT-DEFINITION-3.0.txt" not in novel
