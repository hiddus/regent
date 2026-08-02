"""Unit tests for planned_path_policy."""

from regent.application.planned_path_policy import (
    expand_planned_paths,
    is_allowed_extra_path,
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
    assert not is_allowed_extra_path("random/dir/x.py")
    assert not is_allowed_extra_path(".regent/x")
    assert not is_allowed_extra_path("../etc/passwd")
    assert is_path_within_frozen_plan("src/app.py", ["src/app.py"])
    assert is_path_within_frozen_plan("tests/test_smoke.py", ["src/app.py"])
    assert not is_path_within_frozen_plan(".regent/x", ["src/app.py"])
    assert not is_path_within_frozen_plan("vendor/x.py", ["src/app.py"])
