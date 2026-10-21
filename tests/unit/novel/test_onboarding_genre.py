"""开书澄清：题材答案必须落到 works.genre。

改进库按 genre_tag 检索同类型经验，works.genre 空着就等于这本书永远看不到
别人攒下的经验。真机 197 本书里 170 本 genre 为空，就是因为澄清答案只写进了
goal.assumptions，从没落到作品上。
"""

# ruff: noqa: RUF001

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import generation
from regent.novel.application import works_onboarding as ob
from regent.novel.application.works_constants import (
    GENRE_MAX_CHARS,
    GENRE_TAGS,
    GENRE_UNRESOLVED,
    MAX_QUESTIONS_PER_ROUND,
)
from regent.novel.infrastructure.models import (
    NovelPrincipalModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select


class _Provider:
    model_name = "fake"


async def _seed_owner(session):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
    await session.flush()
    return owner


@pytest.mark.asyncio
async def test_clarify_genre_answer_is_persisted_on_work(novel_db):
    async with novel_db() as s:
        owner = await _seed_owner(s)
        work, out = await ob.create_work(
            s, owner_id=owner, title="雨夜来客", raw_intent="他在雨夜里收到一封信"
        )
        await s.commit()
        assert work.genre == ""
        assert out.status == "CLARIFYING"
        work_id = work.id

    async with novel_db() as s:
        await ob.answer_clarify(s, owner_id=owner, work_id=work_id, answers={"genre": "悬疑"})
        await s.commit()
        row = await s.scalar(select(StoryWorkModel))
        assert row.genre == "悬疑"
        goal = await s.scalar(select(StoryGoalModel))
        assert any("悬疑" in str(line) for line in (goal.assumptions or []))


@pytest.mark.asyncio
async def test_long_genre_is_truncated_to_column_width(novel_db):
    """列宽是 varchar(64)：SQLite 不校验长度，Postgres 会直接报错。"""
    async with novel_db() as s:
        owner = await _seed_owner(s)
        work, _ = await ob.create_work(
            s, owner_id=owner, title="超长题材", raw_intent="前提", genre="玄" * 200
        )
        await s.commit()
        assert len(work.genre) == GENRE_MAX_CHARS


@pytest.mark.asyncio
async def test_llm_questions_do_not_drop_the_genre_question(novel_db, monkeypatch):
    """LLM 动态题整组替换写死模板，真机 170/197 本书因此从没被问题材。"""

    async def _dynamic(provider, *, raw_intent, genre):
        return [
            {
                "question_id": f"q{i}",
                "prompt": f"前提里的第 {i} 个疑点？",
                "options": ["甲", "乙"],
                "default_assumption": "甲",
            }
            for i in range(1, 4)
        ]

    monkeypatch.setattr(generation, "generate_clarify_questions", _dynamic)
    async with novel_db() as s:
        owner = await _seed_owner(s)
        _work, out = await ob.create_work(
            s,
            owner_id=owner,
            title="倒走的表",
            raw_intent="修表匠听见了旧物记忆",
            provider=_Provider(),
        )
        await s.commit()
    ids = [q.question_id for q in out.questions]
    assert len(ids) <= MAX_QUESTIONS_PER_ROUND
    assert "genre" in ids
    genre_q = next(q for q in out.questions if q.question_id == "genre")
    assert genre_q.options == list(GENRE_TAGS)


@pytest.mark.asyncio
async def test_unanswered_genre_leaves_work_genre_empty(novel_db):
    """未回答按默认收口时不得把占位值写成标签：宁可空着，不可污染 genre_tag。"""
    async with novel_db() as s:
        owner = await _seed_owner(s)
        work, _out = await ob.create_work(
            s, owner_id=owner, title="雨夜来客", raw_intent="他在雨夜里收到一封信"
        )
        await s.commit()
        work_id = work.id

    async with novel_db() as s:
        await ob.answer_clarify(
            s, owner_id=owner, work_id=work_id, answers={"genre": GENRE_UNRESOLVED}
        )
        await s.commit()
        assert (await s.scalar(select(StoryWorkModel))).genre == ""
