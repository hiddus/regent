"""Offline counterexamples; no network or business-data writes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "src"))
from regent.novel.application import direction as d
from regent.novel.domain import prose_patch as pp
from regent.novel.domain.scene_requirements import REQUIREMENTS_VERSION, freeze_scene_requirements

base = "A\n\nKEEP\n\nC\n\nTAIL"
noncontiguous_error = None
try:
    pp.apply_patch(
        base_text=base,
        base_hash=pp.content_hash(base),
        paragraphs=pp.split_paragraphs(base),
        patch_hash=pp.content_hash(base),
        replacements=[{"paragraph_ids": ["p0000", "p0002"], "text": "NEW"}],
    )
except Exception as exc:  # noqa: BLE001 — probe
    noncontiguous_error = str(exc)

split_merged = pp.apply_patch(
    base_text=base,
    base_hash=pp.content_hash(base),
    paragraphs=pp.split_paragraphs(base),
    patch_hash=pp.content_hash(base),
    replacements=[
        {"paragraph_ids": ["p0000"], "text": "NEW"},
        {"paragraph_ids": ["p0002"], "text": "C2"},
    ],
)

requirements = freeze_scene_requirements(
    [
        {
            "reader_visible": True,
            "statement": "door closed",
            "state_changes": {"door": "closed"},
        }
    ]
)
report = d.SceneValidation(
    passed=True,
    facts=[d.VerifiedFact(statement="rain", quote="rain")],
)
defects = d._validation_report_defects(
    report, "rain", requirements, protocol_version=REQUIREMENTS_VERSION
)
issues = d._substantive_validation_issues(report, "rain", requirements, {})
print(
    json.dumps(
        {
            "noncontiguous_patch": {
                "rejected": bool(noncontiguous_error),
                "error": noncontiguous_error,
                "split_keeps_middle": "KEEP" in split_merged,
            },
            "missing_required_state": {
                "report_defects": defects,
                "issues": issues,
                "accept_expression": report.passed
                and not defects
                and not issues
                and bool(report.facts),
            },
        },
        indent=2,
        ensure_ascii=False,
    )
)
