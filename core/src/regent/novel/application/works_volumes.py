"""Volume completion services."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application import memory as memory_app
from regent.novel.application.events import append_event
from regent.novel.application.generation import generate_ending_verdict
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import CHAPTERS_PER_NODE, MAX_NODES_PER_VOLUME
from regent.novel.domain import ending
from regent.novel.domain.errors import ValidationFailed
from regent.novel.domain.states import ChapterRunState, StoryWorkState, assert_story_work_transition
from regent.novel.infrastructure.models import (
    ArcNodeModel,
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)

logger = logging.getLogger(__name__)


def _cn_num(n: int) -> str:
    """整数转中文数字（1-10）。"""
    mapping = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
    }
    return mapping.get(n, str(n))


async def _path_node_ids(session: AsyncSession, work: StoryWorkModel) -> list[str]:
    """当前关键路径上的节点 id，按序（P1-3）。

    扩卷后路径会变长，所以判断「还有没有下一节点」必须实时查，不能沿用
    生成期缓存下来的节点数。
    """
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return []
    return list(
        (
            await session.scalars(
                select(CriticalNodeModel.node_id)
                .where(CriticalNodeModel.path_id == path.id)
                .order_by(CriticalNodeModel.ordinal)
            )
        ).all()
    )


async def _last_node_completed(session: AsyncSession, work: StoryWorkModel, run) -> bool:
    """本章完成的是不是关键路径上的最后一个节点（P1-3）。"""
    context = run.generation_context or {}
    if not context.get("node_completed"):
        return False
    target_id = (context.get("target_node") or {}).get("id")
    if not target_id:
        return False
    node_ids = await _path_node_ids(session, work)
    return bool(node_ids) and target_id == node_ids[-1]


def _mark_story_complete(
    session: AsyncSession, *, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    """整本结束：标在刚成章这一次运行上，让 start_run 不再开空章（P1-3）。

    标记必须落在**已完成的那一次运行**上。只写在作品上，start_run 看到的
    还是上一章的旧 context，于是会再开一章没有目标节点的任务（A-03）。
    """
    context = dict(run.generation_context or {})
    context["story_complete"] = True
    run.generation_context = context
    run.version += 1
    if work.state != StoryWorkState.DONE.value:
        assert_story_work_transition(work.state, StoryWorkState.DONE.value)
        work.state = StoryWorkState.DONE.value
        work.version += 1
    session.add(run)


async def _ending_intent_of(work: StoryWorkModel) -> ending.EndingIntent:
    """用户认可的终局。没有它，末节点完成后只能靠「扩卷失败」来结束（B-05）。"""
    return ending.EndingIntent(
        target_volume_count=int(work.ending_target_volume or 0),
        ending_statement=str(work.ending_statement or ""),
    )


async def _director_ending_verdict(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None,
) -> tuple[bool | None, str]:
    """导演对终局的判断。``None`` 表示**没有判断**，不是「判断为没讲完」。

    这个区分是 B-05 的关键：把「模型调用失败」当成「还没讲完」，系统就会在模型
    故障时自动扩卷，把一次技术故障翻译成创作决策——而且扩出来的卷用的是静态
    模板，等于替用户改了方向。
    """
    if provider is None:
        return None, "没有可用的模型：不做终局判断"
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work.id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    # 只取**当前路径**的节点：历史版本的节点不是这一版的计划，混进来会让终局
    # 判断对着一条已经被改掉的计划下结论（C-05）。
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    nodes = []
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
    # 已经写出来的东西：终局判断据此，而不是据此的**计划**（C-05）。
    from regent.novel.domain import memory as memory_domain
    from regent.novel.infrastructure.models import CanonCommitModel

    accepted_text = ""
    latest_run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.content != "",
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if latest_run is not None:
        accepted_text = str(latest_run.content or "")
    canon_commit = await session.scalar(
        select(CanonCommitModel)
        .where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
        )
        .order_by(CanonCommitModel.version.desc())
        .limit(1)
    )
    verified_facts = [
        fact
        for fact in ((canon_commit.facts if canon_commit else None) or [])
        if isinstance(fact, dict)
    ]
    # 还没收的线：这是「有没有讲完」最硬的证据——有未兑现承诺就没讲完。
    bundle = await memory_app.recall_memory(
        session, work=work, chapter_no=int(work.latest_chapter_no), kinds=("promise",)
    )
    open_promises = [item.as_payload() for item in memory_domain.open_promises(bundle.items)]
    try:
        verdict = await generate_ending_verdict(
            provider,
            session=session,
            work=work,
            run=run,
            raw_intent=goal.raw_intent if goal else "",
            genre=work.genre or "",
            ending_statement=str(work.ending_statement or ""),
            volume_no=int(work.total_volume_count or 1),
            latest_chapter_no=int(work.latest_chapter_no),
            completed_nodes=[{"title": n.title, "promise": n.promise} for n in nodes],
            accepted_text=accepted_text,
            verified_facts=verified_facts,
            open_promises=open_promises,
        )
    except Exception as exc:  # 模型故障不是创作结论
        return None, f"导演终局判断不可用：{type(exc).__name__}"
    return bool(verdict.story_complete), str(verdict.reason or "")


async def _decide_ending(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None = None,
) -> ending.EndingDecision:
    """按「用户终局 → 导演判定 → 无依据」决定结束、扩卷还是待定。"""
    intent = await _ending_intent_of(work)
    volume_no = int(work.total_volume_count or 1)
    if int(intent.target_volume_count) > 0:
        # 用户给了卷数：数字本身就是依据，问模型等于把用户的终局降级成建议。
        return ending.decide_ending(intent=intent, volume_no=volume_no)
    complete, reason = await _director_ending_verdict(
        session, work=work, run=run, provider=provider
    )
    return ending.decide_ending(
        intent=intent,
        volume_no=volume_no,
        director_complete=complete,
        director_reason=reason,
    )


def _mark_ending_decision(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    decision: ending.EndingDecision,
) -> None:
    """把终局判定落在**已完成的那一次运行**上。

    只写在作品上，start_run 看到的还是上一章的旧 context，于是会再开一章没有
    目标节点的任务（A-03）。undecided 也要落盘：它带着卷号与章号，是下一次
    判定接着走的上下文。
    """
    context = dict(run.generation_context or {})
    context["ending_decision"] = decision.as_payload()
    run.generation_context = context
    run.version += 1
    session.add(run)


async def _after_chapter_completed(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None = None,
) -> None:
    """成章收尾：先按终局意图决定结束还是扩卷（B-05 / P1-3 / A-03）。

    旧行为是「末节点完成 → 先扩卷 → 扩不出新节点才置 DONE」。那等于把终点定义
    为「生成失败」：正常故事永远不结束，而模型一挂反而会「完本」。现在顺序反过来
    ——先问该不该结束，该继续才扩卷；扩卷失败**不套静态模板**，保留待定状态。
    """
    if await _last_node_completed(session, work, run):
        decision = await _decide_ending(session, work=work, run=run, provider=provider)
        if decision.complete:
            _mark_ending_decision(session, work=work, run=run, decision=decision)
            _mark_story_complete(session, work=work, run=run)
            await append_event(
                session,
                work_id=work.id,
                event_type="story.completed",
                data={
                    "chapter_no": int(work.latest_chapter_no),
                    "basis": decision.basis,
                    "reason": decision.reason,
                },
                branch_id=work.branch_id,
            )
            return
        if decision.expand:
            # 职责删减：禁止章完成后静默扩卷；仅记录待确认，由用户调用 expand API。
            pending = ending.EndingDecision(
                choice=ending.UNDECIDED,
                reason=("已判定应继续下一卷，等待用户确认扩卷：" + (decision.reason or "")),
                basis=decision.basis,
            )
            _mark_ending_decision(session, work=work, run=run, decision=pending)
            if work.state == StoryWorkState.RUNNING.value:
                assert_story_work_transition(work.state, StoryWorkState.PENDING_DECISION.value)
                work.state = StoryWorkState.PENDING_DECISION.value
                work.version += 1
            await append_event(
                session,
                work_id=work.id,
                event_type="volume.expansion_pending",
                data={
                    "chapter_no": int(work.latest_chapter_no),
                    "volume_no": int(work.total_volume_count or 0) + 1,
                    "reason": decision.reason,
                    "basis": decision.basis,
                    "available_actions": ["expand_volume", "set_ending_intent"],
                },
                branch_id=work.branch_id,
            )
            return
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        await append_event(
            session,
            work_id=work.id,
            event_type="ending.undecided",
            data={
                "chapter_no": int(work.latest_chapter_no),
                "volume_no": int(work.total_volume_count or 0),
                "reason": decision.reason,
            },
            branch_id=work.branch_id,
        )
        return
    # 非末节点：作品级连续创作（C01）。默认 policy 关闭时 ensure_next_run 返回 None。
    from regent.novel.application.works_continuation import ensure_next_run

    await ensure_next_run(session, work=work, completed_run=run)
    return


async def _maybe_expand_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
    run: ChapterRunModel | None = None,
) -> None:
    """已退役：自动扩卷移出生产主链。

    保留函数签名以免旧测试/调用方立刻炸；新行为恒为 no-op。
    扩卷唯一写路径：``POST /works/{id}/volumes/{n}/expand``（用户确认）。
    """
    del session, work, provider, run
    return


async def expand_next_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
    target_volume_no: int | None = None,
) -> VolumeModel | None:
    """当前卷写满或末节点完成时，生成下一卷的弧段和节点。

    **没有可用的大纲就返回 None，绝不套用静态模板。** 旧实现在模型失败时填入
    「变强 / 更强大的对手」这条与题材和用户意图都无关的升级模板：卷是续上了，
    但续的是模板的方向，不是用户的方向，而且已经写进正文不可逆（B-05）。

    ``target_volume_no``：调用方期望的新卷号；已存在则幂等返回，与期望不符则拒绝。
    """
    current_vol_no = int(work.total_volume_count)
    next_vol_no = current_vol_no + 1
    if target_volume_no is not None and int(target_volume_no) != next_vol_no:
        existing_mismatch = await session.scalar(
            select(VolumeModel).where(
                VolumeModel.work_id == work.id,
                VolumeModel.volume_no == int(target_volume_no),
            )
        )
        if existing_mismatch is not None:
            return existing_mismatch
        return None

    # 检查是否已有下一卷
    existing = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == next_vol_no,
        )
    )
    if existing is not None:
        return existing

    # 获取当前路径和节点
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return None
    existing_nodes = list(
        (
            await session.scalars(
                select(CriticalNodeModel)
                .where(CriticalNodeModel.path_id == path.id)
                .order_by(CriticalNodeModel.ordinal)
            )
        ).all()
    )
    max_ordinal = max((n.ordinal for n in existing_nodes), default=0)

    # 尝试 LLM 生成下一卷大纲
    outline = None
    if provider is not None:
        try:
            from regent.novel.application.generation import generate_volume_expansion_outline

            # 获取上一卷信息
            prev_vol = await session.scalar(
                select(VolumeModel).where(
                    VolumeModel.work_id == work.id,
                    VolumeModel.volume_no == current_vol_no,
                )
            )
            # 获取 goal
            goal = await session.scalar(
                select(StoryGoalModel)
                .where(StoryGoalModel.work_id == work.id)
                .order_by(StoryGoalModel.version.desc())
                .limit(1)
            )
            last_node_dicts = [
                {"title": n.title, "promise": n.promise, "consequences": n.consequences or []}
                for n in existing_nodes[-5:]
            ]
            outline = await generate_volume_expansion_outline(
                provider,
                raw_intent=goal.raw_intent if goal else "",
                genre=work.genre or "",
                volume_no=next_vol_no,
                prev_volume_title=prev_vol.title if prev_vol else "",
                prev_volume_summary=list(prev_vol.summary or []) if prev_vol else [],
                last_nodes=last_node_dicts,
                cultivation_realm=prev_vol.cultivation_realm if prev_vol else "",
            )
        except Exception as exc:
            # 拿不到大纲就不扩卷。套静态模板会得到一卷「变强／更强大的对手」，
            # 那是模板的方向不是用户的方向，且已经写进正文不可逆（B-05）。
            await append_event(
                session,
                work_id=work.id,
                event_type="volume.expansion_failed",
                data={
                    "volume_no": next_vol_no,
                    "chapter_no": int(work.latest_chapter_no),
                    "reason": f"outline_failed:{type(exc).__name__}",
                },
                branch_id=work.branch_id,
            )
            return None
    else:
        # 没有模型就没有大纲：同样不扩卷，等有模型时再判定（provider=None 不是
        # 「扩卷失败」，但它也不构成「可以自己编一卷」的理由）。
        await append_event(
            session,
            work_id=work.id,
            event_type="volume.expansion_failed",
            data={
                "volume_no": next_vol_no,
                "chapter_no": int(work.latest_chapter_no),
                "reason": "no_provider",
            },
            branch_id=work.branch_id,
        )
        return None

    from regent.novel.domain.models import CriticalNode as CN
    from regent.novel.domain.models import PathNodeType

    new_nodes = []
    for idx, o_node in enumerate(outline.nodes):
        ntype_str = o_node.node_type.upper()
        try:
            ntype = PathNodeType(ntype_str)
        except ValueError:
            ntype = PathNodeType.ESCALATION
        new_nodes.append(
            CN(
                node_id=f"n{max_ordinal + idx + 1:02d}",
                ordinal=max_ordinal + idx + 1,
                title=o_node.title,
                node_type=ntype,
                promise=o_node.promise,
                preconditions=list(o_node.preconditions),
                consequences=list(o_node.consequences),
            )
        )
    vol_title = outline.volume_title or f"第{_cn_num(next_vol_no)}卷"
    vol_realm = outline.cultivation_realm or ""
    if not new_nodes:
        await append_event(
            session,
            work_id=work.id,
            event_type="volume.expansion_failed",
            data={
                "volume_no": next_vol_no,
                "chapter_no": int(work.latest_chapter_no),
                "reason": "empty_outline",
            },
            branch_id=work.branch_id,
        )
        return None

    # 裁剪到合法范围
    if len(new_nodes) > MAX_NODES_PER_VOLUME:
        new_nodes = new_nodes[:MAX_NODES_PER_VOLUME]

    # 写入新 CriticalNode
    for idx, node in enumerate(new_nodes):
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=path.id,
                node_id=node.node_id,
                ordinal=node.ordinal,
                title=node.title,
                node_type=node.node_type.value,
                promise=node.promise,
                preconditions=list(node.preconditions),
                consequences=list(node.consequences),
                requires_human=node.requires_human,
                locked=node.locked,
                volume_no=next_vol_no,
                arc_no=(idx // CHAPTERS_PER_NODE) + 1,
            )
        )
    path.node_count = len(existing_nodes) + len(new_nodes)

    # 标记当前卷完成
    current_vol = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == current_vol_no,
        )
    )
    if current_vol is not None:
        current_vol.state = "COMPLETED"
        # 卷边界必须收口到实际写完的最后一章。保留预计 end_chapter_no 会让旧卷
        # 的区间继续盖住新卷的章号，装配时按区间定位就落到旧卷上——节点数和
        # 章数都不是预设值时，这个漂移直接让新卷的节点选不中（A-03）。
        current_vol.end_chapter_no = max(
            int(current_vol.start_chapter_no), int(work.latest_chapter_no)
        )

    # 创建新卷
    chapters_per_vol = len(new_nodes) * CHAPTERS_PER_NODE
    start_ch = int(work.latest_chapter_no) + 1
    new_vol = VolumeModel(
        id=uuid.uuid4(),
        work_id=work.id,
        volume_no=next_vol_no,
        title=vol_title,
        summary=[],
        cultivation_realm=vol_realm,
        start_chapter_no=start_ch,
        end_chapter_no=start_ch + chapters_per_vol - 1,
        state="ACTIVE",
    )
    session.add(new_vol)
    await session.flush()

    # 创建弧段
    arc_count = max(1, len(new_nodes) // 3)
    for arc_idx in range(arc_count):
        start_ord = arc_idx * 3 + 1
        end_ord = min((arc_idx + 1) * 3, len(new_nodes))
        session.add(
            ArcNodeModel(
                id=uuid.uuid4(),
                volume_id=new_vol.id,
                arc_no=arc_idx + 1,
                title=new_nodes[start_ord - 1].title if start_ord <= len(new_nodes) else "",
                arc_type="STANDARD",
                chapter_range_start=start_ch + (start_ord - 1) * CHAPTERS_PER_NODE,
                chapter_range_end=start_ch + end_ord * CHAPTERS_PER_NODE - 1,
            )
        )

    work.total_volume_count = next_vol_no
    work.version += 1
    # 卷末确认闭环：扩卷成功后解除 PENDING_DECISION，恢复可运行。
    if work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
    await session.flush()

    # 落盘「已扩卷」终局记录，避免投影仍提示 open_decision。
    latest_run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if latest_run is not None:
        resolved = ending.EndingDecision(
            choice=ending.EXPAND,
            reason=f"用户确认扩至第{_cn_num(next_vol_no)}卷",
            basis="user_expand_confirm",
        )
        _mark_ending_decision(session, work=work, run=latest_run, decision=resolved)
        ctx = dict(latest_run.generation_context or {})
        ctx["volume_expansion_resolved"] = {
            "volume_no": next_vol_no,
            "resolved_at_chapter": int(latest_run.chapter_no),
        }
        latest_run.generation_context = ctx
        latest_run.version += 1
        session.add(latest_run)

    await append_event(
        session,
        work_id=work.id,
        event_type="volume.expanded",
        data={
            "volume_no": next_vol_no,
            "node_count": len(new_nodes),
            "work_state": work.state,
        },
        branch_id=work.branch_id,
    )

    # 若作品级连续创作已授权：扩卷确认后按 scope 刷新钉扎，再排队新卷首章。
    try:
        from regent.novel.application.works_continuation import (
            ensure_next_run,
            get_continuation_policy,
            refresh_continuation_after_volume_expand,
        )

        refresh_continuation_after_volume_expand(
            work,
            new_volume_no=next_vol_no,
            new_end_chapter_no=int(new_vol.end_chapter_no or 0) or None,
        )
        if get_continuation_policy(work).enabled and latest_run is not None:
            if latest_run.state == ChapterRunState.CANONIZED.value:
                await ensure_next_run(session, work=work, completed_run=latest_run)
    except Exception:
        # 扩卷本身已成功；开章失败留给补偿扫描，不回滚卷。
        logger.exception("ensure_next_run after expand failed work=%s", work.id)

    return new_vol


async def get_volumes(session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID) -> list:
    """获取作品的所有卷（含弧段）。"""
    from regent.novel.domain.models import ArcNodeOut, VolumeOut

    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    vols = list(
        (
            await session.scalars(
                select(VolumeModel)
                .where(VolumeModel.work_id == work_id)
                .order_by(VolumeModel.volume_no)
            )
        ).all()
    )
    result = []
    for v in vols:
        arcs = list(
            (
                await session.scalars(
                    select(ArcNodeModel)
                    .where(ArcNodeModel.volume_id == v.id)
                    .order_by(ArcNodeModel.arc_no)
                )
            ).all()
        )
        result.append(
            VolumeOut(
                volume_no=int(v.volume_no),
                title=v.title,
                cultivation_realm=v.cultivation_realm,
                start_chapter_no=int(v.start_chapter_no),
                end_chapter_no=int(v.end_chapter_no),
                state=v.state,
                summary=list(v.summary or []),
                arcs=[
                    ArcNodeOut(
                        arc_no=int(a.arc_no),
                        title=a.title,
                        arc_type=a.arc_type,
                        chapter_range_start=int(a.chapter_range_start),
                        chapter_range_end=int(a.chapter_range_end),
                        core_conflict=a.core_conflict,
                        resolution_type=a.resolution_type,
                    )
                    for a in arcs
                ],
            )
        )
    return result


async def set_ending_intent(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    target_volume_count: int | None = None,
    ending_statement: str | None = None,
) -> dict[str, Any]:
    """设定用户认可的终局（B-05）。

    这是三动作里「说出故事目标」的一部分：没有它，末节点完成后系统只能靠
    「扩卷失败」来结束，正常故事永远不结束。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if target_volume_count is not None:
        if int(target_volume_count) < 0:
            raise ValidationFailed("target_volume_count 不能为负")
        work.ending_target_volume = int(target_volume_count)
    if ending_statement is not None:
        work.ending_statement = str(ending_statement)[:500]
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="ending.intent_set",
        data=ending.EndingIntent(
            target_volume_count=int(work.ending_target_volume or 0),
            ending_statement=str(work.ending_statement or ""),
        ).as_payload(),
        branch_id=work.branch_id,
    )
    return {
        "target_volume_count": int(work.ending_target_volume or 0),
        "ending_statement": str(work.ending_statement or ""),
    }


