"""缺陷 6：导演的角色引用不合规 → 整章判死。

四道防线各自要有守卫：

① **确定性归一**——``陈渡（记忆观察者）`` 意图明确，归一即可，不必重做规划；
② **角色表不是封闭集合**——导演可以带新人物进场，但必须**声明**，由系统登记；
③ **违规可读化**——判死时要说出**哪个名字**不能用，否则自修反馈无从下手；
④ **带反馈自修**——规划用 temperature=0，入参不变的盲重试会以近乎确定的方式
   产出同一个坏名字，所以自修必须改入参。

不测的东西：模糊匹配**故意不做**（猜错等于把一个人的戏记到另一个人头上，且
不可逆）；声明了没出场的新人物**故意不登记**（角色表会被带进后续每一章的
上下文，随手声明不该永久抬高成本）。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import ProductionStopped

# 角色表里本来就带括号的名字也必须有：它检验「原样是键则原样保留」这条优先规则。
CAST_NAMES = ["季影", "陈渡", "陈默父亲（陈远舟）"]


def _cast(names=CAST_NAMES) -> dict:
    return {name: {"identity": {}, "drives": {}, "voice": {}} for name in names}


CAST = _cast()


def _brief(*personas: str) -> d.SceneBrief:
    return d.SceneBrief(
        purpose="建立不对等信任",
        setting="旧宅",
        conflict="信任与试探",
        exit_condition="主角交出钥匙",
        actors=[
            d.ActorDirection(persona=name, objective="取得信任", instruction="等对方先开口")
            for name in personas
        ],
        narrative=d.NarrativeSpec(
            viewpoint="陈渡",
            distance="近",
            style="克制",
            reader_effect="担忧",
            disclosure_rule="不揭露同伴秘密",
        ),
    )


def _new(name: str) -> d.NewPersonaSpec:
    return d.NewPersonaSpec(
        name=name,
        voice="沙哑，一句话不超过十个字",
        identity="码头搬工",
        drives="想讨回被拖欠的工钱",
        reason="这场需要一个不在既有关系网里的目击者",
    )


def _direction(*scene_personas: tuple[str, ...], new_personas=()) -> d.ChapterDirection:
    return d.ChapterDirection(
        title="倒走的时针·第一章",
        reader_intent="想知道时钟为何倒走",
        ending_reason="钥匙已交出",
        scenes=[_brief(*names) for names in scene_personas] or [_brief("陈渡")],
        new_personas=list(new_personas),
    )


# --------------------------------------------------------------- ① 确定性归一


def test_persona_that_is_itself_a_cast_key_is_kept_verbatim():
    """角色表名本来就带括号时，去掉括号反而会毁掉身份，必须原样保留。"""
    assert d._canonical_persona("陈默父亲（陈远舟）", CAST) == "陈默父亲（陈远舟）"


def test_trailing_annotation_is_normalized_to_the_cast_key():
    """`陈渡（记忆观察者）` 的意图明确：归一，而不是为此重做整章规划。"""
    assert d._canonical_persona("陈渡（记忆观察者）", CAST) == "陈渡"


@pytest.mark.parametrize("name", ["陈远舟（记忆中的父亲）", "陌生人（老周）", "陈渡（"])
def test_names_that_resolve_to_nobody_are_not_guessed(name: str):
    """归一不了就返回 None。不做模糊匹配：猜错的代价是把戏记错人，且不可逆。"""
    assert d._canonical_persona(name, CAST) is None


def test_brief_issues_normalizes_in_place_and_reports_nothing():
    brief = _brief("陈渡（记忆观察者）", "季影")
    assert d._brief_issues(brief, CAST) == []
    assert [a.persona for a in brief.actors] == ["陈渡", "季影"]


def test_brief_issues_detects_duplicate_only_visible_after_normalization():
    """:`陈渡` 与 `陈渡（旁观者）` 字面上不重复，归一后是同一个人——必须判重复。"""
    brief = _brief("陈渡", "陈渡（旁观者）")
    issues = d._brief_issues(brief, CAST)
    assert len(issues) == 1 and "同一个人" in issues[0]


# ---------------------------------------------------- ② 角色表不是封闭集合


def test_declared_new_persona_becomes_a_legal_reference():
    """导演声明过的新名字应当被接受——角色表是起点，不是牢笼。"""
    direction = _direction(("陈渡", "老周"), new_personas=[_new("老周")])
    merged, issues = d._declared_cast(direction, CAST)
    assert issues == []
    assert "老周" in merged
    assert d._brief_issues(direction.scenes[0], merged) == []


def test_declaring_an_existing_persona_is_rejected():
    """「新增人物」不能变成顶替既有角色的暗门。"""
    direction = _direction(("陈渡",), new_personas=[_new("陈渡")])
    _, issues = d._declared_cast(direction, CAST)
    assert issues and "已是既有角色" in issues[0]


def test_new_persona_budget_is_bounded():
    """每章新增人物要有上限：角色表会被带进后续每一章的上下文与信息集。"""
    direction = _direction(("陈渡",), new_personas=[_new("甲"), _new("乙"), _new("丙")])
    _, issues = d._declared_cast(direction, CAST)
    assert issues and f"最多新增 {d.MAX_NEW_PERSONAS_PER_CHAPTER}" in issues[0]


# --------------------------------------------------------------- ③ 违规可读化


def test_check_brief_names_the_offending_persona():
    """判死信息必须点名，否则自修反馈只能重复一遍规则（等于没说）。"""
    with pytest.raises(ProductionStopped) as info:
        d._check_brief(_brief("陈远舟（记忆中的父亲）", "季影"), CAST)
    assert "陈远舟（记忆中的父亲）" in str(info.value)


# --------------------------------------------------------------- ④ 带反馈自修


class _CallLog:
    """替代 ``d._call``：省掉账本与真实 provider，只记录入参与命令标识。"""

    def __init__(self, outputs: list[d.ChapterDirection]) -> None:
        self._outputs = list(outputs)
        self.payloads: list[dict] = []
        self.command_ids: list[str] = []

    async def __call__(self, session, provider, work, run, production, schema, system,
                       payload, purpose, command_id):
        self.payloads.append(payload)
        self.command_ids.append(command_id)
        production["call_count"] = production.get("call_count", 0) + 1
        return self._outputs.pop(0)


class _Result:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def all(self):
        return [SimpleNamespace(name=n, identity={}, drives={}, voice={}) for n in self._names]


class _Session:
    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.added: list = []

    async def scalars(self, _stmt):
        return _Result(self._names)

    def add_all(self, rows):
        self.added.extend(rows)

    async def flush(self):
        return None


def _run() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        input_version=1,
        branch_id=uuid.uuid4(),
        chapter_no=1,
        user_guidance={},
        current_step="DIRECT",
        title="",
        generation_context={"architecture_version": d.ARCHITECTURE, "production": {}},
    )


def _plan(monkeypatch, outputs, names=CAST_NAMES):
    log = _CallLog(outputs)
    from regent.novel.application import directing_planning

    monkeypatch.setattr(directing_planning, "_call", log)
    run = _run()
    session = _Session(names)
    asyncio.run(
        d.plan_chapter(
            session, provider=SimpleNamespace(model_name="test"),
            work=SimpleNamespace(id=uuid.uuid4()), run=run,
        )
    )
    return run, log, session


def test_plan_registers_declared_new_persona_without_spending_a_repair(monkeypatch):
    """声明过的新人物要真的落进角色表——下游拿它当身份用（信息隔离、声纹、正典）。"""
    run, log, session = _plan(
        monkeypatch, [_direction(("陈渡", "老周"), new_personas=[_new("老周")])]
    )
    assert len(log.command_ids) == 1, "声明过的新人物不该触发自修"
    assert [p.name for p in session.added] == ["老周"], "新人物没有落库"
    production = run.generation_context["production"]
    assert "老周" in production["cast"], "新人物没有并入本章角色表"
    assert production["cast"]["老周"]["voice"] == {"style": "沙哑，一句话不超过十个字"}


def test_unused_declaration_is_not_registered(monkeypatch):
    """声明了没出场的不入库：不让它永久撑大后续每一章的上下文。"""
    _run_out, _log, session = _plan(
        monkeypatch, [_direction(("陈渡",), new_personas=[_new("路人甲")])]
    )
    assert session.added == []


def test_plan_repairs_undeclared_persona_with_feedback_and_keeps_the_chapter(monkeypatch):
    """没声明就冒出来的名字不该判死整章：导演应拿到具体反馈后自修成功。"""
    run, log, _session = _plan(
        monkeypatch,
        [
            _direction(("陈渡", "陈远舟（记忆中的父亲）"), ("季影",)),
            _direction(("陈渡", "季影")),
        ],
    )
    assert len(log.command_ids) == 2, "应当只自修一次就通过"
    # 自修是新的逻辑调用；沿用原 command_id 会被幂等键挡住或复用上一次的坏结果
    assert log.command_ids[1].endswith(":r1")
    feedback = log.payloads[1].get("repair_instructions")
    assert feedback, "自修请求没有携带反馈，等于 temperature=0 下的盲重试"
    joined = "".join(feedback)
    assert "陈远舟（记忆中的父亲）" in joined, "反馈没有点名是哪个名字不能用"
    assert all(name in joined for name in CAST_NAMES), "反馈没有给出可用的在册名单"
    assert "new_personas" in joined, "反馈没有告诉导演可以声明新人物"
    assert run.generation_context["production"]["plan"]["scenes"][0]["actors"][0][
        "persona"
    ] == "陈渡"


def test_plan_normalizes_annotation_without_spending_a_repair(monkeypatch):
    """`陈渡（记忆观察者）` 归一即可，不该为它多花一次模型调用。"""
    run, log, _session = _plan(monkeypatch, [_direction(("陈渡（记忆观察者）", "季影"))])
    assert len(log.command_ids) == 1, "可归一的名字不该触发自修"
    actors = run.generation_context["production"]["plan"]["scenes"][0]["actors"]
    assert [a["persona"] for a in actors] == ["陈渡", "季影"]


def test_plan_repair_budget_is_bounded(monkeypatch):
    """自修必须有上限：反复造人的导演不能无限调用下去。"""
    bad = _direction(("陌生人（老周）", "季影"))
    with pytest.raises(ProductionStopped) as info:
        _plan(monkeypatch, [bad] * (d.MAX_PLAN_REPAIRS + 1))
    assert "自修次数已用尽" in str(info.value)
