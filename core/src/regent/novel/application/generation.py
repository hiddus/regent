"""可恢复的小说章节 Agent loop。

主流程固定为 ASSEMBLE → PERFORM → DIRECT → WEAVE → REVIEW → CANON。
Hive 只存在于 PERFORM 内：多个角色的信息集彼此隔离且任务可并行时并发执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.domain.states import ChapterStep
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    InformationSetModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)


class Performance(BaseModel):
    persona: str
    immediate_goal: str
    private_reasoning: str
    actions: list[str] = Field(min_length=1, max_length=6)
    dialogue: list[str] = Field(default_factory=list, max_length=8)
    emotional_shift: str


class DirectorPlan(BaseModel):
    scene_goal: str
    beats: list[str] = Field(min_length=3, max_length=10)
    allowed_revelations: list[str] = Field(default_factory=list)
    forbidden_revelations: list[str] = Field(default_factory=list)
    ending_hook: str


class ChapterDraft(BaseModel):
    title: str
    content: str = Field(min_length=600)


class ChapterReview(BaseModel):
    passed: bool
    continuity_issues: list[str] = Field(default_factory=list)
    leakage_issues: list[str] = Field(default_factory=list)
    prose_issues: list[str] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)


class CanonExtraction(BaseModel):
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


async def _latest_goal(session: AsyncSession, work_id: uuid.UUID) -> StoryGoalModel:
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    if goal is None:
        raise RuntimeError("story goal missing")
    return goal


async def _canon_facts(session: AsyncSession, work: StoryWorkModel) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(CanonCommitModel)
        .where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
        )
        .order_by(CanonCommitModel.version.desc())
        .limit(8)
    )
    facts: list[dict[str, Any]] = []
    for row in reversed(rows.all()):
        facts.extend(row.facts or [])
    return facts[-80:]


async def assemble(
    session: AsyncSession, *, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    goal = await _latest_goal(session, work.id)
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    nodes: list[CriticalNodeModel] = []
    if path is not None:
        nodes = list(
            (
                await session.scalars(
                    select(CriticalNodeModel)
                    .where(CriticalNodeModel.path_id == path.id)
                    .order_by(CriticalNodeModel.ordinal)
                )
            ).all()
        )
    target_index = min(len(nodes) - 1, max(0, (run.chapter_no - 1) // 2)) if nodes else -1
    target = nodes[target_index] if target_index >= 0 else None
    canon = await _canon_facts(session, work)
    run.generation_context = {
        "raw_intent": goal.raw_intent,
        "normalized_goal": goal.normalized_goal,
        "assumptions": goal.assumptions or [],
        "genre": work.genre,
        "chapter_no": run.chapter_no,
        "target_node": {
            "id": target.node_id,
            "title": target.title,
            "promise": target.promise,
            "preconditions": target.preconditions or [],
            "consequences": target.consequences or [],
        } if target else {},
        "canon": canon,
    }

    existing = list(
        (await session.scalars(select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id))).all()
    )
    if not existing:
        defaults = (
            ("主角", "推动目标但必须付出代价", "克制、具体、少解释"),
            ("同伴", "帮助主角，同时保护自己的秘密", "敏锐、留有余地"),
            ("对手", "阻止主角并证明自己的秩序正确", "冷静、带压迫感"),
        )
        for name, drive, voice in defaults:
            session.add(
                PersonaSpecModel(
                    id=uuid.uuid4(), work_id=work.id, name=name,
                    identity={"role": name}, drives={"primary": drive},
                    voice={"style": voice}, stable_traits=[drive, voice],
                )
            )
        await session.flush()


async def perform(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    personas = list(
        (await session.scalars(select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id))).all()
    )
    scene_id = f"chapter-{run.chapter_no}"
    canon = list(run.generation_context.get("canon", []))

    async def one(persona: PersonaSpecModel) -> dict[str, Any]:
        grants = [
            f for f in canon
            if not f.get("known_by") or persona.name in f.get("known_by", []) or "ALL" in f.get("known_by", [])
        ]
        excluded = [str(f.get("statement", "")) for f in canon if f not in grants]
        context_hash = hashlib.sha256(
            json.dumps(grants, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        info = await session.scalar(
            select(InformationSetModel).where(
                InformationSetModel.persona_id == persona.id,
                InformationSetModel.scene_id == scene_id,
                InformationSetModel.context_hash == context_hash,
            )
        )
        if info is None:
            session.add(
                InformationSetModel(
                    id=uuid.uuid4(), work_id=work.id, persona_id=persona.id,
                    scene_id=scene_id, grants=grants, exclusions=excluded,
                    context_hash=context_hash,
                )
            )
        response = await provider.generate_structured(
            system_prompt=(
                "你正在扮演一个小说人物。只能依据给出的角色设定与已知事实行动；"
                "绝不能使用或猜测未提供的信息。输出人物在本章的行动表演，不写完整章节。"
            ),
            user_prompt=json.dumps(
                {
                    "persona": persona.name, "identity": persona.identity,
                    "drives": persona.drives, "voice": persona.voice,
                    "known_facts": grants,
                    "scene_context": {k: v for k, v in run.generation_context.items() if k != "canon"},
                }, ensure_ascii=False,
            ),
            response_model=Performance,
        )
        return response.output.model_dump(mode="json")

    # Hive 的唯一触发处：信息隔离的角色表演可安全并行。
    run.performances = list(await asyncio.gather(*(one(persona) for persona in personas)))


async def direct(*, provider: ModelProvider, run: ChapterRunModel) -> None:
    response = await provider.generate_structured(
        system_prompt=(
            "你是小说导演。将角色的独立表演编排为因果清晰的场景计划。"
            "禁止让角色知道其信息集中没有出现的事实；不写正文。"
        ),
        user_prompt=json.dumps(
            {"context": run.generation_context, "performances": run.performances},
            ensure_ascii=False,
        ),
        response_model=DirectorPlan,
    )
    context = dict(run.generation_context)
    context["director_plan"] = response.output.model_dump(mode="json")
    run.generation_context = context


async def weave(*, provider: ModelProvider, run: ChapterRunModel) -> None:
    response = await provider.generate_structured(
        system_prompt=(
            "你是中文类型小说写作者。依据导演计划和角色表演写一章可直接阅读的正文。"
            "目标 1800–2600 个中文字符；用动作、对话和具体感官推动情节，避免总结式大纲、"
            "元叙事和设定堆砌；结尾必须兑现本章推进并留下自然悬念。"
        ),
        user_prompt=json.dumps(
            {"context": run.generation_context, "performances": run.performances},
            ensure_ascii=False,
        ),
        response_model=ChapterDraft,
    )
    run.title = response.output.title
    run.content = response.output.content.strip()
    run.word_count = len(run.content)


async def review(*, provider: ModelProvider, run: ChapterRunModel) -> None:
    response = await provider.generate_structured(
        system_prompt=(
            "你是严格的小说编辑。检查正文是否违反既有事实、泄露角色未知信息、缺少因果推进，"
            "或呈现为大纲而非正文。只有达到可读初稿标准才 passed=true。"
        ),
        user_prompt=json.dumps(
            {"context": run.generation_context, "performances": run.performances,
             "draft": {"title": run.title, "content": run.content}}, ensure_ascii=False,
        ),
        response_model=ChapterReview,
    )
    result = response.output
    # Agent loop 内的一次有证据修订；不是期待模型第一次就交付可用结果。
    if not result.passed:
        revised = await provider.generate_structured(
            system_prompt=(
                "你是中文类型小说改稿编辑。严格执行问题清单，重写为完整可读章节；"
                "保留正确情节，修复连续性、信息泄露、节奏和文风问题。"
            ),
            user_prompt=json.dumps(
                {"draft": run.content, "issues": result.model_dump(mode="json"),
                 "context": run.generation_context}, ensure_ascii=False,
            ),
            response_model=ChapterDraft,
        )
        run.title = revised.output.title
        run.content = revised.output.content.strip()
        run.word_count = len(run.content)
    run.review = result.model_dump(mode="json")


async def canon(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    source_hash = hashlib.sha256(run.content.encode("utf-8")).hexdigest()
    existing = await session.scalar(
        select(CanonCommitModel).where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
            CanonCommitModel.chapter_no == run.chapter_no,
            CanonCommitModel.source_hash == source_hash,
        )
    )
    if existing is not None:
        return
    response = await provider.generate_structured(
        system_prompt=(
            "从已完成章节提取后续必须保持一致的客观事实。每项使用 statement、"
            "entities、known_by、confidence 字段；只提取正文明确成立的事实，不推测。"
        ),
        user_prompt=run.content,
        response_model=CanonExtraction,
    )
    latest = await session.scalar(
        select(CanonCommitModel)
        .where(CanonCommitModel.work_id == work.id, CanonCommitModel.branch_id == work.branch_id)
        .order_by(CanonCommitModel.version.desc()).limit(1)
    )
    parent = int(latest.version) if latest else 0
    session.add(
        CanonCommitModel(
            id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
            chapter_no=run.chapter_no, parent_version=parent, version=parent + 1,
            facts=response.output.facts, source_hash=source_hash,
            validation_id=hashlib.sha256(json.dumps(run.review, sort_keys=True).encode()).hexdigest()[:64],
        )
    )


async def execute_step(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel,
    run: ChapterRunModel, step: ChapterStep,
) -> None:
    if step == ChapterStep.ASSEMBLE:
        await assemble(session, work=work, run=run)
    elif step == ChapterStep.PERFORM:
        await perform(session, provider=provider, work=work, run=run)
    elif step == ChapterStep.DIRECT:
        await direct(provider=provider, run=run)
    elif step == ChapterStep.WEAVE:
        await weave(provider=provider, run=run)
    elif step == ChapterStep.REVIEW:
        await review(provider=provider, run=run)
    elif step == ChapterStep.CANON:
        await canon(session, provider=provider, work=work, run=run)
