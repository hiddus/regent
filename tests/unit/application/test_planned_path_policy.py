"""Unit tests for planned_path_policy."""

from regent.application.planned_path_policy import (
    expand_planned_paths,
    is_allowed_extra_path,
    is_incidental_byproduct,
    is_path_within_frozen_plan,
)


def test_expand_planned_paths_adds_scaffold() -> None:
    paths = expand_planned_paths(["src/app.py"], goal_scale="SMALL")
    assert "src/app.py" in paths
    assert "requirements.txt" in paths
    assert "README.md" in paths
    assert "static/style.css" in paths
    assert "templates/index.html" in paths


def test_allowed_extra_and_frozen_plan() -> None:
    assert is_allowed_extra_path("templates/x.html")
    assert is_allowed_extra_path("static/a.css")
    assert is_allowed_extra_path("src/lib/helper.py")
    assert is_allowed_extra_path("src/Component.tsx")
    assert is_allowed_extra_path("src/App.vue")
    assert is_allowed_extra_path("src/schema.sql")
    # Deliverables under a top-level source/ directory are common scaffolds.
    assert is_allowed_extra_path("source/static/engine.js")
    assert is_allowed_extra_path("source/README.md")
    assert is_allowed_extra_path("source/selftest.py")
    assert is_allowed_extra_path("source/deploy.sh")
    assert is_allowed_extra_path("source/nginx.conf")
    assert not is_allowed_extra_path("source/.regent/x")
    assert not is_allowed_extra_path("random/dir/x.py")
    assert not is_allowed_extra_path(".regent/x")
    assert not is_allowed_extra_path("../etc/passwd")
    assert is_path_within_frozen_plan("src/app.py", ["src/app.py"])
    assert is_path_within_frozen_plan("tests/test_smoke.py", ["src/app.py"])
    assert is_path_within_frozen_plan(
        "source/static/data.js", ["src/app.py", "README.md"]
    )
    assert not is_path_within_frozen_plan(".regent/x", ["src/app.py"])
    assert not is_path_within_frozen_plan("vendor/x.py", ["src/app.py"])


def test_incidental_byproduct_detection() -> None:
    """Tool-run caches must be dropped, not denied, by the frozen-plan gate."""
    assert is_incidental_byproduct(".pytest_cache/v/cache/lastfailed")
    assert is_incidental_byproduct("tests/__pycache__/test_smoke.cpython-312.pyc")
    assert is_incidental_byproduct("src/__pycache__/app.pyc")
    assert is_incidental_byproduct("source/lib/__pycache__/x.pyo")
    assert is_incidental_byproduct(".ruff_cache/0.15/CACHEDIR.TAG")
    assert is_incidental_byproduct(".regent_run_ledger.json")
    assert is_incidental_byproduct(".regent_agent_transcript.json")
    assert is_incidental_byproduct(".regent_budget_exhausted.json")
    assert not is_incidental_byproduct(".regent/x")
    assert not is_incidental_byproduct("sub/.regent_state.json")
    assert not is_incidental_byproduct("src/app.py")
    assert not is_incidental_byproduct("README.md")
    assert not is_incidental_byproduct("source/static/engine.js")
