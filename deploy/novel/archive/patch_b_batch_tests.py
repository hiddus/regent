"""修正 test_b_batch.py 中四处构造问题（不改任何断言语义）。"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
path = ROOT / "tests/unit/novel/test_b_batch.py"
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str, count: int = 1) -> None:
    global text
    got = text.count(old)
    assert got == count, f"old 出现 {got} 次（期望 {count}）-> {old[:70]!r}"
    text = text.replace(old, new)


# 1) 导演记忆用明确的失败原因信号；读者认知保持「读者尚不…」
sub(
    """            _fact("雨景写得太满，下次留白", **{"known_by": []}),""",
    """            _fact("雨景写得太满，记录失败原因", **{"known_by": []}),""",
)

# 2) 承诺的 subject 必须是在册人物：传 cast，与生产 cast_of 的行为一致
sub(
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.kind == "promise")
    assert item.state == "RESOLVED"
    assert int(item.source_chapter_no) == 2, "种下章被兑现覆盖了"
    assert int(item.resolved_chapter_no) == 7""",
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
            cast=["甲"],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.subject == "甲")
    assert item.state == "RESOLVED"
    assert int(item.source_chapter_no) == 2, "种下章被兑现覆盖了"
    assert int(item.resolved_chapter_no) == 7""",
)

sub(
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
        )
        await s.commit()
        # 第 9 章又把这句承诺写了一遍
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=9, facts=[_fact("甲承诺明日归还钥匙")],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.kind == "promise")
    assert item.state == "RESOLVED"
    assert int(item.resolved_chapter_no) == 7""",
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
            cast=["甲"],
        )
        await s.commit()
        # 第 9 章又把这句承诺写了一遍
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=9, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.subject == "甲")
    assert item.state == "RESOLVED"
    assert int(item.resolved_chapter_no) == 7""",
)

# 3) 建图入口会自动登记 independent；要构造「不完整」，必须抹掉那一条的覆盖记录
sub(
    """        await s.commit()
        # 只有 a→b：c 既可能独立，也可能是漏记了 b→c
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["a"])
    assert not plan.complete
    assert "覆盖" in plan.reason""",
    """        await s.commit()
        # 建图入口为三条都登记了 independent；抹掉 c 的那条，模拟「漏记了依赖」。
        await s.execute(
            delete(MemoryEdgeModel).where(
                MemoryEdgeModel.downstream_key == rows["c"],
                MemoryEdgeModel.edge_kind == "independent",
            )
        )
        await s.commit()
        # 只剩 a→b：c 既可能独立，也可能是漏记了 b→c
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["a"])
    assert not plan.complete
    assert rows["c"] in plan.unknown""",
)

# 4) 纠错用例同样需要 cast，否则主题取不到人物名
sub(
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=3, facts=[_fact("甲再度许诺")],
        )""",
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=3, facts=[_fact("甲再度许诺")], cast=["甲"],
        )""",
)

sub(
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")],
        )
        await s.commit()
        # 抹掉全部覆盖记录：此时无法区分「独立」与「漏记」""",
    """        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        # 抹掉全部覆盖记录：此时无法区分「独立」与「漏记」""",
)

# 5) 改路径用例必须给出合法节点数（10-20）
sub(
    """        path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, version=1)
        s.add(path)
        await s.flush()
        for ordinal in (1, 2):
            s.add(
                CriticalNodeModel(
                    id=uuid.uuid4(), path_id=path.id, node_id=f"n{ordinal}",
                    ordinal=ordinal, title=f"节点{ordinal}",
                )
            )
        await s.commit()
        _out, impact = await works.update_critical_path(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=CriticalPathUpdate(
                nodes=[], expected_version=1, change_note="清空"
            ),
        )
        await s.commit()
    assert impact is not None""",
    """        path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, version=1)
        s.add(path)
        await s.flush()
        for ordinal in range(1, works.MIN_PATH_NODES + 1):
            s.add(
                CriticalNodeModel(
                    id=uuid.uuid4(), path_id=path.id, node_id=f"n{ordinal}",
                    ordinal=ordinal, title=f"节点{ordinal}",
                )
            )
        await s.commit()
        _out, impact = await works.update_critical_path(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=CriticalPathUpdate(
                nodes=[
                    CriticalNode(
                        node_id=f"m{ordinal}", ordinal=ordinal, title=f"改后{ordinal}"
                    )
                    for ordinal in range(1, works.MIN_PATH_NODES + 1)
                ],
                expected_version=1,
                change_note="换方向",
            ),
        )
        await s.commit()
    assert impact is not None""",
)

sub(
    """from regent.novel.domain.models import CriticalPathUpdate, ReportFactRequest""",
    """from regent.novel.domain.models import (
    CriticalNode,
    CriticalPathUpdate,
    ReportFactRequest,
)""",
)

path.write_text(text, encoding="utf-8")
print("patched test_b_batch.py")
