"""Offline production-context audit; no provider calls."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core' / 'src'))
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from regent.novel.application import works, generation
from regent.novel.infrastructure.models import NovelBase, NovelPrincipalModel, StoryWorkModel, StoryGoalModel, ChapterRunModel

@compiles(JSONB, 'sqlite')
def json_sqlite(*args, **kwargs):
    return 'JSON'

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(NovelBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner = uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject='v77-isolated'))
        work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state='RUNNING', latest_chapter_no=2)
        session.add(work)
        await session.flush()
        session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work.id, raw_intent='钥匙归还的故事'))
        for chapter in (1, 2):
            session.add(ChapterRunModel(id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
                chapter_no=chapter, attempt=1, state='CANONIZED'))
        await session.flush()
        with patch.object(works, 'append_event', AsyncMock()):
            for chapter in (1, 2):
                await works._queue_replay_run(session, work=work, chapter_no=chapter,
                    correction={'ticket_id': 'ticket', 'statement': '甲从未拿到钥匙', 'subject': '甲'})
        run = await session.scalar(select(ChapterRunModel).where(ChapterRunModel.attempt == 2, ChapterRunModel.chapter_no == 1))
        before = dict(run.generation_context)
        await generation.assemble(session, work=work, run=run)
        after = dict(run.generation_context)
        second = await session.scalar(select(ChapterRunModel).where(ChapterRunModel.attempt == 2, ChapterRunModel.chapter_no == 2))
        # First chapter has begun but has not been accepted; a later queued chapter is older.
        second.updated_at = datetime.now(UTC) - timedelta(minutes=1)
        run.state = 'RUNNING'
        run.updated_at = datetime.now(UTC)
        await session.flush()
        picked = {}
        async def capture(*args, **kwargs):
            picked['chapter_no'] = kwargs['chapter_no']
        with patch.object(works, 'advance_step', capture):
            await works.advance_background_run(session, provider=None)
        print(json.dumps({'before_assemble': before,
            'correction_survives_assemble': 'correction' in after,
            'replay_reason_survives_assemble': 'replay_reason' in after,
            'first_replay_state': run.state, 'next_worker_pick': picked}, ensure_ascii=False, indent=2))
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
