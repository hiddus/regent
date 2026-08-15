from regent.application.goal_readiness import (
    blocking_unknowns,
    effective_feasibility_verdict,
)


def test_advisory_unknowns_do_not_block_goal_lock() -> None:
    unknowns = [
        {"question": "WCAG final level", "blocking": False},
        {"question": "reading reminder preference", "blocking": False},
    ]
    assert blocking_unknowns(unknowns) == []
    assert effective_feasibility_verdict(
        "REVISION_REQUIRED", rounds=2, unknowns=unknowns
    ) == "FEASIBLE"


def test_explicit_and_legacy_blockers_remain_fail_closed() -> None:
    explicit = {"question": "approved budget", "blocking": True}
    unknowns = [explicit, "data access"]
    assert blocking_unknowns(unknowns) == unknowns
    assert effective_feasibility_verdict(
        "REVISION_REQUIRED", rounds=3, unknowns=unknowns
    ) == "REVISION_REQUIRED"


def test_not_feasible_is_never_promoted() -> None:
    assert effective_feasibility_verdict(
        "NOT_FEASIBLE", rounds=3, unknowns=[]
    ) == "NOT_FEASIBLE"
