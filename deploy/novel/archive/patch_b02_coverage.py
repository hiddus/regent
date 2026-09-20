"""让 test_incomplete_graph_falls_back_conservatively 构造「真正没有覆盖记录」的图。

建图入口现在会为「没有上游」的条目登记 independent（B-02），所以 with_edges=False
不再等于「图没建立」。要测保守退路，必须把覆盖记录本身抹掉。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
path = ROOT / "tests/unit/novel/test_path_change_scoped_memory.py"
text = path.read_text(encoding="utf-8")

pairs = [
    (
        """from regent.novel.infrastructure.models import (
    CriticalNodeModel,
    CriticalPathModel,
    MemoryItemModel,
)
from sqlalchemy import select""",
        """from regent.novel.infrastructure.models import (
    CriticalNodeModel,
    CriticalPathModel,
    MemoryEdgeModel,
    MemoryItemModel,
)
from sqlalchemy import delete, select""",
    ),
    (
        """    \"\"\"没有依赖边 = 图还没建立，必须保守失效同类整批，并如实记录走了退路。\"\"\"""",
        """    \"\"\"依赖覆盖记录缺失 = 图不完整，必须保守失效同类整批，并如实记录走了退路。\"\"\"""",
    ),
    (
        """        await _seed_path(s, work, titles=TITLES)
        await _seed_memory(s, work, with_edges=False)
        await s.commit()

        await _update(s, work, titles=_titles_with_change())""",
        """        await _seed_path(s, work, titles=TITLES)
        await _seed_memory(s, work, with_edges=False)
        # 建图入口会为「没有上游」的条目登记 independent；这里把覆盖记录全部抹掉，
        # 模拟依赖信息缺失。此时「这条是独立的」与「这条的依赖漏记了」无从区分，
        # 唯一诚实的处置就是保守重做。
        await s.execute(delete(MemoryEdgeModel))
        await s.commit()

        await _update(s, work, titles=_titles_with_change())""",
    ),
]

for old, new in pairs:
    count = text.count(old)
    assert count == 1, f"old 出现 {count} 次 -> {old[:60]!r}"
    text = text.replace(old, new)

path.write_text(text, encoding="utf-8")
print("patched test_path_change_scoped_memory.py")
