"""本书覆盖层：跨章延续、试用、采纳或回退。

一章递归交付的稿子过了硬门，这一轮的配置修改叠到本书覆盖层上，进入试用
（provisional，一本书同时只有一条）。之后用这层配置写的章节攒够
``TRIAL_CHAPTERS`` 章就判定：一次过率不低于试用前同样章数的基线就采纳，
否则回退到上一层。没有基线时，三章里至少两章一次过才采纳。试用前追读自评
判「会追」的，试用中只要有一次判「不追」也回退。试用章首稿字数中位数超长、
且比试用前长出两成以上的，也回退。

新章节运行创建时调用 ``pin_harness``：先结算到期的试用，再把当前覆盖层
钉进运行上下文。钉住之后这一章不再跟着库变。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain.states import ChapterRunState
from regent.novel.harnessing import library
from regent.novel.harnessing.surface import (
    BASE_HARNESS_ID,
    apply_overrides,
    book_terms,
    load_harness,
    mentions_names,
    merge_overrides,
    upgrade_prompts,
)
from regent.novel.infrastructure.models import ChapterRunModel, HarnessLibraryModel

TRIAL_CHAPTERS = 3
# 试用章首稿比试用前长出这么多倍（且超长）就回退：加戏的覆盖层先让初稿变长，
# 删段、截断、首过失败都跟在后面。
TRIAL_DRAFT_GROWTH = 1.2


def _first_pass(run: ChapterRunModel) -> bool | None:
    """这一章首稿是否一次过硬门；认不出来就返回 None（不计入基线）。

    两臂都在 CHECK 里按同一口径记 ``first_pass``：只看硬门，不看软警告——软警告
    会把这一章送进改稿，但改过稿不等于首稿没过门。``h2_first_pass`` 是 H2 臂早先
    写下的同义键，历史运行里还留着。

    两个键都没有的老运行一律返回 None，**不再按「没改过稿也没重写过」反推**。
    反推只能得出 True：真没过门的章必然改过稿或重写过，会被排除；改过稿但首稿
    其实过了门的章也被排除。留下的是清一色干净章，基线因此恒为 100%，
    ``trial_passes`` 就要求试用章也 100% 一次过——护栏被推向「必然回退」，
    和它本该防的方向正好相反。老章节靠新运行写满三章自愈，这期间按保守规则判。
    """
    production = (run.generation_context or {}).get("production") or {}
    for key in ("first_pass", "h2_first_pass"):
        if key in production:
            return bool(production[key])
    return None


async def _latest_runs(
    session: AsyncSession,
    work_id: uuid.UUID,
    *,
    low: int,
    high: int | None = None,
    overlay_id: str | None = None,
) -> list[tuple[ChapterRunModel, bool]]:
    """每个章号取最新一次尝试，连同这一章有没有哪次尝试终止失败过，按章号升序。

    只看最新一次会让「重写一遍」把终止失败洗掉：覆盖层把章写崩了，用户点重写、
    第二次过了，试用就只记得那次成功。给了 ``overlay_id`` 时只看钉着它的尝试。
    """
    stmt = select(ChapterRunModel).where(
        ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no >= low
    )
    if high is not None:
        stmt = stmt.where(ChapterRunModel.chapter_no <= high)
    rows = (
        await session.scalars(
            stmt.order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt.desc())
        )
    ).all()
    latest: dict[int, ChapterRunModel] = {}
    failed: set[int] = set()
    for row in rows:
        if overlay_id is not None and _overlay_of(row) != overlay_id:
            continue
        no = int(row.chapter_no)
        latest.setdefault(no, row)
        if row.state == ChapterRunState.TERMINAL_FAILED.value:
            failed.add(no)
    return [(latest[no], no in failed) for no in sorted(latest)]


def _keeps_reading(run: ChapterRunModel) -> bool | None:
    """这一章动笔前做的追读自评；这一章没做就是 None。"""
    review = ((run.generation_context or {}).get("production") or {}).get("arc_review")
    if isinstance(review, dict) and "keeps_reading" in review:
        return bool(review["keeps_reading"])
    return None


def _overlay_of(run: ChapterRunModel) -> str:
    return str((run.generation_context or {}).get("harness_overlay_id") or "")


def _draft_chars(run: ChapterRunModel) -> int | None:
    """这一章首稿的字数；没记 ``draft_chars`` 的老运行从首次删段或入库正文推。"""
    production = (run.generation_context or {}).get("production") or {}
    if production.get("draft_chars"):
        return int(production["draft_chars"])
    for item in production.get("fit_results") or []:
        if str(item.get("tag") or "").startswith("write:"):
            return int(item.get("chars_before") or 0) or None
    if run.state == ChapterRunState.CANONIZED.value and run.content:
        return len(run.content)
    return None


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2


async def first_pass_baseline(
    session: AsyncSession, work_id: uuid.UUID, *, before_chapter: int
) -> dict[str, int]:
    """试用前最近几章的一次过率与首稿字数中位数；认不出首过信号的章（别的执行臂）不算。"""
    low = max(1, before_chapter - TRIAL_CHAPTERS)
    runs = await _latest_runs(session, work_id, low=low, high=before_chapter - 1)
    counted = [
        (run, failed)
        for run, failed in runs
        if run.state == ChapterRunState.CANONIZED.value and _first_pass(run) is not None
    ]
    seen = [bool(_first_pass(run)) and not failed for run, failed in counted]
    drafts = [n for n in (_draft_chars(run) for run, _ in counted) if n]
    return {
        "chapters": len(seen),
        "ok": sum(1 for item in seen if item),
        "draft_median": _median(drafts),
    }


async def trial_outcome(session: AsyncSession, entry: HarnessLibraryModel) -> dict[str, int]:
    """钉着这条覆盖层写完（入库或终止失败）的前几章；终止失败过的章不算一次过。

    ``lost`` 数的是试用章动笔前判「不追了」的追读自评。自评看的是这一章之前的几章，
    试用首章那一次看的全是试用前的章，不算。
    """
    if entry.source_work_id is None:
        return {"chapters": 0, "ok": 0, "lost": 0, "draft_median": 0}
    since = int(entry.since_chapter or 1)
    runs = await _latest_runs(session, entry.source_work_id, low=since, overlay_id=str(entry.id))
    chapters = ok = lost = 0
    drafts: list[int] = []
    for run, failed in runs:
        if run.state == ChapterRunState.CANONIZED.value:
            chapters += 1
            ok += 1 if _first_pass(run) and not failed else 0
        elif failed:
            chapters += 1
        if run.state == ChapterRunState.CANONIZED.value or failed:
            drafts += [n for n in (_draft_chars(run),) if n]
        if int(run.chapter_no) > since and _keeps_reading(run) is False:
            lost += 1
        if chapters >= TRIAL_CHAPTERS:
            break
    return {"chapters": chapters, "ok": ok, "lost": lost, "draft_median": _median(drafts)}


def drafts_grew(outcome: dict[str, int], baseline: dict[str, Any]) -> bool:
    """试用章首稿中位数超长，且比试用前长出 ``TRIAL_DRAFT_GROWTH`` 倍以上。"""
    from regent.novel.application.directing_chapter import MAX_CHAPTER_CHARS

    trial = int(outcome.get("draft_median") or 0)
    before = int(baseline.get("draft_median") or 0)
    return bool(before) and trial > MAX_CHAPTER_CHARS and trial > before * TRIAL_DRAFT_GROWTH


def trial_passes(outcome: dict[str, int], baseline: dict[str, Any]) -> bool:
    """一次过率不低于基线；试用前追读自评判「会追」的，试用中不能转成「不追」；
    首稿不能明显变长。"""
    if baseline.get("keeps_reading") is True and int(outcome.get("lost") or 0) > 0:
        return False
    if drafts_grew(outcome, baseline):
        return False
    return pass_rate_holds(outcome, baseline)


def pass_rate_holds(outcome: dict[str, int], baseline: dict[str, Any]) -> bool:
    """一次过率不低于基线；没有满窗基线时三章至少两章一次过。"""
    chapters = int(outcome.get("chapters") or 0)
    ok = int(outcome.get("ok") or 0)
    base_n = int(baseline.get("chapters") or 0)
    if base_n >= TRIAL_CHAPTERS:
        return ok * base_n >= int(baseline.get("ok") or 0) * chapters
    return ok * 3 >= chapters * 2


async def _work_entries(
    session: AsyncSession, work_id: uuid.UUID, status: str
) -> list[HarnessLibraryModel]:
    rows = (
        await session.scalars(
            select(HarnessLibraryModel).where(
                HarnessLibraryModel.source_work_id == work_id,
                HarnessLibraryModel.status == status,
            )
        )
    ).all()
    return [row for row in rows if row.overlay_after is not None]


async def settle_trial(session: AsyncSession, work_id: uuid.UUID) -> HarnessLibraryModel | None:
    """结算到期的试用；返回这本书当前生效的覆盖层条目。"""
    for entry in await _work_entries(session, work_id, "provisional"):
        outcome = await trial_outcome(session, entry)
        if outcome["chapters"] < TRIAL_CHAPTERS:
            return entry
        if "draft_median" not in (entry.baseline or {}):
            # 早先入库的条目基线没记首稿字数，按入库时同一窗口补算
            before = await first_pass_baseline(
                session, work_id, before_chapter=int(entry.since_chapter or 1) - 1
            )
            entry.baseline = {**(entry.baseline or {}), "draft_median": before["draft_median"]}
        if trial_passes(outcome, entry.baseline or {}):
            for older in await _work_entries(session, work_id, "adopted"):
                older.status = "superseded"
            entry.status = "adopted"
            entry.decided_at = datetime.now(UTC)
            entry.baseline = {**(entry.baseline or {}), "trial": outcome}
            await library.mark_adopted(session, entry.id)
        else:
            base = entry.baseline or {}
            reasons = []
            if base.get("keeps_reading") is True and outcome.get("lost"):
                reasons.append(f"试用前追读自评判会追，试用中 {outcome['lost']} 次判不追")
            if drafts_grew(outcome, base):
                reasons.append(
                    f"试用首稿中位 {outcome['draft_median']} 字，试用前 {base['draft_median']} 字"
                )
            if not pass_rate_holds(outcome, base):
                reasons.append(
                    f"试用 {outcome['chapters']} 章一次过 {outcome['ok']} 章，"
                    f"低于基线 {base.get('ok', 0)}/{base.get('chapters', 0)}"
                )
            reason = "；".join(reasons)
            await library.record_rollback(session, entry.id, reason=reason)
            entry.baseline = {**base, "trial": outcome}
    adopted = await _work_entries(session, work_id, "adopted")
    if not adopted:
        return None
    return max(adopted, key=lambda row: row.decided_at or row.created_at)


async def pin_harness(
    session: AsyncSession, *, work_id: uuid.UUID, context: dict[str, Any]
) -> None:
    """新运行创建时钉住基础包与本书覆盖层。"""
    entry = await settle_trial(session, work_id)
    # 早期条目整段替换过执笔提示；新运行一律转成追加，基础包升级才到得了这本书。
    overlay = upgrade_prompts(entry.overlay_after or {}) if entry is not None else {}
    config = apply_overrides(load_harness(BASE_HARNESS_ID), overlay)
    context["harness_id"] = BASE_HARNESS_ID
    context["harness_hash"] = config.content_hash
    context["harness_overlay"] = overlay
    context["harness_overlay_id"] = str(entry.id) if entry is not None else ""


async def record_verified_round(
    session: AsyncSession, *, work: Any, run: Any, production: dict[str, Any]
) -> HarnessLibraryModel | None:
    """递归交付的稿子过了 CHECK：把这一轮写进改进库，并按需开一次本书试用。"""
    if production.get("h2_recorded"):
        return None
    if production.get("h2_delivered_from") not in ("probe", "successor"):
        return None
    overrides = production.get("h2_overrides") or {}
    if not overrides:
        return None
    from regent.novel.application.directing_chapter import h2_config, latest_arc_review

    ctx = run.generation_context or {}
    lesson = production.get("h2_lesson") or {}
    experience = str(lesson.get("experience") or "").strip()
    terms = book_terms(ctx, production.get("cast") or {})
    # 通用条目对所有书可见，要过两道：专有名词筛查是确定性的，改写过的情节说法
    # （第 4 章那条「同字不同齿距」）由 SCREEN 那一次调用判。筛查没跑成、或者这一轮
    # 根本没走 SCREEN（老运行、非 generic 分支），都按最坏情况处理，只留在本书。
    screen = production.get("h2_screen")
    generic = (
        lesson.get("kind") == "generic"
        and not mentions_names([experience, overrides], terms)
        and isinstance(screen, dict)
        and screen.get("book_specific") is False
    )
    genre = str(getattr(work, "genre", "") or "")
    # 只对本类型成立的通用经验进类型范围：同类型的书检索得到，别的类型看不见。
    scope = "work"
    if generic:
        scope = "genre" if screen.get("genre_only") and genre else "global"
    work_id = getattr(work, "id", None)
    chapter_no = int(getattr(run, "chapter_no", 0) or 0)
    trial_open = bool(await _work_entries(session, work_id, "provisional"))
    codes = [code for code in lesson.get("failure_codes") or [] if code]
    plan = production.get("chapter_plan") or ctx.get("target_node") or {}
    baseline = await first_pass_baseline(session, work_id, before_chapter=chapter_no)
    review = latest_arc_review(ctx, production)
    if "keeps_reading" in review:
        baseline["keeps_reading"] = bool(review["keeps_reading"])
    entry = await library.record_entry(
        session,
        scope=scope,
        scope_key=str(work_id) if scope == "work" else "",
        genre_tag=genre,
        chapter_role="payoff" if isinstance(plan, dict) and plan.get("shows_rule") else "chapter",
        failure_code=codes[0] if codes else str(lesson.get("trigger") or "")
        if lesson.get("trigger") in ("arc", "stall")
        else "",
        config_tree=h2_config(ctx, production).tree,
        config_diff=overrides,
        experience=experience,
        evidence_refs=[
            str(run.id),
            *(f"{library.CITE_PREFIX}{hit}" for hit in lesson.get("cited") or []),
        ],
        parent_entry_id=ctx.get("harness_overlay_id") or None,
        source_work_id=work_id,
        source_run_id=getattr(run, "id", None),
        harness_hash=str(production.get("h2_effective_hash") or ""),
        status="candidate" if trial_open else "provisional",
        overlay_after=None
        if trial_open
        else merge_overrides(ctx.get("harness_overlay") or {}, overrides),
        since_chapter=chapter_no + 1,
        baseline=baseline,
    )
    for hit in lesson.get("cited") or []:
        await library.mark_effective(session, hit)
    # 库只在这里长，整理也放在这里：没有别的定时任务会去跑它。
    await library.merge_overlapping(session)
    await library.prune_unused(session)
    production["h2_recorded"] = str(entry.id)
    return entry
