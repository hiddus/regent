from regent.api.main import create_app


def test_product_creation_routes_are_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/v1/goals/{goal_id}/discovery-rounds" in paths
    assert "/v1/discovery-rounds/{round_id}" in paths
    assert "/v1/discovery-rounds/{round_id}/hypotheses" in paths
    assert "/v1/discovery-rounds/{round_id}/decision" in paths


def test_generation_routes_are_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/v1/resolution-plans/{resolution_plan_id}/generation-plans" in paths
    assert "/v1/generation-plans/{plan_id}/runs" in paths
    assert "/v1/generation-runs/{run_id}" in paths


def test_build_routes_are_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/v1/workspace-snapshots/{snapshot_id}/dependency-resolutions" in paths
    assert "/v1/workspace-snapshots/{snapshot_id}/builds" in paths
    assert "/v1/app-builds/{build_id}" in paths
    assert "/v1/app-builds/{build_id}/reconcile" in paths


def test_release_routes_are_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/v1/app-builds/{build_id}/release-candidates" in paths
    assert "/v1/release-candidates/{candidate_id}/decision" in paths
    assert "/v1/release-candidates/{candidate_id}/deployments" in paths
    assert "/v1/deployments/{deployment_id}" in paths
