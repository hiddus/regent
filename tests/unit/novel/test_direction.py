# Chinese fixture prose deliberately uses full-width punctuation.
# ruff: noqa: RUF001

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.application.generation import assemble, canon, execute_step
from regent.novel.domain.states import ChapterStep, chapter_step_order
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    CostEntryModel,
    CriticalNodeModel,
    CriticalPathModel,
    ModelCallModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    QuotaReservationModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

TEXT = "他把钥匙放在桌上。" + "雨水沿窗棂流下，两个人仍旧没有开口。" * 65


def brief(instruction="让对方主动开口"):
    return d.SceneBrief(
        purpose="建立不对等信任",
        setting="旧宅",
        conflict="信任与试探",
        exit_condition="主角交出钥匙",
        actors=[d.ActorDirection(persona="主角", objective="取得信任", instruction=instruction)],
        narrative=d.NarrativeSpec(
            viewpoint="主角",
            distance="近",
            style="克制",
            reader_effect="担忧",
            disclosure_rule="不揭露同伴秘密",
        ),
    )


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(output=output, usage=ModelUsage(10, 20), model="test")


def resolution(statement="他把钥匙放在桌上。", changes=None):
    return d.SceneResolution(
        events=[
            d.SceneEvent(
                statement=statement,
                known_by=["主角"],
                reader_visible=True,
                state_changes=changes or {"key": "桌上"},
            )
        ]
    )


def take_decision(action="RENDER", revised=None, evidence="他把钥匙放在桌上。"):
    return d.TakeDirection(
        action=action,
        observation="信任有了具体表现",
        evidence=[evidence],
        instruction="用停顿表现犹豫",
        revised_brief=revised,
    )


def prose_decision(action="ACCEPT"):
    return d.ProseDirection(
        action=action,
        observation="留白形成担忧",
        evidence=["他把钥匙放在桌上。"],
        instruction="缩短雨景，用动作表现犹豫",
    )


def validation():
    return d.SceneValidation(
        passed=True,
        facts=[
            d.VerifiedFact(
                statement="钥匙放在桌上",
                quote="他把钥匙放在桌上。",
                known_by=["主角"],
                entities=["钥匙"],
            )
        ],
        state_changes=[d.VerifiedStateChange(key="key", value="桌上", quote="他把钥匙放在桌上。")],
    )


def successful_outputs():
    return [
        d.ChapterDirection(
            title="交付", reader_intent="为信任担心", ending_reason="交付已成立", scenes=[brief()]
        ),
        d.ActorTurn(intention="信任", actions=["伸手"], private_reasoning="PRIVATE_THOUGHT"),
        resolution(),
        take_decision(),
        d.SceneText(content=TEXT),
        prose_decision(),
        validation(),
        d.ChapterValidation(
            passed=True, node_completed=True, completion_quote="他把钥匙放在桌上。"
        ),
    ]


class Session:
    def __init__(self):
        self.rows = []
        self.commits = 0

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
        self.rows.append(row)

    async def flush(self):
        pass

    async def commit(self):
        """调用在事务外进行：预留先提交，再发起模型调用（§4.4）。"""
        self.commits += 1


def context():
    return {
        "architecture_version": d.ARCHITECTURE,
        "canon": [
            {"statement": "SECRET_OTHER", "known_by": ["同伴"]},
            {"statement": "SECRET_MISSING_VISIBILITY"},
            {"statement": "PUBLIC_FACT", "known_by": ["ALL"]},
        ],
        "actual_state": {},
        "recent_chapters": [{"content": "SECRET_ENDING"}],
    }


def run_object():
    return SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context=context(),
        performances=[],
        review={},
        user_guidance={},
        current_step="DIRECT",
        content="",
        title="",
        word_count=0,
    )


async def tick_to_done(session, provider, work, run):
    for _ in range(50):
        before = len(provider.requests)
        done = await execute_step(
            session, provider=provider, work=work, run=run, step=ChapterStep.PRODUCE
        )
        assert len(provider.requests) - before <= 1
        if done:
            return
    pytest.fail("director did not converge")


