"""工作包3：场景边界及执笔上下文白名单。"""

from __future__ import annotations

from regent.novel.application.directing_script_loop import (
    _is_legitimate_repetition,
    _longest_common_substring,
    _trim_context_for_writer,
)


def test_trim_context_removes_recent_chapter_content():
    ctx = {
        "recent_chapters": [
            {"chapter_no": 1, "content": "整章正文..." * 100, "tail_excerpt": "尾部"},
            {"chapter_no": 2, "content": "另一章正文..." * 100},
        ],
        "actual_state": {"place": "钟表行"},
    }
    trimmed = _trim_context_for_writer(ctx)
    for ch in trimmed["recent_chapters"]:
        assert "content" not in ch
    assert trimmed["recent_chapters"][0]["tail_excerpt"] == "尾部"
    assert trimmed["actual_state"] == {"place": "钟表行"}


def test_trim_context_preserves_handoff_metadata():
    ctx = {
        "previous_handoff": {
            "chapter_no": 1,
            "content_hash": "abc123",
            "tail_excerpt": "他靠在门后",
            "actual_state": {"wound": "left_arm"},
            "some_large_field": "x" * 1000,
        },
    }
    trimmed = _trim_context_for_writer(ctx)
    handoff = trimmed["previous_handoff"]
    assert handoff["chapter_no"] == 1
    assert handoff["content_hash"] == "abc123"
    assert handoff["tail_excerpt"] == "他靠在门后"
    assert "some_large_field" not in handoff


def test_longest_common_substring_finds_long_match():
    a = "他靠在门后，左臂还在渗血，追兵的脚步停在门外。" * 3
    b = "不同的开头。" + "他靠在门后，左臂还在渗血，追兵的脚步停在门外。" * 3
    common = _longest_common_substring(a, b, min_len=40)
    assert "他靠在门后" in common
    assert len(common) >= 40


def test_longest_common_substring_short_match_ignored():
    a = "他靠在门后"
    b = "他靠在窗前"
    common = _longest_common_substring(a, b, min_len=40)
    assert common == ""


def test_longest_common_substring_empty():
    assert _longest_common_substring("", "abc", min_len=10) == ""
    assert _longest_common_substring("abc", "", min_len=10) == ""
    assert _longest_common_substring("abc", "def", min_len=10) == ""


def test_is_legitimate_repetition_detects_quote():
    text = "「他把钥匙放在桌上」这句话在前场出现过，本场再次引用。" * 3
    assert _is_legitimate_repetition(text) is True


def test_is_legitimate_repetition_detects_dialogue():
    text = "他说道：「我们必须尽快离开这里。」她回答说：「同意。」" * 3
    assert _is_legitimate_repetition(text) is True


def test_is_legitimate_repetition_detects_inscription_from_ledger():
    ledger = {
        "props": {
            "watch": {
                "partitions": {"back": "拾光钟表行，创立于二〇一〇年"}
            }
        }
    }
    text = "他翻转怀表，背面刻着「拾光钟表行，创立于二〇一〇年」。" * 3
    assert _is_legitimate_repetition(text, ledger) is True


def test_is_legitimate_repetition_rejects_plain_copy():
    text = "他靠在门后，左臂还在渗血，追兵的脚步停在门外。呼吸急促，心跳如鼓。" * 3
    assert _is_legitimate_repetition(text) is False
