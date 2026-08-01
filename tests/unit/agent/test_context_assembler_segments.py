"""CD-4.3: conversation/evidence retrieval segments in ContextAssembler."""

from __future__ import annotations

from unittest.mock import MagicMock

from regent.agent.context_assembler import ContextAssembler


def _toolkit() -> MagicMock:
    toolkit = MagicMock()
    toolkit.list_tree.return_value = []
    toolkit.recent_writes = []
    toolkit.todos = []
    return toolkit


def test_conversation_segment_empty_by_default() -> None:
    assembler = ContextAssembler(plan={}, toolkit=_toolkit())
    assert assembler._conversation_segment() == ""  # noqa: SLF001


def test_conversation_segment_renders_snippets() -> None:
    plan = {
        "conversation_snippets": [
            {"role": "user", "content": "请用 REST 不要 GraphQL"},
            "补充：deadline 是下周",
        ]
    }
    assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
    segment = assembler._conversation_segment()  # noqa: SLF001
    assert "CONVERSATION CONTEXT" in segment
    assert "REST" in segment
    assert "deadline" in segment


def test_evidence_segment_empty_by_default() -> None:
    assembler = ContextAssembler(plan={}, toolkit=_toolkit())
    assert assembler._evidence_segment() == ""  # noqa: SLF001


def test_evidence_segment_renders_snippets() -> None:
    plan = {
        "evidence_snippets": [
            {"title": "TechCrunch", "source": "https://techcrunch.com/feed/", "summary": "AI news"},
        ]
    }
    assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
    segment = assembler._evidence_segment()  # noqa: SLF001
    assert "EVIDENCE" in segment
    assert "TechCrunch" in segment
    assert "AI news" in segment


def test_assemble_includes_new_segments_in_user_message() -> None:
    plan = {
        "conversation_snippets": [{"role": "user", "content": "marker-conversation"}],
        "evidence_snippets": [{"title": "marker-evidence", "summary": "x"}],
    }
    assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
    messages = assembler.assemble(turn=0, conversation=[])
    static_blob = next(m.content or "" for m in messages if m.role == "user")
    assert "marker-conversation" in static_blob
    assert "marker-evidence" in static_blob


def test_assemble_includes_skill_guidance() -> None:
    plan = {
        "goal_anchor_text": "Flask todo",
        "skill_guidance": "### Skill web-app-scaffold\nUse SQLite persistence.",
        "skill_refs": [{"skill_id": "web-app-scaffold", "version": "1.0.0"}],
    }
    assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
    messages = assembler.assemble(turn=0, conversation=[])
    static_blob = next(m.content or "" for m in messages if m.role == "user")
    assert "SKILL GUIDANCE" in static_blob
    assert "web-app-scaffold" in static_blob
    assert "SQLite persistence" in static_blob


def test_assemble_omits_skill_segment_when_empty() -> None:
    assembler = ContextAssembler(plan={"goal_anchor_text": "x"}, toolkit=_toolkit())
    messages = assembler.assemble(turn=0, conversation=[])
    static_blob = next(m.content or "" for m in messages if m.role == "user")
    assert "SKILL GUIDANCE" not in static_blob


def test_assemble_layout_puts_volatile_after_conversation() -> None:
    from regent.agent.types import ChatMessage

    toolkit = _toolkit()
    toolkit.list_tree.return_value = ["src/app.py"]
    toolkit.recent_writes = ["src/app.py"]
    toolkit.read_text.return_value = "SHOULD_NOT_INLINE_FULLTEXT" * 20
    assembler = ContextAssembler(
        plan={"goal_anchor_text": "stable-goal"},
        toolkit=toolkit,
    )
    conversation = [ChatMessage(role="assistant", content="mid-turn")]
    messages = assembler.assemble(turn=1, conversation=conversation)
    roles = [m.role for m in messages]
    assert roles[0] == "system"
    assert roles[1] == "user"  # static
    assert roles[2] == "assistant"  # conversation
    assert roles[3] == "user"  # volatile
    assert "stable-goal" in (messages[1].content or "")
    assert "WORKSPACE" in (messages[3].content or "")
    assert "SHOULD_NOT_INLINE_FULLTEXT" not in (messages[3].content or "")
    assert "read_file" in (messages[3].content or "")


def test_static_prefix_stable_after_workspace_write() -> None:
    toolkit = _toolkit()
    toolkit.list_tree.return_value = []
    toolkit.recent_writes = []
    assembler = ContextAssembler(
        plan={
            "goal_anchor_text": "Flask todo",
            "skill_guidance": "Use SQLite.",
        },
        toolkit=toolkit,
    )
    before = assembler.static_prefix_text()
    toolkit.list_tree.return_value = ["src/app.py", "requirements.txt"]
    toolkit.recent_writes = ["src/app.py"]
    after = assembler.static_prefix_text()
    assert before == after
    assert "src/app.py" not in before
    volatile = assembler.volatile_suffix_text(turn=2)
    assert "src/app.py" in volatile


def test_goal_reminder_lives_in_volatile_suffix_not_static() -> None:
    assembler = ContextAssembler(
        plan={"goal_anchor_text": "remind-me"},
        toolkit=_toolkit(),
    )
    static = assembler.static_prefix_text()
    volatile = assembler.volatile_suffix_text(turn=10)
    assert "GOAL REMINDER" not in static
    assert "GOAL REMINDER" in volatile
    assert "remind-me" in volatile
