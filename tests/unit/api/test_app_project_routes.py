from regent.api.main import create_app
from regent.infrastructure.models import AppProjectModel, GoalSpecModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_app_project_routes_are_exposed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert {
        "/v1/app-projects",
        "/v1/app-projects/drafts",
        "/v1/app-projects/{project_id}",
        "/v1/app-projects/{project_id}/confirm",
        "/v1/app-projects/{project_id}/guidance",
        "/v1/app-projects/{project_id}/status",
        "/v1/app-projects/{project_id}/goals/{goal_id}/preview-releases",
        "/v1/preview-releases/{release_id}",
        "/v1/preview-releases/{release_id}/events",
        "/v1/preview-releases/{release_id}/evaluate",
        "/v1/self-improvement-runs",
        "/v1/self-improvement-runs/{run_id}",
        "/v1/self-improvement-runs/{run_id}/decision",
    } <= paths


def test_app_project_and_goal_spec_confirmation_constraints_exist() -> None:
    project_ddl = str(CreateTable(AppProjectModel.__table__).compile(dialect=postgresql.dialect()))
    spec_ddl = str(CreateTable(GoalSpecModel.__table__).compile(dialect=postgresql.dialect()))
    assert "ck_app_projects_status" in project_ddl
    assert "ck_goal_specs_status" in spec_ddl
    assert "content_hash" in spec_ddl
    assert "confirmed_by" in spec_ddl
