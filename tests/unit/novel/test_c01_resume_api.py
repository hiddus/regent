"""C-01 恢复入口的**生产入口级**验证：走真实 HTTP 路由与服务端身份解析。

为什么要单独一个文件：应用层函数测过，不等于入口接好了。上一轮 B-05 的教训
是「库里有、生产入口没有」——判定逻辑成立但没有任何请求能到达它。这里直接
打 FastAPI 路由，覆盖依赖装配（``app.state.sessions``）、鉴权（G-11：服务端
解析 token，不接受客户端 actor）与错误 envelope 映射。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from regent.novel.api import novel as novel_api
from regent.novel.application.principal import hash_token, issue_token
from regent.novel.domain.states import ChapterRunState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    NovelPrincipalModel,
    NovelSessionModel,
    StoryWorkModel,
)
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _app(sessions) -> FastAPI:
    app = FastAPI()
    app.include_router(novel_api.router)
    novel_api.register_exception_handlers(app)
    app.state.sessions = sessions
    return app


async def _seed(sessions, *, state: str, queued_replay: bool):
    """建身份 + 会话 token + 作品 +（可选的）排队中的重演任务。"""
    raw = issue_token()
    async with sessions() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"http:{owner}"))
        s.add(
            NovelSessionModel(
                id=uuid.uuid4(),
                token_hash=hash_token(raw),
                principal_id=owner,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        work = StoryWorkModel(
            id=uuid.uuid4(),
            owner_id=owner,
            state=state,
            genre="悬疑",
            latest_chapter_no=1,
        )
        s.add(work)
        await s.flush()
        s.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=work.branch_id,
                chapter_no=1,
                attempt=1,
                state=ChapterRunState.CANONIZED.value,
            )
        )
        if queued_replay:
            s.add(
                ChapterRunModel(
                    id=uuid.uuid4(),
                    work_id=work.id,
                    branch_id=work.branch_id,
                    chapter_no=1,
                    attempt=2,
                    state=ChapterRunState.QUEUED.value,
                )
            )
        await s.commit()
        return raw, work.id


async def _state_of(sessions, work_id) -> str:
    async with sessions() as s:
        return str(
            await s.scalar(
                select(StoryWorkModel.state).where(StoryWorkModel.id == work_id)
            )
        )


async def test_resume_endpoint_runs_the_queued_replay(novel_db):
    """完结作品经 HTTP 恢复后，作品真的回到 RUNNING——任务才会被后台领取（C-01）。"""
    raw, work_id = await _seed(novel_db, state="DONE", queued_replay=True)
    transport = ASGITransport(app=_app(novel_db))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/v1/novel/works/{work_id}/resume-correction",
            json={"ticket_id": "T-1"},
            headers={"X-Novel-Token": raw},
        )

    assert response.status_code == 200, response.text
    assert await _state_of(novel_db, work_id) == "RUNNING"


async def test_resume_endpoint_rejects_work_without_queued_replay(novel_db):
    """没有排队任务时入口必须 409，不能把作品空放回 RUNNING（C-01）。"""
    raw, work_id = await _seed(novel_db, state="PAUSED_QUOTA", queued_replay=False)
    transport = ASGITransport(app=_app(novel_db))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/v1/novel/works/{work_id}/resume-correction",
            json={},
            headers={"X-Novel-Token": raw},
        )

    assert response.status_code == 409, response.text
    assert await _state_of(novel_db, work_id) == "PAUSED_QUOTA"


async def test_resume_endpoint_requires_server_issued_token(novel_db):
    """G-11：没有服务端签发的 token 不能恢复别人的作品。"""
    _raw, work_id = await _seed(novel_db, state="DONE", queued_replay=True)
    transport = ASGITransport(app=_app(novel_db))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/v1/novel/works/{work_id}/resume-correction",
            json={},
            headers={"X-Novel-Token": "forged-token"},
        )

    assert response.status_code == 401, response.text
    assert await _state_of(novel_db, work_id) == "DONE"
