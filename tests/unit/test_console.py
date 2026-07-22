from pathlib import Path


def test_console_uuid_generation_has_legacy_browser_fallback() -> None:
    console = Path("apps/regent-console/index.html").read_text(encoding="utf-8")
    assert "typeof c.randomUUID==='function'" in console
    assert "typeof c.getRandomValues==='function'" in console
    assert "'console-'+newId()" in console
    assert "'console-'+crypto.randomUUID()" not in console


def test_console_is_a_persistent_conversation_workspace() -> None:
    console = Path("apps/regent-console/index.html").read_text(encoding="utf-8")
    assert "/v1/conversations" in console
    assert "所有对话、执行与决策均持久保存" in console
    assert "PREVIEW_READY" in console
    assert "conversation_id" in console
