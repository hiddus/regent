"""一次性补丁：失效判定必须回查数据库，不能信任内存中的 run 对象。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/works.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    """    def _intact() -> bool:
        return lease_is_valid(
            run, owner=_LEASE_OWNER, token=fencing_token
        ) and int(run.input_version or 1) == input_version_before
""",
    '''    async def _stale_reason() -> str | None:
        """回查数据库判定本次结果是否属于旧方向；None 表示仍然有效。

        不能只看内存里的 ``run``：会话默认 ``expire_on_commit=False``，其他会话
        改了 input_version 或抢占了租约，内存对象看不见，会误判为仍然有效。
        显式只查列，保证一定从数据库读。
        """
        row = await session.scalar(
            select(
                ChapterRunModel.lease_owner,
                ChapterRunModel.fencing_token,
                ChapterRunModel.lease_expires_at,
                ChapterRunModel.input_version,
            ).where(ChapterRunModel.id == run.id)
        )
        if row is None:
            return "run_missing"
        owner_now, token_now, expires_now, version_now = row
        if not lease_is_valid(
            SimpleNamespace(
                lease_owner=owner_now,
                fencing_token=token_now,
                lease_expires_at=expires_now,
            ),
            owner=_LEASE_OWNER,
            token=fencing_token,
        ):
            return "lease_lost"
        if int(version_now or 1) != input_version_before:
            return "input_version_changed"
        return None
''',
)

sub(
    """        if not _intact():
            # 租约已被接管或用户已改意：这次失败属于旧方向，不得写回（P0-4）
            return await get_run_progress(session, owner_id=owner_id, work_id=work_id)""",
    """        if await _stale_reason() is not None:
            # 租约已被接管或用户已改意：这次失败属于旧方向，不得写回（P0-4）
            return await get_run_progress(session, owner_id=owner_id, work_id=work_id)""",
)

sub(
    """    if not _intact():
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.result_discarded",
            data={
                "chapter_no": chapter_no,
                "step": pending.step,
                "reason": "lease_lost" if not lease_is_valid(
                    run, owner=_LEASE_OWNER, token=fencing_token
                ) else "input_version_changed",
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)""",
    """    stale = await _stale_reason()
    if stale is not None:
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.result_discarded",
            data={
                "chapter_no": chapter_no,
                "step": pending.step,
                "reason": stale,
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)""",
)

# SimpleNamespace 导入
sub(
    """from __future__ import annotations
""",
    """from __future__ import annotations

from types import SimpleNamespace
""",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
