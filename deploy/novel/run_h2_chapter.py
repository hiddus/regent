"""Harness² 臂真机验证：让 director_chapter_h2 在真实模型上跑完一章。

前置（缺一不可）：
- 服务器 .env 设了 ``NOVEL_EXECUTOR_CANARY=director_chapter_h2``，比例覆盖目标作品的桶；
- 迁移 0057 已在 Postgres 执行（``novel_harness_library`` 存在）；
- 整树部署已核对（``sync_tree.py``）。

验证四件事，缺一不算通过：
1. **钉住**：起章时写入 executor / harness_id / harness_hash / overlay；
2. **不改行为**：默认配置下首稿执笔提示与载荷跟默认臂一致；
3. **真递归**：CHECK 未过（或追读自评报问题）时走 PROPOSE → PROBE → COMPOSE，
   每次调用经 CallBroker 落账，``logical_call_id`` 带 ``h2`` 前缀且不撞；
4. **入库**：过了硬门交付的轮次写进改进库。

只跑到「首稿一次过、递归未触发」也是有效结论——如实记录，不假装验证过递归。

用法：python deploy/novel/run_h2_chapter.py [轮询上限秒] [章数]
证据：deploy/novel/artifacts/h2/
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402
from run_chapter1 import _api, q  # noqa: E402
from run_r3_three_chapters import _resolve_pending, wait_canonized  # noqa: E402

ART = Path(__file__).resolve().parent / "artifacts" / "h2"
WORK_ID = "064adf0a-6cda-4de4-8126-ed5aee38976f"
# 用作品既有主人的 subject 认证，才能操作这本书（新 subject 会建出新主人）。
OWNER_SUBJECT = "r3-prod-08b72a6a"
OWNER_NAME = "R3Product"
H2_ARM = "director_chapter_h2"


def _save(name: str, payload: object) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )
    path.write_text(text, encoding="utf-8")
    return path


def _rows(r: Remote, sql: str) -> list[list[str]]:
    out = q(r, sql)
    if not out:
        return []
    return [line.split("|") for line in out.splitlines() if line.strip()]


def pinning(r: Remote, run_id: str) -> dict:
    rows = _rows(
        r,
        "SELECT id::text, chapter_no::text, state, current_step, "
        "generation_context->>'executor', generation_context->>'executor_version', "
        "generation_context->>'harness_id', generation_context->>'harness_hash', "
        "coalesce(generation_context->>'harness_overlay_id',''), "
        "coalesce((generation_context->'harness_overlay')::text,'null') "
        f"FROM novel_chapter_runs WHERE id='{run_id}'",
    )
    if not rows:
        return {}
    keys = (
        "run_id", "chapter_no", "state", "current_step", "executor",
        "executor_version", "harness_id", "harness_hash", "harness_overlay_id",
        "harness_overlay",
    )
    return dict(zip(keys, rows[0]))


def production(r: Remote, run_id: str) -> dict:
    rows = _rows(
        r,
        "SELECT coalesce((generation_context->'production')::text,'{}') "
        f"FROM novel_chapter_runs WHERE id='{run_id}'",
    )
    if not rows:
        return {}
    try:
        return json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {"_unparsed": rows[0][0][:2000]}


def calls(r: Remote, run_id: str) -> list[dict]:
    rows = _rows(
        r,
        "SELECT logical_call_id, coalesce(step,''), coalesce(purpose,''), status, "
        "coalesce(input_tokens,0)::text, coalesce(output_tokens,0)::text, "
        "coalesce(actual_amount_minor,0)::text, coalesce(prompt_hash,''), "
        "coalesce(error_code,'') "
        f"FROM novel_model_calls WHERE run_id='{run_id}' ORDER BY created_at",
    )
    keys = (
        "logical_call_id", "step", "purpose", "status", "input_tokens",
        "output_tokens", "amount_minor", "prompt_hash", "error_code",
    )
    return [dict(zip(keys, row)) for row in rows]


def steps(r: Remote, run_id: str) -> list[dict]:
    # 按协议顺序排，不按 id：步骤行是同一次 flush 写进去的，UUID 顺序是随机的，
    # 拿 id 排会把「步骤链」显示成乱序，看着像路由错了。
    rows = _rows(
        r,
        "SELECT step, state, attempt::text, coalesce(error_code,'') "
        "FROM novel_chapter_steps WHERE run_id='" + run_id + "' "
        "ORDER BY CASE step WHEN 'ASSEMBLE' THEN 1 WHEN 'PRODUCE' THEN 2 "
        "WHEN 'REVIEW' THEN 3 WHEN 'CANON' THEN 4 ELSE 9 END",
    )
    keys = ("step", "state", "attempt", "error_code")
    return [dict(zip(keys, row)) for row in rows]


def library(r: Remote, work_id: str) -> list[dict]:
    rows = _rows(
        r,
        "SELECT id::text, scope, coalesce(scope_key,''), status, coalesce(failure_code,''), "
        "coalesce(chapter_role,''), coalesce(genre_tag,''), coalesce(harness_hash,''), "
        "adopted_count::text, effective_count::text, "
        "coalesce((config_diff)::text,'{}'), left(coalesce(experience,''),300), "
        "coalesce(source_run_id::text,''), created_at::text "
        f"FROM novel_harness_library WHERE source_work_id='{work_id}' ORDER BY created_at",
    )
    keys = (
        "id", "scope", "scope_key", "status", "failure_code", "chapter_role",
        "genre_tag", "harness_hash", "adopted_count", "effective_count",
        "config_diff", "experience", "source_run_id", "created_at",
    )
    return [dict(zip(keys, row)) for row in rows]


def cost(r: Remote, run_id: str) -> str:
    return q(
        r,
        "SELECT coalesce(sum(actual_amount_minor),0)::text || ' minor / ' "
        "|| coalesce(sum(n),0)::text || ' calls (' || "
        "coalesce(string_agg(status || 'x' || n, ', ' order by status),'') || ')' "
        "FROM (SELECT status, count(*) AS n, sum(actual_amount_minor) AS actual_amount_minor "
        f"FROM novel_model_calls WHERE run_id='{run_id}' GROUP BY status) t",
    )


def check_pinning(pin: dict) -> list[str]:
    """钉住不完整就是灰度没生效——这里不许「大概对」。"""
    fails: list[str] = []
    if pin.get("executor") != H2_ARM:
        fails.append(f"executor={pin.get('executor')!r} 不是 {H2_ARM}")
    if pin.get("harness_id") != "base/v1":
        fails.append(f"harness_id={pin.get('harness_id')!r} 不是 base/v1")
    if not pin.get("harness_hash"):
        fails.append("harness_hash 为空")
    return fails


def summarize(label: str, evidence: dict) -> None:
    pin = evidence["pinning"]
    prod = evidence["production"]
    cs = evidence["calls"]
    h2 = [c for c in cs if ":h2:" in c["logical_call_id"]]
    print(f"\n===== {label} =====")
    print(f"  运行 {pin.get('run_id')} 第 {pin.get('chapter_no')} 章 "
          f"{evidence['final_state']} 字数 {evidence.get('word_count')}")
    print(f"  执行器 {pin.get('executor')} / {pin.get('executor_version')}")
    print(f"  配置包 {pin.get('harness_id')} hash={pin.get('harness_hash')} "
          f"overlay_id={pin.get('harness_overlay_id') or '(无)'}")
    print(f"  阶段 {prod.get('phase')} 递归轮 {prod.get('h2_round') or 0} "
          f"触发 {prod.get('h2_trigger') or '(未触发)'}")
    print(f"  调用 {len(cs)} 次，其中递归 {len(h2)} 次；成本 {evidence.get('cost')}")
    for c in h2:
        print(f"    - {c['logical_call_id']} {c['status']} "
              f"{c['input_tokens']}+{c['output_tokens']}tok {c['amount_minor']}minor")
    print(f"  改稿 {prod.get('repairs') or 0} 次；检查未过 "
          f"{len(prod.get('check_fails') or [])} 条")
    if prod.get("h2_compose"):
        dec = prod["h2_compose"]
        print(f"  组合决定 {dec.get('decision')} 交付 {dec.get('deliver') or '?'}")
    lib = evidence["library"]
    print(f"  改进库条目 {len(lib)} 条")
    for item in lib:
        print(f"    - {item['status']}/{item['scope']} {item['failure_code']} "
              f"adopted={item['adopted_count']} effective={item['effective_count']}")


def run_one(r: Remote, token: str, work_id: str, limit: int) -> dict:
    before = q(
        r,
        "SELECT coalesce(max(chapter_no),0)::text FROM novel_chapter_runs "
        f"WHERE work_id='{work_id}'",
    ).strip()
    next_ch = int(before or "0") + 1
    print(f"\n[起章] 第 {next_ch} 章（灰度桶内应落 {H2_ARM}）")
    started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={}, timeout=180)
    if int(started.get("status") or 0) >= 400:
        print("[FAIL] 起章失败:", json.dumps(started, ensure_ascii=False)[:800])
        return {"final_state": "START_FAILED", "response": started}
    run_id = str(started.get("body", {}).get("run_id") or "")
    if not run_id:
        rows = _rows(
            r,
            "SELECT id::text FROM novel_chapter_runs WHERE work_id="
            f"'{work_id}' AND chapter_no={next_ch} ORDER BY attempt DESC LIMIT 1",
        )
        run_id = rows[0][0] if rows else ""
    print(f"  run={run_id}")

    pin = pinning(r, run_id)
    fails = check_pinning(pin)
    if fails:
        print("[FAIL] 钉住不完整：")
        for f in fails:
            print("   -", f)
        _save(f"ch{next_ch}_pinning_failed.json", {"pinning": pin, "fails": fails})
        return {"final_state": "PIN_FAILED", "pinning": pin, "fails": fails}
    print(f"  [OK] 钉住 {pin['executor']} {pin['harness_id']} hash={pin['harness_hash']}")
    print(f"  步骤链 {[s['step'] for s in steps(r, run_id)]}")

    state, prog = wait_canonized(
        r, work_id=work_id, token=token, chapter_no=next_ch, limit=limit
    )
    print(f"  终态 {state}")

    prod = production(r, run_id)
    evidence = {
        "work_id": work_id,
        "chapter_no": next_ch,
        "final_state": state,
        "pinning": pinning(r, run_id),
        "steps": steps(r, run_id),
        "calls": calls(r, run_id),
        "cost": cost(r, run_id),
        "word_count": q(
            r,
            "SELECT coalesce(word_count,0)::text FROM novel_chapter_runs "
            f"WHERE id='{run_id}'",
        ).strip(),
        "production": prod,
        "library": library(r, work_id),
        "api_progress": prog,
    }
    _save(f"ch{next_ch}_evidence.json", evidence)
    _save(f"ch{next_ch}_production.json", prod)
    summarize(f"第 {next_ch} 章", evidence)
    if state == "PENDING_DECISION":
        _resolve_pending(r, work_id, token)
    return evidence


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
    chapters = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    with Remote() as r:
        pre = q(
            r,
            "SELECT count(*)::text FROM information_schema.tables "
            "WHERE table_name='novel_harness_library'",
        ).strip()
        if pre != "1":
            print("[FAIL] 服务器上没有 novel_harness_library，先跑迁移 0057")
            return 1
        # 灰度配置在容器 env 里，SQL 读不到——直接在 API 容器里问 executor 模块，
        # 顺便确认这本书真的落在灰度桶内（否则跑出来的是默认臂，白跑）。
        env = r.run(
            "docker exec regent-api python -c \""
            "from regent.novel.application import executor as ex;"
            "print(ex.canary_executor(), ex.canary_percent(), ex.choose_executor('%s'))\"" % WORK_ID,
            timeout=120,
        ).out.strip()
        print(f"[灰度] {env}")
        if H2_ARM not in env:
            print(f"[FAIL] 目标作品没落进灰度桶，无法验证：{env}")
            return 1

        auth = _api(
            r, "POST", "/auth/session",
            params=f"subject={OWNER_SUBJECT}&display_name={OWNER_NAME}",
        )
        token = str(auth.get("body", {}).get("token") or "")
        if not token:
            print("[FAIL] 拿不到 token:", json.dumps(auth, ensure_ascii=False)[:400])
            return 1
        print(f"[认证] 以 {OWNER_SUBJECT} 登录")

        results = []
        for index in range(chapters):
            evidence = run_one(r, token, WORK_ID, limit)
            results.append(evidence)
            if evidence.get("final_state") in ("START_FAILED", "PIN_FAILED", "TIMEOUT"):
                break
            if index + 1 < chapters:
                time.sleep(5)

        _save(
            f"summary_{secrets.token_hex(3)}.json",
            {
                "work_id": WORK_ID,
                "canary": env,
                "chapters": [
                    {
                        "chapter_no": item.get("chapter_no"),
                        "final_state": item.get("final_state"),
                        "executor": (item.get("pinning") or {}).get("executor"),
                        "h2_calls": [
                            c["logical_call_id"]
                            for c in (item.get("calls") or [])
                            if ":h2:" in c["logical_call_id"]
                        ],
                        "library_entries": len(item.get("library") or []),
                    }
                    for item in results
                ],
            },
        )
        print(f"\n证据落盘：{ART}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
