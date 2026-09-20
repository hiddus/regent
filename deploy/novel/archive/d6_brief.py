"""取最近一次失败的 DIRECT/plan 调用，对比产出角色名与角色表。

只读。回答一个问题：``_check_brief`` 报"重复或未定义的角色"时，模型到底写了什么名字。
"""

from __future__ import annotations

import json
import shlex
import sys

from _ssh import Remote

PSQL = 'docker exec -i regent-postgres psql -U regent -d regent -t -A -F "|" -c'


def q(r: Remote, sql: str) -> str:
    return r.run(f'{PSQL} {shlex.quote(" ".join(sql.split()))}', timeout=120).out


def main() -> int:
    r = Remote()
    # 1) 最近 5 条 purpose=plan 的调用
    print("=== 最近 plan 调用 ===")
    print(
        q(
            r,
            """
            SELECT left(created_at::text,19), status, purpose, model,
                   COALESCE(error_code,'') , left(logical_call_id,40)
            FROM novel_model_calls
            WHERE purpose='plan'
            ORDER BY created_at DESC LIMIT 5
            """,
        )
    )

    # 2) 最近一条有 output_json 的 plan 调用：抽出 scenes[].actors[].persona
    print("\n=== 最近 plan 产出里的角色名 ===")
    out = q(
        r,
        """
        SELECT output_json::text
        FROM novel_model_calls
        WHERE purpose='plan' AND output_json IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
    )
    if not out.strip():
        print("(无 output_json)")
        return 1
    try:
        data = json.loads(out.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"[解析失败] {exc}\n{out[:2000]}")
        return 1
    for i, scene in enumerate(data.get("scenes") or []):
        names = [a.get("persona") for a in scene.get("actors") or []]
        print(f"  scene[{i}] actors={names}")
    print(f"  title={data.get('title')!r}")

    # 3) 角色表里的名字（每个作品）
    print("\n=== 角色表 ===")
    print(
        q(
            r,
            """
            SELECT left(w.id::text,8), p.name, left(p.identity,30)
            FROM novel_personas p JOIN novel_works w ON w.id=p.work_id
            ORDER BY w.created_at DESC, p.name
            """,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
