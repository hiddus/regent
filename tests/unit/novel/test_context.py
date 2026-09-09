# Chinese fixtures deliberately use full-width punctuation.
# ruff: noqa: RUF001

"""上下文装配的行为测试（G-02 确定性、G-03 信息集隔离）。"""

from regent.novel.domain import context as c


FACTS = [
    {"statement": "PUBLIC", "known_by": ["ALL"]},
    {"statement": "主角知道", "known_by": ["主角"]},
    {"statement": "同伴知道", "known_by": ["同伴"]},
    # 缺失可见性与显式 None 都是“无人知情”，不得隐式公开。
    {"statement": "未标注"},
    {"statement": "空标注", "known_by": None},
]


def test_same_sources_produce_same_manifest_hash():
    kwargs = dict(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona="主角",
        cast={"主角": {"voice": "克制"}},
        direction={"persona": "主角"},
        setting="旧宅",
        canon=FACTS,
        observations=[],
        turn=0,
    )
    first = c.compile_actor_context(**kwargs)
    second = c.compile_actor_context(**kwargs)
    assert first.manifest_hash == second.manifest_hash
    assert first.payload == second.payload


def test_projection_hash_ignores_key_insertion_order():
    built = c.compile_actor_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona="主角",
        cast={"主角": {"voice": "克制", "identity": {}}},
        direction={"persona": "主角"},
        setting="旧宅",
        canon=FACTS,
        observations=[],
        turn=0,
    )
    reordered = c.compile_actor_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona="主角",
        cast={"主角": {"identity": {}, "voice": "克制"}},
        direction={"persona": "主角"},
        setting="旧宅",
        canon=FACTS,
        observations=[],
        turn=0,
    )
    assert built.manifest_hash == reordered.manifest_hash


def test_actor_sees_only_own_information_set():
    compiled = c.compile_actor_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona="主角",
        cast={"主角": {}},
        direction={},
        setting="旧宅",
        canon=FACTS,
        observations=[{"statement": "同伴低声说话", "known_by": ["同伴"]}],
        turn=0,
    )
    known = {fact["statement"] for fact in compiled.payload["known_facts"]}
    assert known == {"PUBLIC", "主角知道"}
    assert compiled.payload["observations"] == []


def test_writer_receives_only_reader_visible_events():
    compiled = c.compile_writer_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        narrative={"viewpoint": "主角"},
        events=[
            {"statement": "可见", "reader_visible": True},
            {"statement": "隐藏", "reader_visible": False},
        ],
        voices={"主角": "克制"},
        previous_ending="",
        target_characters=800,
        director_instruction="",
    )
    assert [e["statement"] for e in compiled.payload["events"]] == ["可见"]


def test_director_watch_strips_private_reasoning():
    compiled = c.compile_director_performance_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        brief={},
        events=[],
        performances=[{"persona": "主角", "actions": ["伸手"], "private_reasoning": "他在撒谎"}],
        rule_issues=[],
        remaining_turns=2,
    )
    assert "他在撒谎" not in c.canonical(compiled.payload)


def test_manifest_records_source_hash_not_content():
    compiled = c.compile_actor_context(
        work_id="w",
        branch_id="b",
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona="主角",
        cast={"主角": {}},
        direction={},
        setting="旧宅",
        canon=FACTS,
        observations=[],
        turn=0,
    )
    kinds = {source.kind for source in compiled.manifest.sources}
    assert {"canon", "observations", "persona", "brief"} <= kinds
    assert all(source.hash for source in compiled.manifest.sources)
    # 绑定关系进入 manifest，便于定位泄露发生在哪一场、哪一次 take。
    assert compiled.manifest.binding["scene_index"] == "0"
    assert compiled.manifest.binding["take_no"] == "1"
