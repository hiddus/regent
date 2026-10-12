"""工作包4：修订复验、证据及去重。"""

from __future__ import annotations

from regent.novel.application.chapter_accept import collect_accepted_facts


def test_collect_accepted_facts_deduplicates_by_statement():
    production = {
        "accepted": [0, 1],
        "takes": [
            {
                "validation": {
                    "facts": [
                        {"statement": "他靠在门后"},
                        {"statement": "左臂还在渗血"},
                    ]
                }
            },
            {
                "validation": {
                    "facts": [
                        {"statement": "他靠在门后"},  # duplicate
                        {"statement": "追兵停在门外"},
                    ]
                }
            },
        ],
    }
    facts = collect_accepted_facts(production)
    statements = [f["statement"] for f in facts]
    assert statements == ["他靠在门后", "左臂还在渗血", "追兵停在门外"]
    assert len(facts) == 3


def test_collect_accepted_facts_preserves_order():
    production = {
        "accepted": [0, 1, 2],
        "takes": [
            {"validation": {"facts": [{"statement": "first"}]}},
            {"validation": {"facts": [{"statement": "second"}]}},
            {"validation": {"facts": [{"statement": "third"}]}},
        ],
    }
    facts = collect_accepted_facts(production)
    assert [f["statement"] for f in facts] == ["first", "second", "third"]


def test_collect_accepted_facts_handles_empty():
    assert collect_accepted_facts({"accepted": [], "takes": []}) == []
    assert collect_accepted_facts({}) == []


def test_collect_accepted_facts_skips_empty_statements():
    production = {
        "accepted": [0],
        "takes": [
            {
                "validation": {
                    "facts": [
                        {"statement": ""},
                        {"statement": "valid"},
                        {"statement": "  "},
                    ]
                }
            }
        ],
    }
    facts = collect_accepted_facts(production)
    assert len(facts) == 1
    assert facts[0]["statement"] == "valid"


def test_collect_accepted_facts_attaches_source_tracking():
    production = {
        "accepted": [0],
        "takes": [
            {
                "scene_index": 2,
                "take_no": 1,
                "prose_versions": ["他靠在门后，左臂还在渗血。"],
                "validation": {
                    "facts": [
                        {"statement": "他靠在门后"},
                    ]
                },
            }
        ],
    }
    facts = collect_accepted_facts(production)
    assert len(facts) == 1
    assert facts[0]["statement"] == "他靠在门后"
    source = facts[0]["source_tracking"]
    assert source["take_id"] == "scene2:take1"
    assert source["accepted_index"] == 0
    assert len(source["source_hash"]) == 64  # SHA-256 hex digest
