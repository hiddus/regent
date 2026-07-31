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
    user_blob = messages[1].content or ""
    assert "marker-conversation" in user_blob
    assert "marker-evidence" in user_blob