async def test_director_precedes_actors_and_writer_receives_only_visible_material():
    provider, session, run = Provider(successful_outputs()), Session(), run_object()
    work = SimpleNamespace(id=uuid.uuid4())
    await execute_step(session, provider=provider, work=work, run=run, step=ChapterStep.DIRECT)
    assert provider.requests[0]["schema"] is d.ChapterDirection
    await tick_to_done(session, provider, work, run)
    await d.validate_chapter(session, provider=provider, work=work, run=run)
    actor = next(r for r in provider.requests if r["schema"] is d.ActorTurn)
    writer = next(r for r in provider.requests if r["schema"] is d.SceneText)
    assert "PUBLIC_FACT" in actor["user_prompt"]
    for secret in ("SECRET_OTHER", "SECRET_MISSING_VISIBILITY", "SECRET_ENDING"):
        assert secret not in actor["user_prompt"]
        assert secret not in writer["user_prompt"]
    assert "PRIVATE_THOUGHT" not in writer["user_prompt"]
    assert run.review["passed"]
    assert run.generation_context["actual_state"] == {"key": "桌上"}
    assert len(run.generation_context["production"]["decisions"]) == 2


async def test_retake_keeps_rejected_events_out_of_state_and_future_context():
    outputs = successful_outputs()
    rejected = [
        d.ActorTurn(intention="试探", actions=["藏钥匙"]),
        resolution("错误尝试留下的事件", {"poison": "REJECTED_SECRET"}),
        take_decision("RETAKE", brief("改为主动交出钥匙"), "错误尝试留下的事件"),
    ]
    provider = Provider(outputs[:1] + rejected + outputs[1:])
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    production = run.generation_context["production"]
    assert production["takes"][0]["status"] == "REJECTED"
    assert production["accepted"] == [1]
    assert "poison" not in production["working_state"]
    actors = [r for r in provider.requests if r["schema"] is d.ActorTurn]
    assert "改为主动交出钥匙" in actors[1]["user_prompt"]
    assert "REJECTED_SECRET" not in actors[1]["user_prompt"]


async def test_director_rewrite_is_rewatched_and_revalidated():
    outputs = successful_outputs()
    outputs[5:5] = [prose_decision("REWRITE"), d.SceneText(content=TEXT + "门外响起雨声。")]
    provider = Provider(outputs)
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    schemas = [r["schema"] for r in provider.requests]
    assert schemas.count(d.ProseDirection) == 2
    assert schemas[-1] is d.SceneValidation
    assert len(run.generation_context["production"]["takes"][0]["prose_versions"]) == 2


async def test_director_cannot_override_failed_independent_validation():
    outputs = [
        *successful_outputs()[:6],
        d.SceneValidation(passed=True, issues=["泄露秘密"]),
        prose_decision(),
    ]
    provider = Provider(outputs)
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    with pytest.raises(d.ProductionStopped, match="不能接受"):
        await tick_to_done(session, provider, work, run)
    assert not run.generation_context["production"]["accepted"]
    assert not run.content


async def test_call_budget_stops_before_another_provider_request():
    provider = Provider(successful_outputs())
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    run.generation_context["production"]["call_count"] = d.MAX_CALLS
    with pytest.raises(d.ProductionStopped, match="预算"):
        await d.produce_tick(session, provider=provider, work=work, run=run)
    assert len(provider.requests) == 1


async def test_canon_rejects_prose_changed_after_validation():
    provider = Provider(successful_outputs())
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    await d.validate_chapter(session, provider=provider, work=work, run=run)
    run.content += "修改了结局"
    with pytest.raises(RuntimeError, match="UNVALIDATED_CHAPTER"):
        await canon(session, provider=provider, work=work, run=run)


def test_executor_version_keeps_inflight_legacy_order():
    assert chapter_step_order()[1] == ChapterStep.PERFORM
    assert chapter_step_order({"architecture_version": d.ARCHITECTURE}) == (
        ChapterStep.ASSEMBLE,
        ChapterStep.DIRECT,
        ChapterStep.PRODUCE,
        ChapterStep.REVIEW,
        ChapterStep.CANON,
    )


