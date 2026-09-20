"""剧本择优对照臂：领域隔离、执行器注册、阶段机 happy path。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import executor as executor_app
from regent.novel.application.generation import execute_step
from regent.novel.application.editor_audit import EditorAuditResult
from regent.novel.domain.script_protocol import (
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
    CastDraftPersona,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptAssignmentBoard,
    ScriptChoice,
    ScriptWriterTask,
    assemble_production_packet,
    assert_rejected_isolated,
)
from regent.novel.domain.states import ChapterStep, chapter_step_order


TEXT = "他把钥匙放在桌上。" + "雨水沿窗棂流下，两个人仍旧没有开口。" * 65


def _script(title: str, *, cost_type: str, payoff: str) -> ChapterScript:
    return ChapterScript(
        title_line=title,
        start_state="门口施压",
        end_change="把柄易手",
        protagonist_want="护住底线",
        opposition="对方逼选",
        beats=["施压", "权衡", "代价落地"],
        turning_point="当场表态",
        turning_reason="不选损失更大",
        cost=f"{cost_type}代价",
        cost_type=cost_type,
        key_dialogue=["你现在就选。"],
        protagonist_knows="有限",
        protagonist_weighs="交出会失线索",
        reading_question="还能不能翻盘",
        power_payoff=payoff,
        cast_draft=[
            CastDraftPersona(
                name="弃选幽灵",
                desire="不该进正式表",
                relation="",
                knowledge="",
                limit="",
                voice="",
            )
        ],
    )


def test_assemble_isolates_rejected_script():
    selected = _script("候选甲", cost_type="暴露", payoff="读出伪证")
    rejected = _script(
        "候选乙-独特弃选标题",
        cost_type="亲情把柄被扣住不得赎回",
        payoff="用金手指换信息却把家人推上赌桌",
    )
    choice = ScriptChoice(
        selected_id="alpha",
        reason="代价更非常规",
        weakness="",
        must_land_beats=["当场代价"],
        allow_writer_room="",
        direction_notes="",
    )
    packet = assemble_production_packet(selected=selected, choice=choice, meta={"x": 1})
    assert_rejected_isolated(
        packet,
        {"beta": rejected.model_dump(mode="json")},
        selected=selected,
    )
    blob = str(packet)
    assert "弃选幽灵" in blob  # 选定稿 cast_draft 可在包内
    assert "候选乙-独特弃选标题" not in blob
    # 导演灵感可点名落选点子，不算泄漏
    inspired = dict(packet)
    inspired["direction"] = {
        **(packet.get("direction") or {}),
        "borrowable_ideas": [rejected.cost_type],
        "inspiration_from": ["beta"],
    }
    assert_rejected_isolated(
        inspired,
        {"beta": rejected.model_dump(mode="json")},
        selected=selected,
    )
    with pytest.raises(ValueError, match="rejected script leaked"):
        leak = dict(packet)
        leak["script"] = {
            **(packet.get("script") or {}),
            "extra_note": rejected.cost_type,
        }
        assert_rejected_isolated(
            leak,
            {"beta": rejected.model_dump(mode="json")},
            selected=selected,
        )


def test_script_scene_is_default_executor(monkeypatch):
    assert executor_app.STABLE_EXECUTOR == d.SCRIPT_SCENE_ARCHITECTURE
    assert executor_app.executor_version(d.SCRIPT_ARCHITECTURE) == "director_script@1"
    assert executor_app.executor_version(d.SCRIPT_SCENE_ARCHITECTURE) == (
        "director_script_scene@1"
    )
    assert d.SCRIPT_ARCHITECTURE in executor_app.KNOWN_EXECUTORS
    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY", raising=False)
    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY_PERCENT", raising=False)
    assert executor_app.choose_executor(str(uuid.uuid4())) == d.SCRIPT_SCENE_ARCHITECTURE
    assert d.production_protocol(
        type(
            "R",
            (),
            {"generation_context": {"architecture_version": d.SCRIPT_ARCHITECTURE}},
        )()
    ) == PROTOCOL_SCRIPT
    assert d.production_protocol(
        type(
            "R",
            (),
            {
                "generation_context": {
                    "architecture_version": d.SCRIPT_SCENE_ARCHITECTURE
                }
            },
        )()
    ) == PROTOCOL_SCRIPT_SCENE
    assert chapter_step_order(
        {"architecture_version": d.SCRIPT_ARCHITECTURE}
    ) == chapter_step_order({"architecture_version": d.ARCHITECTURE})


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(
            output=output.model_copy(deep=True), usage=ModelUsage(10, 20), model="test"
        )


class Session:
    async def scalar(self, query):
        return None

    async def scalars(self, query):
        return SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    name="主角",
                    identity={},
                    drives={},
                    voice={"style": "克制"},
                )
            ]
        )

    def add(self, row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass


def _script_outputs():
    quote = "他把钥匙放在桌上。"
    return [
        ScriptAssignmentBoard(
            goal_restated="写出可拍第一章，金手指当场兑现",
            tasks=[
                ScriptWriterTask(slot="alpha", task="公开场域改写当面结果"),
                ScriptWriterTask(slot="beta", task="先稳住，把反噬推到对手侧"),
                ScriptWriterTask(slot="gamma", task="借第三方施压"),
                ScriptWriterTask(slot="delta", task="小规模试探规则与代价"),
            ],
        ),
        _script("候选甲", cost_type="暴露", payoff="读出伪证"),
        _script("候选乙", cost_type="亲情", payoff="赌上家人"),
        _script("候选丙", cost_type="借刀", payoff="借剪辑师施压"),
        _script("候选丁", cost_type="试探", payoff="只改一句测代价"),
        ScriptChoice(
            selected_id="alpha",
            reason="钩子更深",
            weakness="",
            must_land_beats=["当场代价", "金手指兑现"],
            allow_writer_room="可加强感官",
            direction_notes="贴主角",
            inspiration_from=["delta"],
            borrowable_ideas=["丁的小试探节奏"],
        ),
        d.SceneText(content=TEXT),
        d.ScriptChapterValidation(
            hard_fails=[],
            soft_notes=[],
            facts=[
                d.VerifiedFact(
                    statement="钥匙放在桌上",
                    quote=quote,
                    known_by=["主角"],
                )
            ],
        ),
        ChapterCreativeAccept(accept=True, fault="ok", notes="可继续"),
        d.ChapterValidation(
            passed=True, node_completed=True, completion_quote=quote
        ),
        EditorAuditResult(passed=True, issues=[]),
    ]


@pytest.mark.asyncio
async def test_script_protocol_happy_path_no_rejected_in_cast():
    provider = Provider(_script_outputs())
    session = Session()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context={
            "architecture_version": d.SCRIPT_ARCHITECTURE,
            "executor": d.SCRIPT_ARCHITECTURE,
            "executor_version": "director_script@1",
            "canon": [],
            "actual_state": {},
            "recent_chapters": [],
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="DIRECT",
        content="",
        title="",
        word_count=0,
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())
    await execute_step(session, provider=provider, work=work, run=run, step=ChapterStep.DIRECT)
    production = run.generation_context["production"]
    assert production["protocol"] == PROTOCOL_SCRIPT
    assert production["phase"] == "BRIEF"
    assert "弃选幽灵" not in str(production.get("cast"))

    for _ in range(20):
        done = await execute_step(
            session, provider=provider, work=work, run=run, step=ChapterStep.PRODUCE
        )
        if done:
            break
    else:
        pytest.fail("script protocol did not converge")

    production = run.generation_context["production"]
    assert production["phase"] == "DONE"
    sp = production["script_protocol"]
    assert sp["selected_id"] == "alpha"
    assert "beta" in sp["rejected"]
    assert "gamma" in sp["rejected"]
    assert "delta" in sp["rejected"]
    assert len(sp["rejected"]) == 3
    packet = sp["production_packet"]
    assert "小试探" in str(packet.get("direction") or {})
    assert_rejected_isolated(packet, sp["rejected"], selected=packet.get("script"))
    assert "弃选幽灵" in str(packet.get("cast"))
    # 正式 cast 仍只有既有角色，候选人未登记
    assert list(production["cast"]) == ["主角"]
    assert "弃选幽灵" not in production["cast"]
    assert run.content.startswith("他把钥匙放在桌上。")

    await d.validate_chapter(session, provider=provider, work=work, run=run)
    assert run.review["passed"]
    facts = run.generation_context.get("verified_facts") or []
    assert facts
    assert all("候选乙" not in str(f) for f in facts)
