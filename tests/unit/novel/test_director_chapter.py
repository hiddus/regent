"""director_chapter：一次成章，硬门，最多一次改稿。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from regent.novel.application import executor as executor_app
from regent.novel.application.directing_chapter import (
    CHAPTER_FIT_SYSTEM,
    CHAPTER_PHASES,
    FIT_TARGET_CHARS,
    MAX_CHAPTER_CHARS,
    MIN_CHAPTER_CHARS,
    ChapterFacts,
    ChapterFitCut,
    ChapterPlan,
    ChapterProse,
    ArcReview,
    MethodVerdict,
    accept_draft,
    apply_fit_cut,
    build_write_payload,
    chapter_node_for_run,
    check_chapter,
    drop_copied_span,
    drop_repeated_span,
    dedupe_chapter_text,
    grounded_facts,
    length_brief,
    load_cast,
    fit_chapter_length,
    fit_protected,
    fit_seam_break,
    fit_units,
    prepare_chapter_canon,
    produce_chapter_tick,
    PlanRepeated,
    min_chapters_per_stage,
    repeated_span,
    max_chapters_per_stage,
    name_warnings,
    rhythm_mismatch,
    stage_position,
    stray_names,
    write_canon,
)
from regent.novel.application.generation import BookRhythm, CanonExtraction, rhythm_problems
from regent.novel.application.chapter_review import validate_chapter
from regent.novel.application.directing_protocol import production_protocol
from regent.novel.application.directing_scene_loop import produce_tick
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.states import ChapterStep, chapter_step_order

PROSE = "林晚把铜钥匙放进抽屉。"
_sentence = 1
while len(PROSE) < 2000:
    PROSE += (
        f"林晚把第{_sentence}枚铜钥匙放进第{_sentence}只抽屉，"
        f"窗外第{_sentence}阵雨还在下，走廊里没有第{_sentence}个人经过。"
    )
    _sentence += 1
PRIOR = "上一章写的是码头的雾和一艘没有名字的船。"

LONG = PROSE
_tail = 1
while len(LONG) < 3200:
    LONG += (
        f"第{_tail}遍钟声敲响时，林晚数到第{_tail + 2}下才把抽屉合上，"
        f"外面第{_tail + 5}阵雨还没停。"
    )
    _tail += 1


def _run(*, phase: str = "WRITE", content: str = "", prior: str = PRIOR, repairs: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id="run-1",
        chapter_no=2,
        input_version=1,
        title="第二章",
        content=content,
        word_count=len(content),
        review={},
        generation_context={
            "architecture_version": "director_chapter",
            "chapter_no": 2,
            "story_complete": False,
            "target_node": {"id": "n1", "title": "夺钥", "promise": "夺回铜钥匙"},
            "story_bible_block": "雨城",
            "prose_style": {},
            "reader_contract": {},
            "dramatic_engine": {},
            "canon": [{"statement": "林晚住在雨城"}],
            "recent_chapters": [{"content": prior}],
            "memory": [
                {"kind": "promise", "state": "OPEN", "subject": "林晚", "content": "还回铜钥匙"},
                {"kind": "director_note", "state": "OPEN", "content": "导演备忘"},
            ],
            "production": {
                "protocol": "chapter",
                "phase": phase,
                "repairs": repairs,
                "call_count": 0,
                "cast": {"林晚": {"identity": {"role": "主角"}}},
                "takes": [],
                "accepted": [],
                "check_fails": [],
                "working_state": {},
            },
        },
    )


def test_step_order_has_no_direct():
    steps = chapter_step_order({"architecture_version": "director_chapter"})
    assert steps == (
        ChapterStep.ASSEMBLE,
        ChapterStep.PRODUCE,
        ChapterStep.REVIEW,
        ChapterStep.CANON,
    )
    assert ChapterStep.DIRECT not in steps


def test_protocol_is_chapter():
    run = SimpleNamespace(
        generation_context={
            "architecture_version": "director_chapter",
            "production": {"call_key_version": 2},
        }
    )
    assert production_protocol(run) == "chapter"
    assert production_protocol({"protocol": "chapter"}) == "chapter"
    assert production_protocol({"architecture_version": "director_chapter"}) == "chapter"
    assert "director_chapter" in executor_app.KNOWN_EXECUTORS
    assert executor_app.executor_version("director_chapter") == "director_chapter@1"


def test_write_payload_is_five_blocks():
    run = _run()
    payload = build_write_payload(run.generation_context, {"林晚": {}})
    assert payload["previous_chapter"] == PRIOR
    assert payload["target_node"]["promise"] == "夺回铜钥匙"
    assert payload["open_promises"] == [{"subject": "林晚", "content": "还回铜钥匙"}]
    assert payload["canon"][0]["statement"] == "林晚住在雨城"
    forbidden = {"candidates", "scene_plan", "continuity_ledger", "director_memory"}
    assert forbidden.isdisjoint(payload)


def test_no_path_back_to_brief():
    banned = {
        "BRIEF",
        "WRITE_SCRIPTS",
        "SELECT",
        "COMPILE_SCENES",
        "WRITE_SCENE",
        "AUDIT_SCENE",
        "ACCEPT_CHAPTER",
    }
    assert banned.isdisjoint(CHAPTER_PHASES)


def test_one_chapter_one_node():
    nodes = [SimpleNamespace(node_id=f"n{i}") for i in range(1, 5)]
    target, exhausted = chapter_node_for_run(nodes, chapter_no=4)
    assert target.node_id == "n4"
    assert exhausted is False
    later, done = chapter_node_for_run(nodes, chapter_no=5)
    assert later is None
    assert done is True


def _pad(seed: str, n: int = 40) -> str:
    return "".join(f"{seed}第{i}步，林晚还站在门口。" for i in range(1, n))


def test_drop_copied_span_keeps_the_new_part():
    previous = _pad("旧章现场", 120)
    fresh = _pad("新的选择", 120)
    copied = previous[:120]
    kept = drop_copied_span(fresh + copied, previous)
    assert copied not in kept
    assert kept.startswith("新的选择")
    assert len(kept) >= 2000
    text = "甲。" * 1100 + "乙" * 2000
    fitted = fit_chapter_length(text)
    assert 2000 <= len(fitted) <= 2500
    assert fitted.endswith("。")
    assert "乙" not in fitted


def test_short_chapter_fails():
    text = _pad("夜色很浓", 20)
    assert len(text) < 2000
    fails = check_chapter(text, ctx={"chapter_no": 1}, cast={"林晚": {}}, title="一")
    assert any("不足2000字" in item for item in fails)


def test_near_cast_name_is_a_repair_hint_not_a_hard_fail():
    text = _pad("夜色很浓", 80) + "林大勇推门进来。"
    assert stray_names(text, ["赵大勇"]) == ["林大勇"]
    assert stray_names(text, ["赵大勇", "林大勇"]) == []
    fails = check_chapter(text, ctx={"chapter_no": 2}, cast={"赵大勇": {}}, title="二")
    assert not any("林大勇" in item for item in fails)
    assert any("林大勇" in item for item in name_warnings(text, {"赵大勇": {}}))
    title_talk = (
        _pad("夜色很浓", 80)
        + "灵田的执事把名册放下。一名执事走过来。交由执事登记。内门执事没说话。"
        + "他开口石头就滚了。脚底石头是湿的。"
    )
    assert stray_names(title_talk, ["赵执事", "陈石头"]) == []
    assert stray_names(_pad("夜色很浓", 80) + "李执事站在门口。", ["赵执事"]) == ["李执事"]
    assert stray_names("舅舅林大勇推门进来。", ["赵大勇"]) == ["林大勇"]
    address = _pad("夜色很浓", 80) + "林老师把作文本合上。李老师在门口。"
    assert stray_names(address, ["王老师", "林夏"]) == ["李老师"]


def test_repeated_result_breaks_rhythm():
    earlier = [{"pressure": "保住同事", "choice": "填上名字", "result": "同事当场变成包裹上的人", "shows_rule": True}]
    rhythm = {"stake": "保住同事", "payoff_limit": 1, "arc_end": "规则只兑现一次"}
    again = {
        "pressure": "保住下一个同事",
        "choice": "再填一个名字",
        "result": "同事当场变成包裹上的人",
        "shows_rule": False,
    }
    assert "同一场" in rhythm_mismatch(again, rhythm, earlier)
    fresh = {
        "pressure": "包裹还在他手里",
        "choice": "他停下不再派送",
        "result": "这班货因此没出站",
        "shows_rule": True,
    }
    assert "已经发生够了" in rhythm_mismatch(fresh, rhythm, earlier)


STAKE = "陈石头必须在三日后内门比试中赢一场否则被除名"


def test_pressure_cannot_copy_stake_or_earlier_pressure():
    rhythm = {"stake": STAKE, "payoff_limit": 2}
    earlier = [{"pressure": "初筛只剩最后一个名额", "choice": "只用外门拳法", "result": "以最后一名过了初筛"}]
    copied_stake = {"pressure": STAKE, "choice": "夜里去暗沟", "result": "腕骨裂缝加深"}
    assert "照抄了这一卷的目标" in rhythm_mismatch(copied_stake, rhythm, earlier)
    same_pressure = {
        "pressure": "初筛只剩最后一个名额，他还没上台",
        "choice": "夜里去暗沟",
        "result": "腕骨裂缝加深",
    }
    assert rhythm_mismatch(same_pressure, rhythm, earlier) == ""
    earlier_long = [{"pressure": "赵执事只给他待定，三日后在内门比试中输一场就当场除名", "choice": "a", "result": "b"}]
    again = {"pressure": "赵执事只给他待定，三日后在内门比试中输一场就当场除名", "choice": "c", "result": "d"}
    assert "压力和第1章相同" in rhythm_mismatch(again, rhythm, earlier_long, chapter_no=2)
    assert rhythm_mismatch(copied_stake, rhythm, []) == ""
    carried = [{"pressure": "赵海已独自进入货舱十五分钟未出，她被关在医疗舱", "choice": "a", "result": "b"}]
    still_there = {"pressure": "她被锁在急救室，赵海已独自进入货舱十五分钟", "choice": "c", "result": "d"}
    assert rhythm_mismatch(still_there, rhythm, carried) == ""


def test_long_results_sharing_an_unchanged_status_are_not_one_scene():
    tail = "她获得第三天值班资格，距离凌晨还有不到二十小时，日志消失页的指令仍然有效"
    earlier = [
        {
            "pressure": "p1",
            "choice": "c1",
            "result": "她复紧了控制柜法兰，柜门传感器被干扰源压在高电平，系统再也看不到柜门开闭；"
            + tail
            + "，但干扰源是一处未授权改装，一旦被发现就是更重的指控。",
        }
    ]
    rhythm = {"stake": "活下去", "payoff_limit": 1}
    moved = {
        "pressure": "p2",
        "choice": "c2",
        "result": "她给主气闸泄压阀加了铅封，指令来了也打不开那道门，只能剪断铅封后手动操作；"
        + tail
        + "，可铅封和巡检记录都登记在案，门一开她就是唯一责任人。",
    }
    assert rhythm_mismatch(moved, rhythm, earlier, chapter_no=2) == ""
    same = {"pressure": "p2", "choice": "c2", "result": earlier[0]["result"]}
    assert "同一场" in rhythm_mismatch(same, rhythm, earlier, chapter_no=2)


def test_repeated_choice_names_the_earlier_chapter():
    rhythm = {"stake": "找到苏念", "payoff_limit": 1}
    earlier = [
        {"pressure": "p1", "choice": "c1", "result": "r1"},
        {"pressure": "p2", "choice": "抄下苏念的号码，用办公室座机拨过去，在对方接听前挂断，确认号码有效", "result": "r2"},
    ]
    again = {"pressure": "p3", "choice": "用座机拨打这个号，在对方接听前挂断，确认号码是否仍能接通", "result": "r3"}
    reason = rhythm_mismatch(again, rhythm, earlier, chapter_no=3)
    assert "第2章已经这样做过" in reason
    assert "在对方接听前挂断" in reason


def test_stage_position_and_pacing():
    stages = ["他进了内门却被盯上", "他在藏经阁找到骨简的来历", "他必须在禁地和宗门之间选一边"]
    earlier = [
        {"pressure": "p1", "choice": "c1", "result": "r1", "stage_index": 0, "reaches_stage": False},
        {"pressure": "p2", "choice": "c2", "result": "r2", "stage_index": 0, "reaches_stage": True},
        {"pressure": "p3", "choice": "c3", "result": "r3", "stage_index": 1, "reaches_stage": False},
    ]
    assert stage_position(earlier, stages) == (1, 1, False)
    assert stage_position([], stages) == (0, 0, False)
    rhythm = {"stake": "活下去", "payoff_limit": 3, "stages": stages, "volume_chapters": 30}
    assert min_chapters_per_stage(rhythm) == 5
    jump = {"pressure": "新处境", "choice": "新选择", "result": "他必须在禁地和宗门之间选一边了"}
    assert "越过了眼下这一段" in rhythm_mismatch(jump, rhythm, earlier)
    rushed = {"pressure": "新处境", "choice": "新选择", "result": "新结果", "reaches_stage": True}
    assert "才写了 2 章" in rhythm_mismatch(rushed, rhythm, earlier)
    steady = {"pressure": "新处境", "choice": "新选择", "result": "新结果", "reaches_stage": False}
    assert rhythm_mismatch(steady, rhythm, earlier) == ""
    assert max_chapters_per_stage(rhythm) == 10
    stuck = [
        {"pressure": f"p{i}", "choice": f"c{i}", "result": f"r{i}", "stage_index": 0, "reaches_stage": False}
        for i in range(10)
    ]
    assert stage_position(stuck, stages, cap=10) == (1, 0, False)
    assert stage_position(stuck, stages) == (0, 10, False)
    opening_rhythm = {
        "stake": "活下去",
        "payoff_limit": 1,
        "stages": ["林夏在家长会上确认陈玉兰不知情", "林夏找到苏念", "林夏公开真相"],
    }
    skipped = {"pressure": "林夏在家长会上确认陈玉兰毫不知情", "choice": "c", "result": "r"}
    assert "第一段还没发生" in rhythm_mismatch(skipped, opening_rhythm, [])


def test_rhythm_problems_reject_one_scene_volume():
    flat = BookRhythm(
        opening="林夏在家长会上公开真相并与周小满和解",
        stake="保住教师尊严",
        stages=["林夏在家长会上公开真相并与周小满和解"],
        arc_end="林夏在家长会上公开真相并与周小满和解",
    )
    problems = rhythm_problems(flat)
    assert any("3 到 5" in item for item in problems)
    assert any("开局就做完" in item for item in problems)
    arc = BookRhythm(
        opening="家长会前夜，林夏发现满分作文开头是她十年前的评语",
        stake="林夏要弄清作文本为什么会回来",
        stages=["她在家长会上被迫当众回答", "学校开始调查她当年的匿名汇款", "周念的中考成绩被暂扣"],
        arc_end="周念的中考成绩被暂扣",
    )
    assert rhythm_problems(arc) == []


@pytest.mark.asyncio
async def test_second_repeated_plan_stops_before_writing(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {"stake": "保住同事", "payoff_limit": 1}
    run.generation_context["chapter_plans"] = [
        {"chapter_no": 1, "pressure": "旧压力", "choice": "旧选择", "result": "包裹留在问题件区不动"}
    ]
    seen: list[dict] = []

    async def _fake_call(*args, **kwargs):
        seen.append({"command": args[9], "temperature": kwargs.get("temperature"), "payload": args[7]})
        return ChapterPlan(pressure="新压力", choice="新选择", result="包裹留在问题件区不动")

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "PLAN"
    assert not production.get("chapter_plan")
    for _ in range(3):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
        ) is False
        production = run.generation_context["production"]
    assert production["plan_calls"] == 4
    assert production["phase"] == "WRITE"
    assert production["chapter_plan"]["choice"] == "新选择"
    assert any("同一场" in item for item in production["plan_warnings"])
    assert seen[0]["temperature"] == 0
    assert seen[1]["temperature"] == 0.7
    assert seen[1]["payload"]["rejected_plan"]["result"] == "包裹留在问题件区不动"
    assert seen[1]["payload"]["previous_result"] == "包裹留在问题件区不动"
    assert len({item["command"] for item in seen}) == 4


@pytest.mark.asyncio
async def test_same_method_replans_twice_then_writes(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {"stake": "找到苏念", "payoff_limit": 1}
    run.generation_context["chapter_plans"] = [
        {"pressure": "旧压力", "choice": "用办公室座机拨打后挂断", "result": "号码是通的"}
    ]
    seen: list[str] = []

    async def _fake_call(*args, **kwargs):
        schema = args[5]
        seen.append(schema.__name__)
        if schema is MethodVerdict:
            return MethodVerdict(same_method=True, matched="用座机拨号后挂断")
        return ChapterPlan(
            used_methods=["用座机拨号后挂断"],
            pressure="对方还没接",
            choice="再用座机拨通后立刻挂断",
            result="听筒里只有忙音",
        )

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    for _ in range(4):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
        ) is False
        production = run.generation_context["production"]
    assert production["phase"] == "PLAN"
    assert production["method_rounds"] == 2
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "WRITE"
    assert seen == ["ChapterPlan", "MethodVerdict", "ChapterPlan", "MethodVerdict", "ChapterPlan"]
    assert production["plan_reject"] == ""


def _room_ctx(chapter_no: int = 6) -> dict:
    """第 1 章在走廊；第 2–3 章没记地点、章纲提到约束室；第 4–5 章记了地点。"""
    return {
        "chapter_no": chapter_no,
        "chapter_plans": [
            {"chapter_no": 1, "pressure": "p1", "choice": "c1", "result": "r1", "place": "走廊"},
            {"chapter_no": 2, "pressure": "被押进约束室", "choice": "c2", "result": "r2"},
            {"chapter_no": 3, "pressure": "p3", "choice": "c3", "result": "r3"},
            {"chapter_no": 4, "pressure": "p4", "choice": "c4", "result": "r4", "place": "约束室"},
            {"chapter_no": 5, "pressure": "p5", "choice": "c5", "result": "r5", "place": "医疗舱约束室"},
        ],
        # 第 3 章章纲没提地点，正文里约束室出现两次也算还在那里
        "recent_chapters": [
            {"chapter_no": 5, "content": "……"},
            {"chapter_no": 4, "content": "……"},
            {"chapter_no": 3, "content": "约束室的灯灭了。他在约束室里数心跳。"},
        ],
    }


def test_place_streak_counts_back_through_old_and_new_plans():
    from regent.novel.application.directing_chapter import place_streak

    ctx = _room_ctx()
    assert place_streak(ctx, "约束室") == 4
    assert place_streak(ctx, "走廊") == 0
    assert place_streak(ctx, "") == 0
    # 换个叫法也算同一处；只共一个字不算
    # 正文只提一次不算
    ctx["recent_chapters"][2]["content"] = "约束室的灯灭了。"
    assert place_streak(ctx, "约束室") == 2
    # 换个叫法也算同一处；只共一个字不算
    ctx["chapter_plans"][4]["place"] = "物理约束观察室"
    assert place_streak(ctx, "约束室") == 2
    ctx["chapter_plans"][4]["place"] = "指挥舱"
    assert place_streak(ctx, "医疗舱") == 0


@pytest.mark.asyncio
async def test_plan_that_stays_in_the_same_place_too_long_is_replanned(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context.update(_room_ctx())
    run.generation_context["book_rhythm"] = {"stake": "逃出去", "payoff_limit": 1}
    seen: list[dict] = []

    async def _fake_call(*args, **kwargs):
        schema = args[5]
        if schema is MethodVerdict:
            return MethodVerdict(same_method=False, matched="")
        seen.append(args[7])
        if len(seen) == 1:
            return ChapterPlan(pressure="又被绑住", choice="咬破嘴唇", result="血滴在扶手上", place="约束室")
        return ChapterPlan(pressure="门开了", choice="趁换班冲出去", result="躲进通风管", place="通风管")

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    for _ in range(2):
        await produce_chapter_tick(None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production)
        production = run.generation_context["production"]
    assert seen[0]["place_streak"] == {"place": "医疗舱约束室", "chapters": 2, "limit": 4}
    assert "连续 4 章在「约束室」" in seen[1]["reject_reason"]
    assert production["chapter_plan"]["place"] == "通风管"


@pytest.mark.asyncio
async def test_new_method_goes_on_to_write(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {"stake": "找到苏念", "payoff_limit": 1}
    run.generation_context["chapter_plans"] = [
        {"pressure": "旧压力", "choice": "用办公室座机拨打后挂断", "result": "号码是通的"}
    ]

    async def _fake_call(*args, **kwargs):
        if args[5] is MethodVerdict:
            return MethodVerdict(same_method=False, matched="")
        return ChapterPlan(
            used_methods=["用座机拨号后挂断"],
            pressure="对方约了地方",
            choice="请事假去省城见面",
            result="假条批下来了",
        )

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "METHOD"
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "WRITE"
    assert int(production.get("method_rounds") or 0) == 0
    assert run.generation_context["target_node"]["choice"] == "请事假去省城见面"


@pytest.mark.asyncio
async def test_result_that_skips_ahead_is_replanned(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {
        "stake": "弄清评语为什么出现在别人的作文里",
        "payoff_limit": 1,
        "stages": ["林夏查到作文本经过谁的手", "林夏当面问苏慧", "林夏读到周小雨的信"],
        "arc_end": "林夏明白评语是周小雨生前的安排",
        "volume_chapters": 30,
    }
    seen: list[dict] = []

    async def _fake_call(*args, **kwargs):
        seen.append({"schema": args[5].__name__, "payload": args[7]})
        if args[5] is MethodVerdict:
            return MethodVerdict(same_method=False, skips_ahead=True, skipped="周小雨生前的安排被说破")
        return ChapterPlan(pressure="家长会前夜", choice="翻陈默的课桌", result="找到小雨的信，知道是她安排的")

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    for _ in range(2):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
        ) is False
        production = run.generation_context["production"]
    assert [item["schema"] for item in seen] == ["ChapterPlan", "MethodVerdict"]
    assert seen[1]["payload"]["used_methods"] == []
    assert seen[1]["payload"]["later_stages"] == ["林夏当面问苏慧", "林夏读到周小雨的信"]
    assert seen[1]["payload"]["arc_end"] == "林夏明白评语是周小雨生前的安排"
    assert production["phase"] == "PLAN"
    assert "提前写出了后面的事：周小雨生前的安排被说破" in production["plan_reject"]
    assert production["plan_rejected"]["result"] == "找到小雨的信，知道是她安排的"


def _three_chapters() -> list[dict]:
    return [
        {"chapter_no": i, "pressure": f"压力{i}", "choice": f"把证物换个地方藏{i}", "result": f"证物换了位置{i}"}
        for i in (1, 2, 3)
    ]


@pytest.mark.asyncio
async def test_arc_review_runs_every_three_chapters_and_steers_the_plan(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {"stake": "活下去", "payoff_limit": 1}
    run.generation_context["reader_contract"] = {"must_deliver": ["每一段都靠禁术翻盘一次"]}
    run.generation_context["chapter_plans"] = _three_chapters()
    seen: list[dict] = []

    async def _fake_call(*args, **kwargs):
        seen.append({"schema": args[5].__name__, "payload": args[7], "command": args[9]})
        if args[5] is ArcReview:
            return ArcReview(
                keeps_reading=False,
                problems=[{"chapters": [1, 2, 3], "problem": "三章都在挪证物", "fix": "让对手先动手"}],
                directives=["这一章由对手先出手，主角被迫应对"],
                payoff_due=True,
            )
        if args[5] is MethodVerdict:
            return MethodVerdict(same_method=False)
        return ChapterPlan(pressure="对手堵上门", choice="当场用禁术", result="赢了但折寿", shows_rule=True)

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    for _ in range(3):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
        ) is False
        production = run.generation_context["production"]
    assert [item["schema"] for item in seen] == ["ArcReview", "ChapterPlan", "MethodVerdict"]
    review_in = seen[0]["payload"]
    assert review_in["reader_contract"]["must_deliver"] == ["每一段都靠禁术翻盘一次"]
    assert [item["chapter_no"] for item in review_in["recent_chapters"]] == [1, 2, 3]
    plan_in = seen[1]["payload"]
    assert plan_in["editor_notes"]["directives"] == ["这一章由对手先出手，主角被迫应对"]
    assert plan_in["payoff_due"] is True
    assert seen[2]["payload"]["editor_notes"]["problems"] == ["三章都在挪证物"]
    assert production["phase"] == "WRITE"
    node = run.generation_context["target_node"]
    assert node["arc_review"]["directives"] == ["这一章由对手先出手，主角被迫应对"]
    assert node["shows_rule"] is True


def test_arc_review_can_lift_payoff_cap_and_push_the_stage():
    stages = ["他进了内门却被盯上", "他找到骨简的来历"]
    rhythm = {"stake": "活下去", "payoff_limit": 1, "stages": stages, "volume_chapters": 30}
    earlier = [
        {"pressure": "p1", "choice": "c1", "result": "r1", "shows_rule": True, "stage_index": 0},
        {"pressure": "p2", "choice": "c2", "result": "r2", "stage_index": 0},
    ]
    again = {"pressure": "新处境", "choice": "新选择", "result": "新结果", "shows_rule": True}
    assert "已经发生够了" in rhythm_mismatch(again, rhythm, earlier)
    assert rhythm_mismatch(again, rhythm, earlier, review={"payoff_due": True}) == ""
    quiet = {"pressure": "新处境", "choice": "新选择", "result": "新结果"}
    assert "好几章没有兑现" in rhythm_mismatch(quiet, rhythm, earlier, review={"payoff_due": True})
    assert rhythm_mismatch(quiet, rhythm, earlier, review={"advance_stage": True}) == ""
    landed = dict(quiet, reaches_stage=True)
    assert "才写了" in rhythm_mismatch(landed, rhythm, earlier, review={"advance_stage": True})
    ripe = earlier + [
        {"pressure": f"p{i}", "choice": f"c{i}", "result": f"r{i}", "stage_index": 0} for i in range(3, 8)
    ]
    assert "拖住了" in rhythm_mismatch(quiet, rhythm, ripe, review={"advance_stage": True})
    assert rhythm_mismatch(landed, rhythm, ripe, review={"advance_stage": True}) == ""


def test_review_cannot_push_a_stage_below_its_floor():
    from regent.novel.application.directing_chapter import settle_stage_claim, stage_pushed

    stages = ["一", "二", "三"]
    rhythm = {"stake": "活下去", "payoff_limit": 1, "stages": stages, "volume_chapters": 30}
    fresh = [{"pressure": "p0", "choice": "c0", "result": "r0", "stage_index": 0, "reaches_stage": True}]
    assert stage_pushed(rhythm, fresh, {"advance_stage": True}) is False
    landed = {"pressure": "新处境", "choice": "新选择", "result": "新结果", "reaches_stage": True}
    assert "才写了 1 章" in rhythm_mismatch(landed, rhythm, fresh, review={"advance_stage": True})
    node = dict(landed)
    settle_stage_claim(node, rhythm, fresh, {"advance_stage": True})
    assert node["reaches_stage"] is False
    ripe = fresh + [
        {"pressure": f"p{i}", "choice": f"c{i}", "result": f"r{i}", "stage_index": 1} for i in range(1, 5)
    ]
    assert stage_pushed(rhythm, ripe, {"advance_stage": True}) is True
    unfinished = {"advance_stage": True, "stage_remaining": ["对手当面拿出证据"]}
    assert stage_pushed(rhythm, ripe, unfinished) is False
    assert "最后一章" not in (rhythm_mismatch(landed, rhythm, ripe, review=unfinished) or "")
    node = dict(landed)
    settle_stage_claim(node, rhythm, ripe, None)
    assert node["reaches_stage"] is True


def test_last_stage_landing_ends_the_volume():
    from regent.novel.application.works_volumes import stage_rhythm_finished

    rhythm = {"stages": ["一", "二"], "volume_chapters": 30}
    ledger = [
        {"chapter_no": 1, "pressure": "p", "choice": "c", "result": "r", "stage_index": 0, "reaches_stage": True},
        {"chapter_no": 2, "pressure": "p", "choice": "c", "result": "r", "stage_index": 1},
        {"chapter_no": 3, "pressure": "旧", "choice": "旧", "result": "旧", "stage_index": 1},
    ]
    last = {"id": "ch3", "pressure": "p", "choice": "c", "result": "r", "stage_index": 1, "reaches_stage": True}
    context = {"book_rhythm": rhythm, "chapter_plans": ledger, "target_node": last}
    assert stage_rhythm_finished(context, 3) is True
    context["target_node"] = dict(last, reaches_stage=False)
    assert stage_rhythm_finished(context, 3) is False
    assert stage_rhythm_finished({"book_rhythm": {}, "target_node": last}, 3) is False
    full = dict(context, book_rhythm=dict(rhythm, volume_start=1, volume_chapters=4))
    full["target_node"] = dict(last, reaches_stage=False)
    assert stage_rhythm_finished(full, 3) is True

    from regent.novel.application.works_continuation import volume_finished_by

    done = SimpleNamespace(chapter_no=3, generation_context=dict(context, target_node=last))
    assert volume_finished_by(done) is True
    open_run = SimpleNamespace(chapter_no=3, generation_context=context)
    assert volume_finished_by(open_run) is False
    expanded = SimpleNamespace(
        chapter_no=3,
        generation_context=dict(
            context, target_node=last, volume_expansion_resolved={"volume_no": 2}
        ),
    )
    assert volume_finished_by(expanded) is False


def test_new_volume_does_not_inherit_the_last_volume_stages():
    from regent.novel.application.directing_chapter import previous_plans

    old = {"choice": "c", "result": "r", "stage_index": 4, "reaches_stage": True}
    ctx = {
        "book_rhythm": {"stages": ["一", "二", "三"], "volume_start": 31, "volume_chapters": 36},
        "chapter_plans": [],
        "recent_chapters": [{"chapter_no": 30, "target_node": old}, {"chapter_no": 29, "target_node": old}],
    }
    assert previous_plans(ctx) == []
    ctx["book_rhythm"]["volume_start"] = 1
    assert len(previous_plans(ctx)) == 2


@pytest.mark.asyncio
async def test_expanded_staged_volume_does_not_end_the_story():
    from regent.novel.application.works_volumes import _last_node_completed

    rhythm = {"stages": ["一"], "volume_start": 1, "volume_chapters": 3}
    last = {"id": "ch3", "pressure": "p", "choice": "c", "result": "r", "stage_index": 0}
    context = {"book_rhythm": rhythm, "target_node": last}
    run = SimpleNamespace(chapter_no=3, generation_context=context)
    assert await _last_node_completed(None, None, run) is True
    run.generation_context = dict(context, volume_expansion_resolved={"volume_no": 2})
    assert await _last_node_completed(None, None, run) is False


@pytest.mark.asyncio
async def test_plan_that_ignores_editor_notes_is_replanned(monkeypatch):
    run = _run(phase="PLAN")
    run.generation_context["book_rhythm"] = {"stake": "活下去", "payoff_limit": 1}
    plans = _three_chapters()
    plans[-1]["arc_review"] = {"directives": ["让对手先出手"], "problems": ["三章都在挪证物"]}
    plans.append({"chapter_no": 4, "pressure": "p4", "choice": "c4", "result": "r4"})
    run.generation_context["chapter_plans"] = plans

    async def _fake_call(*args, **kwargs):
        if args[5] is MethodVerdict:
            return MethodVerdict(same_method=False, ignores_notes=True, ignored="让对手先出手")
        return ChapterPlan(pressure="风平浪静", choice="又把证物换个地方", result="证物在新地方")

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    production = run.generation_context["production"]
    for _ in range(2):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
        ) is False
        production = run.generation_context["production"]
    assert production["phase"] == "PLAN"
    assert "没有做到编辑意见：让对手先出手" in production["plan_reject"]
    assert "arc_review" not in production


def test_chapter_call_cap_covers_the_short_path_only():
    from regent.novel.application.directing_budget import (
        CHAPTER_MAX_CALLS,
        H2_CALLS_PER_ROUND,
        H2_MAX_ROUNDS,
        MAX_CALLS,
        effective_call_cap,
    )

    assert CHAPTER_MAX_CALLS == 16
    assert MAX_CALLS == 120
    assert effective_call_cap({"protocol": "chapter", "call_count": 0}) == 16
    assert effective_call_cap({"protocol": "script_scene", "call_count": 0}) == 120
    assert (
        effective_call_cap(
            {"protocol": "chapter", "executor": "director_chapter_h2", "call_count": 0}
        )
        == CHAPTER_MAX_CALLS + H2_MAX_ROUNDS * H2_CALLS_PER_ROUND
    )


@pytest.mark.asyncio
async def test_plan_replaces_prewritten_chapter_plot(monkeypatch):
    run = _run()
    run.generation_context["book_rhythm"] = {
        "stake": "保住同事",
        "payoff_limit": 1,
        "arc_end": "规则只兑现一次",
    }
    run.generation_context["target_node"] = {
        "id": "n1",
        "title": "赵大勇变脸",
        "promise": "发生：身体扭曲",
    }

    async def _fake_call(*args, **kwargs):
        return ChapterPlan(
            pressure="考核逼他清掉问题件",
            choice="陈默拒绝随便填名字",
            result="包裹留在问题件区",
            shows_rule=False,
        )

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is False
    node = run.generation_context["target_node"]
    assert node["choice"] == "陈默拒绝随便填名字"
    assert "变脸" not in node["promise"]
    assert run.generation_context["production"]["phase"] == "WRITE"


def test_overlong_chapter_fails():
    text = _pad("夜色很浓", 200)
    assert len(text) > MAX_CHAPTER_CHARS == 3000
    fails = check_chapter(text, ctx={"chapter_no": 1}, cast={"林晚": {}}, title="一")
    assert any("超过3000字" in item for item in fails)
    # 提示要的是 2001–2499 字；2500–3000 字照样验收，不送去删段
    assert not check_chapter(_pad("夜色很浓", 165), ctx={"chapter_no": 1}, cast={"林晚": {}}, title="一")
    # 长度是机械约束：探针比较两稿好坏时不该按它判失败
    assert not check_chapter(
        text, ctx={"chapter_no": 1}, cast={"林晚": {}}, title="一", ignore_max_length=True
    )


def _grouped(text: str, size: int = 3) -> list[str]:
    sentences = [s + "。" for s in text.split("。") if s]
    return ["".join(sentences[i : i + size]) for i in range(0, len(sentences), size)]


_KEEP = _grouped(PROSE)
_FILLER = _grouped(LONG[len(PROSE) :])
ENDING = "陈默拒绝随便填名字，包裹留在问题件区。"
# 中间夹着铺陈段的超长稿：删掉铺陈就落回上下限之间，章末结果原样留着
PADDED = "\n\n".join(_KEEP[:4] + _FILLER + _KEEP[4:] + [ENDING])
FITTED = "\n\n".join(_KEEP + [ENDING])


def _filler_units(draft: str) -> list[int]:
    return [no for no, (_, piece) in enumerate(fit_units(draft)) if piece in _FILLER]


async def _fit_run(
    monkeypatch, *, drop: list[int] | None = None, draft: str = PADDED
) -> tuple[SimpleNamespace, list]:
    run = _run()
    run.generation_context["production"]["chapter_plan"] = {
        "id": "n1",
        "pressure": "考核逼他表态",
        "choice": "陈默拒绝随便填名字",
        "result": "包裹留在问题件区",
    }
    calls: list[dict] = []

    async def _fake_call(*args, **kwargs):
        purpose = args[8]
        calls.append(
            {"purpose": purpose, "command": args[9], "payload": args[7], "system": args[6]}
        )
        if purpose == "chapter_fit":
            return ChapterFitCut(drop=_filler_units(draft) if drop is None else drop)
        return ChapterProse(content=draft)

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    return run, calls


def test_fitted_fixture_lands_inside_the_window():
    assert len(PADDED) > MAX_CHAPTER_CHARS
    assert MIN_CHAPTER_CHARS <= len(FITTED) < FIT_TARGET_CHARS


@pytest.mark.asyncio
async def test_overlong_draft_is_cut_by_paragraph_not_chopped(monkeypatch):
    """超长稿由模型挑段、代码删段再落稿：章末结果正是砍尾会砍掉的那一段。"""
    run, calls = await _fit_run(monkeypatch)
    work = SimpleNamespace(id="w")

    async def _tick() -> bool:
        return await produce_chapter_tick(
            None, provider=None, work=work, run=run,
            production=run.generation_context["production"],
        )

    assert await _tick() is False
    production = run.generation_context["production"]
    # 超长时不落稿、不进 CHECK，先挂着这一稿去压缩
    assert production["phase"] == "FIT"
    assert run.content == ""
    tag = production["fit"]["tag"]
    assert tag.startswith("write:w0:")
    assert production["fit"]["draft"] == PADDED

    assert await _tick() is False
    production = run.generation_context["production"]
    fit = calls[1]
    assert fit["purpose"] == "chapter_fit"
    assert fit["command"] == f"v1:fit:{tag}"
    assert fit["system"] == CHAPTER_FIT_SYSTEM
    payload = fit["payload"]
    assert payload["must_keep"] == {
        "choice": "陈默拒绝随便填名字",
        "result": "包裹留在问题件区",
    }
    assert [unit["text"] for unit in payload["units"]] == _KEEP[:4] + _FILLER + _KEEP[4:] + [ENDING]
    assert all(unit["chars"] == len(unit["text"]) for unit in payload["units"])
    assert payload["need_cut"] == len(PADDED) - FIT_TARGET_CHARS
    # 章末那段在保护区里，模型想删也删不掉
    assert len(payload["units"]) - 1 in payload["protected"]
    assert production["phase"] == "CHECK"
    assert "fit" not in production
    # 删到目标字数就停：铺陈段只删掉够数的那几段，正文段与章末一段不少
    content = run.content
    assert MIN_CHAPTER_CHARS <= len(content) <= FIT_TARGET_CHARS
    assert all(piece in content for piece in _KEEP) and content.endswith(ENDING)
    dropped = [piece for piece in _FILLER if piece not in content]
    assert 0 < len(dropped) < len(_FILLER)
    assert production["fit_results"] == [
        {
            "tag": tag,
            "chars_before": len(PADDED),
            "chars_dropped": len(PADDED) - len(content),
            "units_dropped": len(dropped),
            "chars_after": len(content),
            "truncated": False,
        }
    ]
    assert await _tick() is True
    assert run.generation_context["production"]["phase"] == "DONE"
    assert [c["purpose"] for c in calls] == ["chapter_write", "chapter_fit"]


def test_fit_cut_keeps_the_ending_and_the_floor():
    """删段只认模型挑的编号，但章末保护区、越界编号和删破下限的那一段一律不删。"""
    units = fit_units(PADDED)
    protected = fit_protected(units)
    assert protected[-1] == len(units) - 1
    assert sum(len(units[no][1]) for no in protected) >= 400
    # 章末片段排在最前、越界编号夹在中间：都跳过，删到目标字数就停，不一路删到下限
    everything = protected[::-1] + [-1, len(units) + 5] + list(range(len(units)))
    body, removed = apply_fit_cut(PADDED, everything)
    assert not set(removed) & set(protected)
    assert body.endswith(ENDING)
    assert MIN_CHAPTER_CHARS <= len(body) <= FIT_TARGET_CHARS
    assert len(removed) < len(units) - len(protected)
    # 模型什么都不挑，稿子原样回来
    assert apply_fit_cut(PADDED, []) == (PADDED, [])


def test_fit_cut_skips_drops_that_break_a_seam():
    """模型挑的段会让专名凭空出现、或问句没了答句还在：这种段不删，接着删别的。"""
    intro = "周砚推门进来，" + "雨" * 140
    filler = ["风" * 150 for _ in range(18)]
    question, answer = "“包裹是谁寄的？”", "“不知道。”他摇头。"
    later = "周砚把包裹放回架上。" + "灯" * 140
    ending = "结" * 420
    paras = [intro, *filler, question, answer, later, ending]
    draft = "\n\n".join(paras)
    assert len(draft) > MAX_CHAPTER_CHARS
    q_no = paras.index(question)
    body, removed = apply_fit_cut(draft, [0, q_no, *range(1, 19)], terms=["周砚"])
    assert 0 not in removed and q_no not in removed
    assert body.startswith(intro) and question in body
    assert MIN_CHAPTER_CHARS <= len(body) <= FIT_TARGET_CHARS
    units = fit_units(draft)
    assert fit_seam_break(units, {q_no}, []).startswith("问句被删")
    assert fit_seam_break(units, {q_no, q_no + 1}, []) == ""
    assert fit_seam_break(units, {0}, ["周砚"]).startswith("专名首次出场被删")
    # 不给专名表时只查问答
    assert fit_seam_break(units, {0}, []) == ""


def test_long_paragraph_is_split_into_sentences_with_closing_quotes():
    para = "".join(f"他第{i}次说：“走。”她没动。" for i in range(40))
    assert len(para) > 300
    units = fit_units(para)
    assert units[0] == (0, "他第0次说：“走。”")
    assert units[1] == (0, "她没动。")
    assert apply_fit_cut(para, [])[0] == para


@pytest.mark.asyncio
async def test_truncated_tail_is_a_hard_fail_not_a_note(monkeypatch):
    """删段没删回来、章末被截掉：按硬门失败处理，改不回来就终止，不能入正史。

    第 4 章的交付稿就是这么丢了章纲定下的结果——截断只记了一笔，照常进正史。
    """
    from regent.novel.domain.errors import ProductionStopped

    run, calls = await _fit_run(monkeypatch, drop=[], draft=LONG)
    work = SimpleNamespace(id="w")
    stopped = ""
    for _ in range(20):
        try:
            await produce_chapter_tick(
                None, provider=None, work=work, run=run,
                production=run.generation_context["production"],
            )
        except ProductionStopped as exc:
            stopped = str(exc)
            break
    production = run.generation_context["production"]
    assert production["fit_results"][0]["truncated"] is True
    assert len(run.content) <= MAX_CHAPTER_CHARS
    # 截掉的尾巴留了原文，硬门失败带字数，改稿载荷拿得到这段尾巴
    cut = production["fit_truncated"]
    assert cut and len(cut) > 100
    assert any("章末被截掉" in item for item in production["check_fails"])
    repairs = [c for c in calls if c["purpose"] == "chapter_repair"]
    assert repairs and repairs[0]["payload"]["cut_tail"] == cut
    # 改稿拿到现成的字数，不靠模型自己数
    assert repairs[0]["payload"]["length"] == length_brief(repairs[0]["payload"]["previous_draft"])
    assert repairs[0]["payload"]["length"]["max_chars"] == MAX_CHAPTER_CHARS
    # 每一稿都删不回来：走完改稿与重写后终止，绝不 DONE
    assert "截掉" in stopped
    assert production["phase"] != "DONE"


@pytest.mark.asyncio
async def test_clean_compression_leaves_no_truncation_mark(monkeypatch):
    """删得回来就不留截断标记：标记只属于真砍过章末的那一稿。"""
    run, _ = await _fit_run(monkeypatch)
    work = SimpleNamespace(id="w")
    for _ in range(3):
        await produce_chapter_tick(
            None, provider=None, work=work, run=run,
            production=run.generation_context["production"],
        )
    production = run.generation_context["production"]
    assert production["phase"] == "DONE"
    assert "fit_truncated" not in production


def test_fit_command_id_follows_the_draft():
    """同一个 tag 换一份稿子必须是新的一次调用，否则撞 CallBroker 的幂等键。"""
    run = _run()
    production = run.generation_context["production"]
    accept_draft(run, production, ctx=run.generation_context, draft=LONG, tag="write:w0")
    first = production["fit"]["tag"]
    accept_draft(run, production, ctx=run.generation_context, draft=LONG + "又补了一句。", tag="write:w0")
    assert production["fit"]["tag"] != first
    assert production["fit"]["tag"].startswith("write:w0:")


def test_short_draft_skips_compression():
    run = _run()
    production = run.generation_context["production"]
    accept_draft(run, production, ctx=run.generation_context, draft=PROSE, tag="write:w0")
    assert production["phase"] == "CHECK"
    assert "fit" not in production
    assert run.content == PROSE


def test_replayed_cost_fails():
    once = "林晚用刚拿到的铜钥匙打开抽屉，钥匙随即断成两截。"
    text = _pad("铺子里只剩钟声", 20) + once + _pad("他在灯下坐着", 12) + once
    assert repeated_span(text)
    fails = check_chapter(text, ctx={"chapter_no": 2}, cast={"林晚": {}}, title="二")
    assert any("又出现了一遍" in item for item in fails)


def test_ungrounded_facts_dropped():
    kept = grounded_facts(
        "林晚把钥匙放在桌上。",
        [
            {"statement": "钥匙在桌上", "quote": "钥匙放在桌上"},
            {"statement": "他飞走了", "quote": "他展开翅膀飞走了"},
            {"statement": "没有摘录", "quote": ""},
        ],
    )
    assert kept == [{"statement": "钥匙在桌上", "quote": "钥匙放在桌上"}]


@pytest.mark.asyncio
async def test_missing_production_still_writes(monkeypatch):
    """ASSEMBLE 若没把 production 带过来，第一次写正文也不能 KeyError。"""
    run = _run()
    del run.generation_context["production"]

    async def _fake_call(*args, **kwargs):
        run.generation_context["production"] = args[4]
        return ChapterProse(content=PROSE)

    async def _cast(*args, **kwargs):
        return {"林晚": {"identity": {"role": "主角"}}}

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    monkeypatch.setattr("regent.novel.application.directing_chapter.load_cast", _cast)
    assert await produce_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run
    ) is False
    saved = run.generation_context["production"]
    assert saved["protocol"] == "chapter"
    assert saved["phase"] == "CHECK"
    assert run.content == PROSE


@pytest.mark.asyncio
async def test_write_then_check_pass_returns_done(monkeypatch):
    run = _run()
    calls: list[str] = []

    async def _fake_call(*args, **kwargs):
        calls.append(kwargs.get("purpose") or args[8])
        return ChapterProse(content=PROSE)

    monkeypatch.setattr(
        "regent.novel.application.directing_chapter._call",
        _fake_call,
    )
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is False
    assert run.generation_context["production"]["phase"] == "CHECK"
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is True
    assert run.generation_context["production"]["phase"] == "DONE"
    assert calls == ["chapter_write"]
    assert run.content == PROSE


@pytest.mark.asyncio
async def test_check_fail_repairs_once_then_stops(monkeypatch):
    run = _run(phase="CHECK", content=PROSE, prior=PROSE)
    assert check_chapter(PROSE, ctx=run.generation_context, cast={"林晚": {}}, title="第二章")

    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is False
    assert run.generation_context["production"]["phase"] == "REPAIR"

    async def _same(*args, **kwargs):
        return ChapterProse(content=PROSE)

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _same)
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is False
    assert run.generation_context["production"]["repairs"] == 1
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run,
        production=run.generation_context["production"],
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "WRITE"
    assert production["rewrites"] == 1
    assert production["repairs"] == 0

    seen: list[dict] = []

    async def _rewrite(*args, **kwargs):
        seen.append({"command": args[9], "payload": args[7]})
        return ChapterProse(content=PROSE)

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _rewrite)
    for expected in ("CHECK", "REPAIR", "CHECK"):
        assert await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run,
            production=run.generation_context["production"],
        ) is False
        assert run.generation_context["production"]["phase"] == expected
    assert seen[0]["command"].endswith(":chapter_write:w1")
    assert seen[0]["payload"]["avoid"]
    with pytest.raises(ProductionStopped, match="重写后仍未通过"):
        await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run,
            production=run.generation_context["production"],
        )


@pytest.mark.asyncio
async def test_near_name_repairs_once_then_passes(monkeypatch):
    stray = PROSE + "林大勇推门进来。"
    run = _run(phase="CHECK", content=stray)
    production = run.generation_context["production"]
    production["cast"] = {"赵大勇": {"identity": {"role": "主角"}}}
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert production["phase"] == "REPAIR"
    assert any("林大勇" in item for item in production["check_fails"])

    async def _same(*args, **kwargs):
        return ChapterProse(content=stray)

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _same)
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is False
    production = run.generation_context["production"]
    assert await produce_chapter_tick(
        None, provider=None, work=SimpleNamespace(id="w"), run=run, production=production
    ) is True
    production = run.generation_context["production"]
    assert production["phase"] == "DONE"
    assert any("林大勇" in item for item in production["soft_issues"])


def test_repeated_span_inside_chapter_is_dropped():
    once = "林晚用刚拿到的铜钥匙打开抽屉，钥匙随即断成两截，她愣在原地。"
    text = PROSE + once + "雨还在下。" + once
    settled = drop_repeated_span(text)
    assert settled.count(once) == 1
    assert not repeated_span(settled)
    fails = check_chapter(
        dedupe_chapter_text(text, PRIOR), ctx={"chapter_no": 2}, cast={"林晚": {}}, title="二"
    )
    assert not any("又出现了一遍" in item for item in fails)


def test_last_chapter_of_stage_must_reach_it():
    stages = ["他进了内门却被盯上", "他在藏经阁找到骨简的来历", "他必须在禁地和宗门之间选一边"]
    rhythm = {"stake": "活下去", "payoff_limit": 3, "stages": stages, "volume_chapters": 30}
    earlier = [
        {"pressure": f"p{i}", "choice": f"c{i}", "result": f"r{i}", "stage_index": 0, "reaches_stage": False}
        for i in range(9)
    ]
    stalled = {"pressure": "新处境", "choice": "新选择", "result": "新结果", "reaches_stage": False}
    assert "最后一章" in rhythm_mismatch(stalled, rhythm, earlier)
    turned = dict(stalled, reaches_stage=True)
    assert rhythm_mismatch(turned, rhythm, earlier) == ""


@pytest.mark.asyncio
async def test_review_does_not_call_model(monkeypatch):
    run = _run(phase="DONE", content=PROSE)
    called = AsyncMock(side_effect=AssertionError("审校不应调用模型"))
    monkeypatch.setattr("regent.novel.application.chapter_review._call", called)
    before = run.content
    assert await validate_chapter(None, provider=None, work=SimpleNamespace(id="w"), run=run) is True
    assert run.content == before
    assert run.review["passed"] is True
    called.assert_not_called()


@pytest.mark.asyncio
async def test_review_does_not_rewind():
    run = _run(phase="DONE", content="太短")
    with pytest.raises(ProductionStopped, match="硬门未过"):
        await validate_chapter(None, provider=None, work=SimpleNamespace(id="w"), run=run)
    assert run.generation_context["production"]["phase"] == "DONE"


@pytest.mark.asyncio
async def test_prepare_drops_ungrounded_facts(monkeypatch):
    text = PROSE
    run = _run(phase="DONE", content=text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    run.review = {"passed": True, "hard_issues": []}
    run.generation_context["production"]["review_history"] = [
        {"content_hash": digest, "passed": True, "hard_issues": []}
    ]
    run.generation_context["production"]["takes"] = [
        {"take_no": 1, "content": text, "status": "ACCEPTED", "validation": {"facts": []}}
    ]
    run.generation_context["production"]["accepted"] = [0]

    async def _fake_call(*args, **kwargs):
        return ChapterFacts(
            facts=[
                {"statement": "林晚收起铜钥匙", "quote": "林晚把铜钥匙放进抽屉"},
                {"statement": "他飞走了", "quote": "他展开翅膀离开雨城"},
            ]
        )

    monkeypatch.setattr("regent.novel.application.directing_chapter._call", _fake_call)
    await prepare_chapter_canon(None, provider=None, work=SimpleNamespace(id="w"), run=run)
    facts = run.generation_context["verified_facts"]
    assert len(facts) == 1
    assert facts[0]["quote"] == "林晚把铜钥匙放进抽屉"
    assert run.generation_context["node_completed"] is False


@pytest.mark.asyncio
async def test_past_last_node_stops_before_model(monkeypatch):
    run = _run()
    run.generation_context["story_complete"] = True
    run.generation_context["target_node"] = {}
    called = AsyncMock(side_effect=AssertionError("不应调用模型"))
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", called)
    with pytest.raises(ProductionStopped, match="主线用尽"):
        await produce_chapter_tick(
            None, provider=None, work=SimpleNamespace(id="w"), run=run,
            production=run.generation_context["production"],
        )
    called.assert_not_called()
    assert run.generation_context["production"]["call_count"] == 0


class _EmptyScalars:
    def all(self):
        return []


class _EmptySession:
    async def scalars(self, *_args, **_kwargs):
        return _EmptyScalars()


@pytest.mark.asyncio
async def test_no_placeholder_cast():
    work = SimpleNamespace(id="w")
    with pytest.raises(ProductionStopped, match="世界书未登记人物"):
        await load_cast(_EmptySession(), work, {"world_bible": {}})
    cast = await load_cast(
        _EmptySession(),
        work,
        {"world_bible": {"personas": [{"name": "林晚", "voice": {"style": "短句"}}]}},
    )
    assert "主角" not in cast
    assert "林晚" in cast


@pytest.mark.asyncio
async def test_pinned_script_scene_still_routes_to_script_tick(monkeypatch):
    seen: dict[str, bool] = {}

    async def _fake(*_args, **_kwargs):
        seen["script"] = True
        return False

    monkeypatch.setattr(
        "regent.novel.application.directing_scene_loop._produce_script_tick",
        _fake,
    )
    run = SimpleNamespace(
        generation_context={
            "architecture_version": "director_script_scene",
            "production": {"protocol": "script_scene", "phase": "BRIEF"},
        }
    )
    assert await produce_tick(None, provider=None, work=None, run=run) is False
    assert seen["script"] is True


def test_length_brief_gives_the_count_and_the_gap():
    assert length_brief("字" * 2300) == {"current_chars": 2300, "min_chars": 2000, "max_chars": 3000}
    assert "need_cut" not in length_brief("字" * 2800)
    assert length_brief("字" * 3500)["need_cut"] == 3500 - FIT_TARGET_CHARS
    assert length_brief("字" * 1800)["need_add"] == 200


def _fact(no: int, *entities: str) -> dict:
    return {
        "statement": f"第{no}章的事：{'、'.join(entities)}",
        "quote": "原文摘录" * 10,
        "entities": ["陈默", *entities],
        "known_by": ["陈默"],
        "chapter_no": no,
        "volume_no": 1,
        "confidence": 1.0,
        "source_tracking": {"take_id": "scene0:take1", "source_hash": "x" * 64},
    }


def test_power_clause_stays_only_for_runs_written_before_it_was_dropped():
    from regent.novel.application.directing_chapter import FRONT_GATE_MARK, _writer_system

    ctx = _run().generation_context
    # 老运行没有标记：回放照旧带金手指条款，prompt_hash 对得上
    assert "金手指" in _writer_system(ctx, {})
    assert "金手指" not in _writer_system({**ctx, "front_gate": FRONT_GATE_MARK}, {})


def test_write_canon_keeps_recent_and_plan_relevant_facts_only():
    canon = [_fact(1, "铜壳怀表"), _fact(1, "旧照片"), *[_fact(5, f"零件{i}") for i in range(45)], _fact(6, "门槛石")]
    data = {
        "canon": canon,
        "chapter_no": 7,
        "production": {"chapter_plan": {"choice": "陈默打开那只铜壳怀表"}},
    }
    cast = {"陈默": {}}
    # 没有标记的老运行：整份原样，回放的 input_hash 不变
    assert write_canon(data, cast) == canon

    kept = write_canon({**data, "write_canon": "relevant"}, cast)
    statements = [fact["statement"] for fact in kept]
    assert statements[0] == "第1章的事：铜壳怀表"
    assert "第1章的事：旧照片" not in statements
    assert statements[-1] == "第6章的事：门槛石"
    assert len(kept) == 1 + 40
    assert set(kept[0]) == {"statement", "known_by", "chapter_no"}


@pytest.mark.parametrize("schema", [ChapterFacts, CanonExtraction])
def test_extra_facts_are_trimmed_not_rejected(schema):
    # 真机 4d48a7fe 第 3 章：模型给了 47 条，整次被拒两回后这一章零事实入正史
    facts = [{"subject": f"人物{i}", "fact": f"第{i}条"} for i in range(47)]
    parsed = schema.model_validate({"facts": facts})
    assert parsed.facts == facts[:40]
