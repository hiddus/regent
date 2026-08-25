from pathlib import Path

_SRC = Path("apps/regent-console/src")


def test_console_uuid_generation_has_legacy_browser_fallback() -> None:
    api = (_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    # The console uses crypto.randomUUID() for idempotency keys
    assert "crypto.randomUUID()" in api
    assert "console-" in api


def test_console_is_a_persistent_conversation_workspace() -> None:
    api = (_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    types = (_SRC / "lib" / "types.ts").read_text(encoding="utf-8")
    hooks = (_SRC / "hooks" / "useWorkspace.ts").read_text(encoding="utf-8")
    assert "/v1/conversations" in api
    assert "conversation_id" in types
    assert "PREVIEW_READY" in hooks
    assert "execution_stage" in hooks


def test_console_supports_explicit_steering_and_queued_messages() -> None:
    app = (_SRC / "App.tsx").read_text(encoding="utf-8")
    composer = (_SRC / "components" / "Composer.tsx").read_text(encoding="utf-8")
    assert "sendQueue" in app
    assert "queuedCount" in composer
    assert "[${intent}]" in composer
    for label in ("补充信息", "纠正方向", "询问进度", "继续执行"):
        assert label in composer
