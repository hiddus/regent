import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core' / 'src'))
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from regent.novel.infrastructure.models import NovelBase, NovelPrincipalModel, StoryWorkModel, CostEntryModel, QuotaReservationModel
from regent.novel.application.production import CallBroker
from regent.novel.application.direction import VerifiedFact
from regent.novel.domain import memory, evaluation as e
from regent.novel.application import evaluation as app

@compiles(JSONB, 'sqlite')
def json_sqlite(*args, **kwargs):
    return 'JSON'

async def main():
    result = {}
    fact = VerifiedFact(statement='铜钥匙只能使用一次', quote='铜钥匙只能使用一次', known_by=['主角'], entities=['铜钥匙']).model_dump()
    result['memory_normal_fact_count'] = len(memory.extract_items([fact], chapter_no=1))
    result['memory_tagged_statement_count'] = len(memory.extract_items([{**fact, 'memory_kind':'rule', 'subject':'铜钥匙'}], chapter_no=1))
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(NovelBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner, wid = uuid.uuid4(), uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject='audit-isolated'))
        session.add(StoryWorkModel(id=wid, owner_id=owner))
        await session.flush()
        broker = CallBroker(lease_owner='audit')
        for attempt, amount in [(1,3),(2,4)]:
            await broker._top_up(session, call=SimpleNamespace(logical_call_id='same-call', attempt=attempt, work_id=wid, chapter_no=1, step='PRODUCE'), amount=amount)
        result['topup_expected_minor'] = 7
        result['topup_consumed_minor'] = await session.scalar(select(func.sum(CostEntryModel.amount_minor)).where(CostEntryModel.entry_kind=='CONSUME'))
        rows=(await session.scalars(select(QuotaReservationModel))).all()
        result['topup_reservations']=[{'key':r.reservation_key,'amount':r.amount_minor,'settled':r.settled_minor,'status':r.status} for r in rows]
        for drift in (False, True):
            eid='audit-drift' if drift else 'audit-count'
            config=e.EvalConfig(eval_id=eid, sample=e.SampleSpec(total=24), thresholds=e.PromotionThresholds(min_samples=10,min_read_willingness=5 if drift else 4,min_character_credibility=4,min_emotion_and_payoff=4,max_fact_error=.1,max_cost_minor_per_chapter=100))
            row=await app.freeze_eval(session, config=config)
            if drift:
                row.config={**row.config,'thresholds':{**row.config['thresholds'],'min_read_willingness':4}}
                await session.flush()
            scores=[e.BlindScore(sample_id='only-one-sample',arm=arm,rater=f'r{i}',scores={'read_willingness':4.5 if arm=='v2' else 3,'character_credibility':4.5,'emotion_and_payoff':4.5,'fact_error':0}) for arm in ('v1','v2') for i in range(10)]
            await app.record_scores(session,eval_id=eid,scores=scores)
            report=await app.build_report(session,eval_id=eid)
            verdict,reasons=await app.decide(session,eval_id=eid)
            result[eid]={'registered_samples':report['samples'],'distinct_scored_samples':1,'n':report['arms'][0]['n'],'verdict':verdict,'config_was_changed':drift,'cost_supplied':False}
    await engine.dispose()
    print(json.dumps(result,ensure_ascii=False,indent=2))

asyncio.run(main())
