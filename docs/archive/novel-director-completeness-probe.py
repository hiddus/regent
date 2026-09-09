"""Offline audit of current director integration; no model/network calls."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core' / 'src'))
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from regent.novel.application import works, memory
from regent.novel.application.direction import VerifiedFact
from regent.novel.domain import memory as dm
from regent.novel.domain.models import ReportFactRequest
from regent.novel.infrastructure.models import (
    NovelBase, NovelPrincipalModel, StoryWorkModel, ChapterRunModel, VolumeModel,
)

@compiles(JSONB, 'sqlite')
def json_sqlite(*args, **kwargs):
    return 'JSON'

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(NovelBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    result = {}
    async with sessions() as session:
        owner = uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject='isolated-completeness-audit'))
        work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state='DONE', latest_chapter_no=1)
        session.add(work)
        await session.flush()
        session.add(ChapterRunModel(id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
                                    chapter_no=1, attempt=1, state='CANONIZED'))
        await session.flush()
        correction = '甲其实从未持有钥匙'
        with patch.object(works, 'append_event', AsyncMock()):
            response = await works.report_fact(session, owner_id=owner, work_id=work.id,
                payload=ReportFactRequest(statement=correction, chapter_no=1, subject='甲'))
        replay = await session.scalar(select(ChapterRunModel).where(ChapterRunModel.work_id == work.id,
                                                                  ChapterRunModel.attempt == 2))
        result['replay'] = dict(accepted=response.accepted, work_state=work.state,
                               run_state=replay.state, context=replay.generation_context,
                               correction_in_context=correction in json.dumps(replay.generation_context, ensure_ascii=False),
                               background_result=await works.advance_background_run(session, provider=None))
        work.state = 'RUNNING'
        work.latest_chapter_no = 8
        work.ending_target_volume = 1
        session.add(VolumeModel(id=uuid.uuid4(), work_id=work.id, volume_no=1, title='唯一一卷',
                                start_chapter_no=1, end_chapter_no=10, state='ACTIVE'))
        await session.flush()
        with patch.object(works, 'expand_next_volume', AsyncMock()) as expand:
            await works._maybe_expand_volume(session, work=work)
            result['early_expansion'] = dict(user_target_volumes=1, current_volume=1,
                                            progress='8/10', expansion_called=expand.await_count)
        first = VerifiedFact(statement='甲承诺归还钥匙', quote='甲承诺归还钥匙', known_by=['甲']).model_dump()
        second = VerifiedFact(statement='甲把钥匙还给乙，兑现承诺', quote='甲把钥匙还给乙，兑现承诺', known_by=['甲']).model_dump()
        items = dm.extract_items([first], chapter_no=1, cast=['甲'])
        result['formal_payoff'] = [i.state for i in dm.resolve_items(items, [second], chapter_no=2)]
    await engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
