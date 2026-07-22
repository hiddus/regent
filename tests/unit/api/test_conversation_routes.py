from regent.api.main import create_app


def test_conversation_routes_are_exposed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert {
        "/v1/conversations",
        "/v1/conversations/{conversation_id}",
        "/v1/conversations/{conversation_id}/goal/{goal_id}",
        "/v1/conversations/{conversation_id}/messages",
    } <= paths
