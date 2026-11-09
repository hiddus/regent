"""作品级质量终止的落地点：取正文、发独立复核、写作品状态。

与章内 CHECK 的分工：
- 章内只管这一章能不能进正史；
- 这里只管这本书还值不值得往下写。它只在成章之后跑，跑不通过就不创建下一章。

三条边界（Plan R1）：

1. **必须用正文，不能用章纲。** 追读自评看的是最近几章的章纲；章纲写得没问题而
   正文读不下去，是长篇最常见的失败形态。复核输入是已入正史章节的正文原文。
2. **必须独立复核。** 自评与复核是两次调用、两套提示：自评提「可能不追」，复核
   拿正文逐句核对。复核给出的每条证据都要能在对应章节正文里逐字找到。
3. **判断不可用不等于判断为真。** 调用失败、预算耗尽一律记待核验：不停止、不标
   完结，下一轮追读自评再判一次。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.events import append_event
from regent.novel.domain import quality_stop
from regent.novel.domain.states import StoryWorkState, assert_story_work_transition
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel

# 复核窗口：最近几章正文。6 章约 1.5 万字，再长就只是多花钱。
QUALITY_WINDOW = 4
# 单章进复核的最大字数：从章末往前取。开头的铺垫在「这几章还追不追」里信息量最低。
QUALITY_PROSE_LIMIT = 2600
# 复核调用的独立预算（分）。它不占本章的调用次数与金额上限：章已经写完了，
# 不能因为本章预算刚好用尽就让终止判定永远「不可用」。
QUALITY_CALL_BUDGET_MINOR = 2_000

QUALITY_REVIEW_SYSTEM = (
    "你是这部书的终审读者，只判断一件事：读完这几章正文，还会不会往下追。\n"
    "给你的是已经发表的正文原文，不是章纲，不是简介。判断只看正文里实际写了什么。\n"
    "判 stop=true 只有三种情况，且每种都必须在正文里指出具体位置：\n"
    "- 复读拖延：同样的动作、同样的对话、同样的解释反复出现，几章下来局面没有变化；\n"
    "- 无新变化：主角反复处理同一件事，没有新的选择、新的代价、新的信息；\n"
    "- 承诺不兑现：作品一直答应读者的那件事，这几章既没有发生也没有推进。\n"
    "以下不算：节奏慢但有明确选择与后果；合法的回忆、转述、引用旧文；同一地点内的推进；"
    "为了后文铺垫而暂时压住的信息。这些情况 stop=false。\n"
    "evidence 每条写 chapter_no、quote、problem：quote 必须是从那一章正文里原样复制的连续句子，"
    "不得改写、不得跨段拼接、不得写概括；problem 说明这句为什么不追。最多三条。\n"
    "reason 用一句话说明读者为什么放下这本书；uncertainty 写下你不确定的地方，没有就留空。\n"
    "拿不准时 stop=false：误停一本书比多看几章代价大。"
)


class QualityQuoteOut(BaseModel):
    chapter_no: int = Field(description="这条证据出自第几章")
    quote: str = Field(min_length=6, description="该章正文里的连续原文，原样复制")
    problem: str = Field(default="", description="这句为什么不追")


class QualityStopReview(BaseModel):
    stop: bool = Field(description="读完这几章正文，读者是否已经不会往下追")
    reason: str = Field(default="", description="读者为什么放下这本书，一句话")
    evidence: list[QualityQuoteOut] = Field(default_factory=list, max_length=3)
    uncertainty: str = Field(default="", description="不确定的地方；没有留空")


def _clip_tail(text: str, limit: int) -> str:
    body = str(text or "")
    return body if len(body) <= limit else body[-limit:]


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


async def recent_prose(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    upto_chapter: int,
    window: int = QUALITY_WINDOW,
) -> list[tuple[int, str]]:
    """最近 ``window`` 章已入正史的正文，按章号升序。

    只看 CANONIZED：草稿、被替换的版本、失败重试的中间稿都不算「读者读到的书」。
    """
    rows = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.work_id == work.id,
                    ChapterRunModel.branch_id == work.branch_id,
                    ChapterRunModel.state == "CANONIZED",
                    ChapterRunModel.chapter_no <= int(upto_chapter),
                )
                .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
            )
        ).all()
    )
    seen: dict[int, str] = {}
    for row in rows:
        no = int(row.chapter_no)
        if no in seen or not str(row.content or "").strip():
            continue
        seen[no] = str(row.content)
        if len(seen) >= int(window):
            break
    return sorted(seen.items())


def review_payload(
    *,
    review: dict[str, Any],
    chapters: list[tuple[int, str]],
    reader_contract: dict[str, Any] | None = None,
    dramatic_engine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "reader_contract": reader_contract or {},
        "dramatic_engine": dramatic_engine or {},
        "plan_level_review": {
            "keeps_reading": review.get("keeps_reading"),
            "problems": [
                str(item.get("problem") or "")
                for item in review.get("problems") or []
                if isinstance(item, dict)
            ],
        },
        "chapters": [
            {"chapter_no": no, "prose": _clip_tail(text, QUALITY_PROSE_LIMIT)}
            for no, text in chapters
        ],
    }


async def _call_review(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    payload: dict[str, Any],
    window: list[int],
) -> QualityStopReview:
    from regent.novel.application.production import CallBroker

    command_id = (
        f"quality_stop:{work.id}:{int(run.chapter_no)}:"
        f"{_digest(json.dumps(window))[:8]}:{_digest(json.dumps(payload, ensure_ascii=False))[:8]}"
    )
    broker = CallBroker(
        lease_owner=f"work:{work.id}:quality_stop",
        budget_limit_minor=QUALITY_CALL_BUDGET_MINOR,
    )
    result = await broker.run(
        session,
        provider=provider,
        schema=QualityStopReview,
        work_id=work.id,
        run_id=run.id,
        chapter_no=int(run.chapter_no),
        step="CANON",
        purpose="quality_stop_review",
        command_id=command_id,
        system_prompt=QUALITY_REVIEW_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        temperature=0,
        model_hint=getattr(provider, "model_name", "") or "",
    )
    return result.output  # type: ignore[no-any-return]


def _record(
    work: StoryWorkModel,
    decision: quality_stop.QualityStopDecision,
    *,
    attempts: int,
) -> None:
    bible = dict(work.story_bible or {})
    payload = decision.as_payload()
    payload |= {
        "decided_at": datetime.now(UTC).isoformat(),
        "attempts": int(attempts),
    }
    previous = payload.get("prose_hashes") or {}
    if previous:
        payload["prose_hashes"] = previous
    bible["quality_stop"] = payload
    work.story_bible = bible


async def maybe_stop_for_quality(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None,
) -> bool:
    """成章之后判一次「还追不追」。返回 True 表示本书已停止续写。

    只在章纲自评判了「不追」时才发起复核，因此正常作品每 3 章最多多一次调用，
    而且只在真的出现退化信号时才会发生。
    """
    if quality_stop.work_quality_stopped(work):
        return True
    production = (run.generation_context or {}).get("production") or {}
    review = production.get("arc_review") if isinstance(production, dict) else None
    if not isinstance(review, dict) or review.get("keeps_reading") is not False:
        return False

    chapters = await recent_prose(session, work=work, upto_chapter=int(run.chapter_no))
    bodies = {no: text for no, text in chapters}
    hashes = {no: _digest(text) for no, text in chapters}
    attempt = int(quality_stop.quality_stop_record(work).get("attempts") or 0) + 1
    decision: quality_stop.QualityStopDecision
    if not chapters or provider is None:
        decision = quality_stop.decide_quality_stop(
            keeps_reading=False,
            chapters=bodies,
            prose_hashes=hashes,
            unavailable_reason=(
                "没有可复核的已入正史正文" if not chapters else "复核模型不可用"
            ),
        )
    else:
        context = run.generation_context or {}
        contract = context.get("reader_contract")
        engine = context.get("dramatic_engine")
        payload = review_payload(
            review=review,
            chapters=chapters,
            reader_contract=contract if isinstance(contract, dict) else None,
            dramatic_engine=engine if isinstance(engine, dict) else None,
        )
        try:
            verdict = await _call_review(
                session,
                provider=provider,
                work=work,
                run=run,
                payload=payload,
                window=[no for no, _ in chapters],
            )
        except Exception as exc:  # 调用结果不可知：待核验，不冒充结论
            decision = quality_stop.decide_quality_stop(
                keeps_reading=False,
                chapters=bodies,
                prose_hashes=hashes,
                unavailable_reason=(
                    f"正文复核调用未完成（{type(exc).__name__}: {str(exc)[:120]}）"
                ),
            )
        else:
            quotes = [
                quality_stop.QualityQuote(
                    chapter_no=int(item.chapter_no),
                    quote=str(item.quote or ""),
                    problem=str(item.problem or ""),
                    prose_hash=hashes.get(int(item.chapter_no), ""),
                )
                for item in verdict.evidence
            ]
            decision = quality_stop.decide_quality_stop(
                keeps_reading=False,
                chapters=bodies,
                prose_hashes=hashes,
                model_stop=bool(verdict.stop),
                model_reason=str(verdict.reason or ""),
                model_uncertainty=str(verdict.uncertainty or ""),
                quotes=quotes,
            )

    _record(work, decision, attempts=attempt)
    if not decision.stop:
        await append_event(
            session,
            work_id=work.id,
            event_type="work.quality_unverified",
            data={
                "chapter_no": int(run.chapter_no),
                "status": decision.status,
                "reason": decision.reason,
                "rejected": list(decision.rejected),
                "attempts": attempt,
            },
            branch_id=work.branch_id,
            chapter_no=int(run.chapter_no),
        )
        return False

    if work.state != StoryWorkState.STOPPED_QUALITY.value:
        assert_story_work_transition(work.state, StoryWorkState.STOPPED_QUALITY.value)
        work.state = StoryWorkState.STOPPED_QUALITY.value
        work.version += 1
    # 停止也落在运行上：start_run 读的是最近一次运行的上下文，只写作品会留下
    # 「作品停了但上一章仍说自己在跑」的缝。
    context = dict(run.generation_context or {})
    context["quality_stop"] = decision.as_payload()
    run.generation_context = context
    run.version += 1
    session.add(run)
    await session.flush()
    await append_event(
        session,
        work_id=work.id,
        event_type="work.quality_stopped",
        data={
            "chapter_no": int(run.chapter_no),
            "status": decision.status,
            "basis": decision.basis,
            "reason": decision.reason,
            "uncertainty": decision.uncertainty,
            "window": list(decision.window),
            "evidence": [item.as_payload() for item in decision.evidence],
            "rejected": list(decision.rejected),
        },
        branch_id=work.branch_id,
        chapter_no=int(run.chapter_no),
    )
    return True


__all__ = [
    "QUALITY_REVIEW_SYSTEM",
    "QUALITY_WINDOW",
    "QualityStopReview",
    "maybe_stop_for_quality",
    "recent_prose",
    "review_payload",
]
