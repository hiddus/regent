"""在服务器容器里核查 H2 臂的四件不能在本地证的事。

1. 改进库检索边界：试用中（provisional）的 global 条目会不会被别的书看到；
2. 回放校验：用钉住的上下文能否复原首稿 input_hash（晋级前的零调用前提）；
3. 试用判定：这条覆盖层的三章试用现在是什么状态、基线有没有；
4. 覆盖层是否真的进了执笔提示（对比默认臂常量）。

用法（本地）：python deploy/novel/h2_server_probe.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

REMOTE_PY = r'''
import asyncio, json, uuid

WORK = "064adf0a-6cda-4de4-8126-ed5aee38976f"
OTHER = "36ce0ca5-9c18-4270-b4fd-a9c484b319fb"
RUNS = ["16837d33-eb1b-43f6-a0c3-8e7db901d402", "f59d6642-71ef-40b7-89d8-447980d29374"]


async def main() -> None:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.novel.harnessing import library, overlay, replay
    from regent.novel.infrastructure.models import ChapterRunModel, HarnessLibraryModel
    from sqlalchemy import select

    out: dict = {}
    engine = create_engine(get_settings())
    sessions = create_session_factory(engine)
    try:
        async with sessions() as s:
            # 1. 检索边界：换一本书的身份查，看试用中的条目是否外泄
            rows = (await s.scalars(select(HarnessLibraryModel))).all()
            out["library_rows"] = [
                {
                    "id": str(r.id), "scope": r.scope, "status": r.status,
                    "genre_tag": r.genre_tag, "chapter_role": r.chapter_role,
                    "failure_code": r.failure_code, "since_chapter": r.since_chapter,
                    "baseline": r.baseline, "source_work": str(r.source_work_id or ""),
                }
                for r in rows
            ]
            hits_other = await library.search(
                s, work_id=uuid.UUID(OTHER), genre_tag="", chapter_role="payoff",
                failure_code="arc",
            )
            out["search_from_other_book"] = {
                "count": len(hits_other),
                "ids": [h["id"] for h in hits_other],
            }
            hits_own = await library.search(
                s, work_id=uuid.UUID(WORK), genre_tag="", chapter_role="payoff",
                failure_code="arc",
            )
            out["search_from_own_book"] = {
                "count": len(hits_own),
                "ids": [h["id"] for h in hits_own],
            }
            out["live_statuses"] = list(library.LIVE_STATUSES)
            out["shareable_statuses"] = list(library.SHAREABLE_STATUSES)
            out["baseline_before_ch5"] = await overlay.first_pass_baseline(
                s, uuid.UUID(WORK), before_chapter=5
            )

            # 2. 回放校验
            out["replay"] = []
            for run_id in RUNS:
                run = await s.get(ChapterRunModel, uuid.UUID(run_id))
                if run is None:
                    out["replay"].append({"run_id": run_id, "ok": False, "reason": "运行不存在"})
                    continue
                ctx = run.generation_context or {}
                res = replay.check_write_replay(ctx)
                out["replay"].append({
                    "run_id": run_id, "chapter_no": int(run.chapter_no),
                    "executor": ctx.get("executor"), "harness_id": ctx.get("harness_id"),
                    "harness_hash": ctx.get("harness_hash"),
                    "overlay_id": ctx.get("harness_overlay_id") or "",
                    "overlay_keys": sorted((ctx.get("harness_overlay") or {}).keys()),
                    **res,
                })

            # 3. 试用判定现状
            entry = await s.get(HarnessLibraryModel, rows[0].id) if rows else None
            if entry is not None:
                outcome = await overlay.trial_outcome(s, entry)
                out["trial"] = {
                    "status": entry.status, "since_chapter": entry.since_chapter,
                    "baseline": entry.baseline, "outcome": outcome,
                    "passes_now": overlay.trial_passes(outcome, entry.baseline or {}),
                    "trial_chapters_needed": overlay.TRIAL_CHAPTERS,
                }
    finally:
        await engine.dispose()

    # 4. 覆盖层是否进了执笔提示
    from regent.novel.application import directing_chapter as dc
    from regent.novel.harnessing.surface import apply_overrides, load_base_pack

    base = load_base_pack("v1")
    out["prompt_parity"] = {
        "base_write_equals_default": base.get_prompt("write") == dc.CHAPTER_WRITE_SYSTEM,
        "base_write_len": len(base.get_prompt("write")),
        "default_write_len": len(dc.CHAPTER_WRITE_SYSTEM),
    }
    if rows and rows[0].overlay_after:
        layered = apply_overrides(base, rows[0].overlay_after)
        out["prompt_parity"]["overlay_hash"] = layered.content_hash
        out["prompt_parity"]["overlay_write_len"] = len(layered.get_prompt("write"))
        out["prompt_parity"]["overlay_starts_with_default"] = layered.get_prompt("write").startswith(
            dc.CHAPTER_WRITE_SYSTEM.split("\n")[0]
        )
    print("###JSON###" + json.dumps(out, ensure_ascii=False))


asyncio.run(main())
'''


def main() -> int:
    with Remote() as r:
        r.write_text("/opt/regent/sync/h2_server_probe_remote.py", REMOTE_PY)
        r.run("docker cp /opt/regent/sync/h2_server_probe_remote.py regent-api:/tmp/h2probe.py")
        res = r.run("docker exec regent-api python /tmp/h2probe.py", timeout=300)
        raw = res.out
        if "###JSON###" not in raw:
            print("[FAIL] 探针没有输出：", raw[-2000:], res.err[-2000:])
            return 1
        payload = json.loads(raw.split("###JSON###", 1)[1].strip())
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        print("\n===== 判定 =====")
        leaked = [
            i for i in payload["search_from_other_book"]["ids"]
        ]
        provisional_global = [
            row["id"] for row in payload["library_rows"]
            if row["status"] == "provisional" and row["scope"] == "global"
        ]
        if leaked and set(leaked) & set(provisional_global):
            print("[风险] 试用中（未过三章判定）的 global 条目会被别的书检索到：", leaked)
        else:
            print("[OK] 别的书检索不到试用中的条目")
        own = payload["search_from_own_book"]["ids"]
        if set(provisional_global) <= set(own):
            print(f"[OK] 本书仍检索得到自己的试用条目：{own}")
        else:
            print(f"[风险] 本书检索不到自己的试用条目：{own} vs {provisional_global}")
        print(f"  试用前基线（含默认臂章节）：{payload['baseline_before_ch5']}")
        for item in payload["replay"]:
            print(f"  回放 ch{item.get('chapter_no')} ok={item.get('ok')} "
                  f"{item.get('reason') or ''} overlay={item.get('overlay_keys')}")
        trial = payload.get("trial") or {}
        print(f"  试用 status={trial.get('status')} since_ch{trial.get('since_chapter')} "
              f"baseline={trial.get('baseline')} outcome={trial.get('outcome')} "
              f"passes_now={trial.get('passes_now')}")
        print(f"  提示词平移一致：{payload['prompt_parity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