@pytest.mark.parametrize("repair", [False, True])
async def test_worker_reloads_each_checkpoint_and_commits_only_verified_facts(
    monkeypatch, repair, novel_db
):
    # SQLite exercises actual JSON persistence and ledgers. The Postgres-specific
    # event sequence allocator is outside this test; capture user payloads here.
    events = []

    async def append_event(session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(works, "append_event", append_event)
    # 与计费库同源：ModelCall / 预留 / 成本流水必须落在同一本账上。
    sessions = novel_db
    engine = sessions.kw["bind"]
    owner, work_id = uuid.uuid4(), uuid.uuid4()
    outputs = successful_outputs()
    if repair:
        outputs[-1:] = [
            d.ChapterValidation(passed=False, issues=["场景衔接冲突"], failed_scene_index=0),
            prose_decision("REWRITE"),
            d.SceneText(content=TEXT + "门外响起雨声。"),
            prose_decision(),
            validation(),
            d.ChapterValidation(passed=True),
        ]
    provider = Provider(outputs)
    try:
        async with sessions() as session:
            session.add(NovelPrincipalModel(id=owner, subject="director-test"))
            work = StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
            session.add(work)
            session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
            session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
            path_id = uuid.uuid4()
            session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=2))
            for ordinal in (1, 2):
                session.add(
                    CriticalNodeModel(
                        id=uuid.uuid4(),
                        path_id=path_id,
                        node_id=f"n{ordinal}",
                        ordinal=ordinal,
                        title=f"节点{ordinal}",
                    )
                )
            await session.commit()
            progress = await works.start_run(session, owner_id=owner, work_id=work_id)
            assert "PRODUCE" in progress.steps and "PERFORM" not in progress.steps
            await session.commit()
        for _ in range(25):
            async with sessions() as session:
                progress = await works.advance_background_run(session, provider=provider)
                await session.commit()
                if progress and progress.state.value == "CANONIZED":
                    break
        else:
            pytest.fail("worker did not finish")
        async with sessions() as session:
            run = await session.scalar(select(ChapterRunModel))
            expected = TEXT + "门外响起雨声。" if repair else TEXT
            assert run.content == expected
            assert run.generation_context["production"]["phase"] == "DONE"
            assert run.review["revised"] is repair
            assert run.generation_context["production"]["call_count"] == len(provider.requests)
            calls = (await session.scalars(select(ModelCallModel))).all()
            assert len(calls) == len(provider.requests)
            assert {c.status for c in calls} == {"SUCCEEDED"}
            consumed = (
                await session.scalars(
                    select(CostEntryModel).where(CostEntryModel.entry_kind == "CONSUME")
                )
            ).all()
            released = (
                await session.scalars(
                    select(CostEntryModel).where(CostEntryModel.entry_kind == "RELEASE")
                )
            ).all()
            assert len(consumed) == len(provider.requests)
            # 账本恒等式：预留 = 已结算 + 已释放（G-10，余额可由流水重放）
            reserved = (await session.scalars(select(QuotaReservationModel))).all()
            assert sum(r.amount_minor for r in reserved) == sum(
                c.amount_minor for c in consumed
            ) + sum(r.amount_minor for r in released)
            assert all(c.price_book_version for c in consumed)
            commit = await session.scalar(select(CanonCommitModel))
            assert commit.facts[0]["quote"] in run.content
            before = len(provider.requests)
            await works.advance_step(
                session, provider=provider, owner_id=owner, work_id=work_id, chapter_no=1
            )
            assert len(provider.requests) == before
            assert progress.completed_scenes == progress.scene_count == 1
            # A newer failed attempt and its orphaned Canon must not replace
            # the accepted chapter in reading or the next chapter's context.
            session.add(
                ChapterRunModel(
                    id=uuid.uuid4(),
                    work_id=work_id,
                    branch_id=run.branch_id,
                    chapter_no=1,
                    attempt=2,
                    state="TERMINAL_FAILED",
                    content="废弃稿",
                )
            )
            session.add(
                CanonCommitModel(
                    id=uuid.uuid4(),
                    work_id=work_id,
                    branch_id=run.branch_id,
                    chapter_no=1,
                    parent_version=1,
                    version=2,
                    facts=[{"statement": "ORPHAN_SECRET", "known_by": ["ALL"]}],
                    source_hash=hashlib.sha256("废弃稿".encode()).hexdigest(),
                    validation_id="orphan",
                )
            )
            await session.flush()
            edition = await works.get_chapter(
                session, owner_id=owner, work_id=work_id, chapter_no=1
            )
            assert edition.content == expected
            following = SimpleNamespace(
                chapter_no=2, generation_context={"architecture_version": d.ARCHITECTURE}
            )
            await assemble(session, work=await session.get(StoryWorkModel, work_id), run=following)
            assert "ORPHAN_SECRET" not in json.dumps(following.generation_context)
            assert following.generation_context["actual_state"] == {"key": "桌上"}
            assert following.generation_context["target_node"]["id"] == ("n1" if repair else "n2")
        assert "PRIVATE_THOUGHT" not in json.dumps(events, default=str)
        assert any(e["event_type"] == "chapter.scene_progress" for e in events)
    finally:
        await engine.dispose()


