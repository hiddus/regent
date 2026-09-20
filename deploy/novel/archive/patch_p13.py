"""一次性补丁：P1-3 按卷记忆与路径终止。"""
from __future__ import annotations

import pathlib

gen = pathlib.Path("core/src/regent/novel/application/generation.py")
text = gen.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"generation.py count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) 当前卷号 + 事实打标签
sub(
    """async def _canon_facts(""",
    '''async def _current_volume_no(session: AsyncSession, work: StoryWorkModel) -> int:
    """当前卷号：优先 ACTIVE，其次编号最大的一卷。"""
    current = await session.scalar(
        select(VolumeModel)
        .where(
            VolumeModel.work_id == work.id,
            VolumeModel.state == "ACTIVE",
        )
        .order_by(VolumeModel.volume_no.desc())
        .limit(1)
    )
    if current is not None:
        return int(current.volume_no)
    latest = await session.scalar(
        select(VolumeModel)
        .where(VolumeModel.work_id == work.id)
        .order_by(VolumeModel.volume_no.desc())
        .limit(1)
    )
    return int(latest.volume_no) if latest is not None else 1


def tag_facts(
    facts: list[Any], *, volume_no: int, chapter_no: int
) -> list[dict[str, Any]]:
    """给事实打上卷/章标签（P1-3）。

    没有标签就无法按卷切分，长篇只能退化成「最近 N 条」——那样既会把前几卷的
    细节搬进当前卷，也会把当前卷的关键事实挤出上下文。
    """
    tagged: list[dict[str, Any]] = []
    for fact in facts:
        item = dict(fact) if isinstance(fact, dict) else fact.model_dump(mode="json")
        item.setdefault("volume_no", int(volume_no))
        item.setdefault("chapter_no", int(chapter_no))
        tagged.append(item)
    return tagged


async def _canon_facts(''',
)

# 2) 第一级改为按卷切分
sub(
    """    - 当前卷：全量事实（最近 CanonCommit），上限 120 条
    - 前 1-2 卷：每卷摘要（VolumeModel.summary），每卷 <= 20 条
    - 更早卷 / 全局：世界观级摘要，<= 15 条

    TODO(R2): 第一级目前用「最近 120 条 Canon 事实」近似，未按卷切分。
    Canon 事实不保证携带 volume_no，直接按卷过滤会在事实缺标签时静默清空上下文，
    因此未做该过滤；长篇阶段需为 Canon 事实补齐 volume_no 后再启用真正的分卷取事实。
    \"\"\"""",
    """    - 当前卷：全量事实（按 volume_no 过滤，上限 120 条）
    - 前 1-2 卷：每卷摘要（VolumeModel.summary），每卷 <= 20 条
    - 更早卷 / 全局：世界观级摘要，<= 15 条

    历史事实可能没有 volume_no 标签。直接按卷过滤会在标签缺失时静默清空上下文，
    因此缺失标签的事实按当前卷处理；新提交的事实一律由 ``tag_facts`` 打标签。
    \"\"\"""",
)

sub(
    """    current_volume_facts = all_facts[-120:]
""",
    """    current_volume_no = await _current_volume_no(session, work)
    in_volume = [
        fact
        for fact in all_facts
        if int(fact.get("volume_no", current_volume_no) or current_volume_no)
        == current_volume_no
    ]
    # 标签整体缺失时退回全量：宁可多带，也不能把上下文清空到无法创作。
    current_volume_facts = (in_volume or all_facts)[-120:]
""",
)

# 3) canon 提交时打标签
sub(
    """    latest = await session.scalar(
        select(CanonCommitModel)
        .where(CanonCommitModel.work_id == work.id, CanonCommitModel.branch_id == work.branch_id)
        .order_by(CanonCommitModel.version.desc()).limit(1)
    )
    parent = int(latest.version) if latest else 0""",
    """    latest = await session.scalar(
        select(CanonCommitModel)
        .where(CanonCommitModel.work_id == work.id, CanonCommitModel.branch_id == work.branch_id)
        .order_by(CanonCommitModel.version.desc()).limit(1)
    )
    parent = int(latest.version) if latest else 0
    facts = tag_facts(
        facts,
        volume_no=await _current_volume_no(session, work),
        chapter_no=int(run.chapter_no),
    )""",
)

# 4) 末节点完成后不得重复生成
sub(
    """        if previous_index is not None:
            index = min(len(nodes) - 1, previous_index + int(previous.get("node_completed", False)))
            target = nodes[index]
        elif not recent_chapters:
            target = nodes[0]""",
    """        if previous_index is not None:
            completed = bool(previous.get("node_completed"))
            if completed and previous_index >= len(nodes) - 1:
                # 末节点已完成：不再重复生成它（P1-3）。是否还有下一章取决于
                # 有没有展开出新卷与新节点；没有就是整本结束。
                target = None
                story_complete = True
            else:
                target = nodes[min(len(nodes) - 1, previous_index + int(completed))]
        elif not recent_chapters:
            target = nodes[0]""",
)

sub(
    """    # 计算前后节点（让 DIRECT 知道从哪来、往哪去）
    target_idx = None""",
    """    # 计算前后节点（让 DIRECT 知道从哪来、往哪去）
    target_idx = None""",
)

sub(
    """    run.generation_context = {
        "architecture_version": architecture_version,""",
    """    run.generation_context = {
        "architecture_version": architecture_version,
        "story_complete": story_complete,""",
)

# story_complete 初始化
sub(
    """    prev_node = nodes[target_idx - 1] if target_idx and target_idx > 0 else None""",
    """    prev_node = nodes[target_idx - 1] if target_idx and target_idx > 0 else None""",
)

gen.write_text(text, encoding="utf-8")
print("patched", gen)
