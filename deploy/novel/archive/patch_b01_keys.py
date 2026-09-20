"""同步 B-01 的键口径变更：promise/belief/director_note 的键现在带内容指纹。

只改「构造/查找键」的方式，不放宽任何断言语义。每条 old 断言出现次数为 1。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        count = text.count(old)
        assert count == 1, f"{rel}: old 出现 {count} 次，不是 1 次 -> {old[:70]!r}"
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print(f"patched {rel}")


# ---------------------------------------------------------------------------
# tests/unit/novel/test_long_term_memory.py
# ---------------------------------------------------------------------------
patch(
    "tests/unit/novel/test_long_term_memory.py",
    [
        (
            """    item = next(r for r in rows if r.subject == "玉佩")
    assert item.state == "RESOLVED"
    assert int(item.source_chapter_no) == 4  # 兑现留痕，来源可查""",
            """    item = next(r for r in rows if r.subject == "玉佩")
    assert item.state == "RESOLVED"
    # 种下章与兑现章分开留痕：「第几章埋的」和「第几章收的」都要能查
    assert int(item.source_chapter_no) == 1
    assert int(item.resolved_chapter_no) == 4""",
        ),
        (
            """    edges = [
        (domain.item_key("rule", "境界"), domain.item_key("promise", "密约")),
        (domain.item_key("promise", "密约"), domain.item_key("promise", "远征")),
    ]""",
            """    key = {i.subject: i.key for i in items}
    edges = [
        (key["境界"], key["密约"]),
        (key["密约"], key["远征"]),
    ]""",
        ),
        (
            """        await memory_app.link_memory(
            s, work=work,
            upstream_key=domain.item_key("rule", "境界"),
            downstream_key=domain.item_key("promise", "密约"),
        )""",
            """        await memory_app.link_memory(
            s, work=work,
            upstream_key=domain.item_key("rule", "境界"),
            downstream_key=domain.item_key("promise", "密约", "密约待兑现"),
        )""",
        ),
    ],
)

# ---------------------------------------------------------------------------
# tests/unit/novel/test_memory_from_real_facts.py
# ---------------------------------------------------------------------------
patch(
    "tests/unit/novel/test_memory_from_real_facts.py",
    [
        (
            """    promise = rows["promise:节点1"]""",
            """    promise = rows[domain.item_key("promise", "节点1", "节点1承诺")]""",
        ),
    ],
)
path = ROOT / "tests/unit/novel/test_memory_from_real_facts.py"
text = path.read_text(encoding="utf-8")
if "from regent.novel.domain import memory as domain" not in text:
    old_import = "from regent.novel.application import works\n"
    assert text.count(old_import) == 1
    text = text.replace(
        old_import, old_import + "from regent.novel.domain import memory as domain\n", 1
    )
    path.write_text(text, encoding="utf-8")
    print("patched import in test_memory_from_real_facts.py")

# ---------------------------------------------------------------------------
# tests/unit/novel/test_path_change_scoped_memory.py
# ---------------------------------------------------------------------------
patch(
    "tests/unit/novel/test_path_change_scoped_memory.py",
    [
        (
            """def _capture_events(monkeypatch, target_module) -> list[dict]:""",
            '''def _promise_key(subject: str, content: str | None = None) -> str:
    """承诺类键带内容指纹（B-01）：构造键必须给出内容，否则查不到。"""
    return domain.item_key("promise", subject, content or f"{subject}尚未兑现")


def _capture_events(monkeypatch, target_module) -> list[dict]:''',
        ),
        (
            """                upstream_key=domain.item_key("promise", upstream),
                downstream_key=domain.item_key("promise", downstream),""",
            """                upstream_key=_promise_key(upstream),
                downstream_key=_promise_key(downstream),""",
        ),
        (
            """    expected_downstream = {
        domain.item_key("promise", t) for t in TITLES[CHANGED_INDEX:]
    }
    assert invalidated == expected_downstream
    # 上游（改它不需要推翻上游）、无关支线、世界规则全部存活
    assert domain.item_key("promise", TITLES[0]) in alive
    assert domain.item_key("promise", SIDE_THREAD) in alive
    assert domain.item_key("rule", RULE_SUBJECT) in alive""",
            """    expected_downstream = {
        _promise_key(t) for t in TITLES[CHANGED_INDEX:]
    }
    assert invalidated == expected_downstream
    # 上游（改它不需要推翻上游）、无关支线、世界规则全部存活
    assert _promise_key(TITLES[0]) in alive
    assert _promise_key(SIDE_THREAD, "支线旧宅密室另有隐情") in alive
    assert domain.item_key("rule", RULE_SUBJECT) in alive""",
        ),
        (
            """    assert domain.item_key("promise", SIDE_THREAD) in invalidated
    assert domain.item_key("promise", TITLES[0]) in invalidated
    assert domain.item_key("rule", RULE_SUBJECT) in alive""",
            """    assert _promise_key(SIDE_THREAD, "支线旧宅密室另有隐情") in invalidated
    assert _promise_key(TITLES[0]) in invalidated
    assert domain.item_key("rule", RULE_SUBJECT) in alive""",
        ),
        (
            """    assert invalidated == {domain.item_key("promise", t) for t in TITLES[CHANGED_INDEX:]}
    assert domain.item_key("promise", TITLES[0]) in {""",
            """    assert invalidated == {_promise_key(t) for t in TITLES[CHANGED_INDEX:]}
    assert _promise_key(TITLES[0]) in {""",
        ),
    ],
)