async def test_later_scene_receives_only_accepted_prior_observations():
    outputs = successful_outputs()
    plan = outputs[0].model_copy(update={"scenes": [brief(), brief("等待对方回应")]})
    provider = Provider([plan, *outputs[1:7], *outputs[1:7]])
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    actors = [json.loads(r["user_prompt"]) for r in provider.requests if r["schema"] is d.ActorTurn]
    assert actors[0]["observations"] == []
    assert actors[1]["observations"][0]["statement"] == "他把钥匙放在桌上。"
    assert run.content == TEXT + "\n\n" + TEXT
    assert len(run.generation_context["production"]["accepted"]) == 2


async def test_state_changes_without_prose_evidence_cannot_be_accepted():
    outputs = successful_outputs()[:6]
    outputs += [validation().model_copy(update={"state_changes": []}), prose_decision()]
    provider = Provider(outputs)
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    with pytest.raises(d.ProductionStopped, match="不能接受"):
        await tick_to_done(session, provider, work, run)
    assert run.generation_context["production"]["working_state"] == {}


async def test_continue_uses_resolved_observations_before_next_action():
    outputs = successful_outputs()
    outputs[3:3] = [
        take_decision("CONTINUE"),
        d.ActorTurn(intention="等待回应", actions=["后退一步"]),
        resolution("他等待回答。"),
    ]
    provider = Provider(outputs)
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    actors = [json.loads(r["user_prompt"]) for r in provider.requests if r["schema"] is d.ActorTurn]
    assert actors[1]["turn"] == 1
    assert actors[1]["observations"][0]["statement"] == "他把钥匙放在桌上。"


async def test_scene_state_follows_the_runtime_machine_and_records_manifests():
    """单场戏跑通：场景阶段由 Runtime 推进，每次上下文装配都有 manifest 留痕。"""
    provider = Provider(successful_outputs())
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)
    take = run.generation_context["production"]["takes"][0]
    assert take["scene_state"] == "ACCEPTED"
    assert take["status"] == "ACCEPTED"
    audiences = [m["audience"] for m in take["manifests"]]
    assert audiences == ["actor", "director", "writer"]
    # 同样的来源必须得到同样的投影：manifest hash 可复算、可核对。
    assert all(m["manifest_hash"] and m["projection_hash"] for m in take["manifests"])


async def test_director_retake_without_a_new_brief_is_rejected_by_the_runtime():
    """模型提出越界命令时由 Runtime 拒绝，而不是被静默执行。"""
    outputs = successful_outputs()
    outputs[3:3] = [take_decision("RETAKE", revised=None, evidence="他把钥匙放在桌上。")]
    provider = Provider(outputs)
    session, run, work = Session(), run_object(), SimpleNamespace(id=uuid.uuid4())
    await d.plan_chapter(session, provider=provider, work=work, run=run)
    with pytest.raises(d.ProductionStopped, match="重演必须给出修改后的场景指令"):
        await tick_to_done(session, provider, work, run)
    production = run.generation_context["production"]
    assert len(production["takes"]) == 1
    assert production["takes"][0]["scene_state"] == "DIRECTOR_VIEW"


async def test_first_chapter_completes_without_per_scene_review(novel_db, monkeypatch):
    """旅程验收：用户不需要逐场审核也能完成首章（P1-4）。

    全程只在 worker tick 中推进，作品不得进入等待人工输入或等待裁决的状态——
    那意味着导演把创作决定推回给了用户。
    """
    events: list[dict] = []

    async def append_event(session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(works, "append_event", append_event)

    owner, work_id = uuid.uuid4(), uuid.uuid4()
    provider = Provider(successful_outputs())
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="journey-test"))
        work = StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
        session.add(work)
        session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
        session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=2))
        for ordinal in (1, 2):
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path_id,
                    node_id=f"n{ordinal}",
                    ordinal=ordinal,
                    title=f"节点{ordinal}",
                )
            )
        await session.commit()
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()

    seen_states: list[str] = []
    for _ in range(30):
        async with novel_db() as session:
            progress = await works.advance_background_run(session, provider=provider)
            await session.commit()
            if progress is None:
                break
            seen_states.append(progress.state.value)
            if progress.state.value == "CANONIZED":
                break
    else:
        pytest.fail("worker did not finish the first chapter")

    async with novel_db() as session:
        run = await session.scalar(select(ChapterRunModel))
        assert run.content
        assert run.word_count == len(run.content)
    blocked = {"AWAITING_INPUT", "PENDING_DECISION"}
    assert not (set(seen_states) & blocked), f"首章把决定推回给了用户：{seen_states}"
    assert any(e.get("event_type") == "chapter.done" for e in events)