async def resolve_ending(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    """终局待定时重新判定：能判就结束或扩卷，判不了就如实继续待定（B-05）。

    待定不是终态。它只说明**上一次**没有拿到依据；这次拿到了就往下走，拿不到
    就把待定留在运行上，等下次——不留死局，也不靠猜。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if run is None or not await _last_node_completed(session, work, run):
        return {"choice": "not_applicable", "reason": "末节点尚未完成", "state": work.state}
    decision = await _decide_ending(session, work=work, run=run, provider=provider)
    if decision.complete:
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        _mark_story_complete(session, work=work, run=run)
        outcome = decision.choice
    elif decision.expand:
        # 与章完成收尾一致：不自动扩卷，返回待确认建议。
        pending = ending.EndingDecision(
            choice=ending.UNDECIDED,
            reason=("已判定应继续下一卷，等待用户确认扩卷：" + (decision.reason or "")),
            basis=decision.basis,
        )
        _mark_ending_decision(session, work=work, run=run, decision=pending)
        if work.state == StoryWorkState.RUNNING.value:
            assert_story_work_transition(work.state, StoryWorkState.PENDING_DECISION.value)
            work.state = StoryWorkState.PENDING_DECISION.value
            work.version += 1
        await append_event(
            session,
            work_id=work.id,
            event_type="volume.expansion_pending",
            data={
                "chapter_no": int(work.latest_chapter_no),
                "volume_no": int(work.total_volume_count or 0) + 1,
                "reason": decision.reason,
                "basis": decision.basis,
                "available_actions": ["expand_volume", "set_ending_intent"],
            },
            branch_id=work.branch_id,
        )
        outcome = ending.UNDECIDED
    else:
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        outcome = decision.choice
    await append_event(
        session,
        work_id=work_id,
        event_type="ending.resolved",
        data={"choice": outcome, "reason": decision.reason, "basis": decision.basis},
        branch_id=work.branch_id,
        chapter_no=int(work.latest_chapter_no),
    )
    await session.flush()
    return {
        "choice": outcome,
        "reason": decision.reason,
        "basis": decision.basis,
        "state": work.state,
    }
