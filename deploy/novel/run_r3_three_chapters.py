"""R3 真机：同一作品连续三章 + 逐章记忆探针 + 一次 FACT 纠错。

用法：
  python deploy/novel/run_r3_three_chapters.py [每章轮询上限秒]

证据落盘：deploy/novel/artifacts/r3/
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402
from run_chapter1 import INTENT, _api, dump_diagnostics, q  # noqa: E402

ART = Path(__file__).resolve().parent / "artifacts" / "r3"
TARGET_CHAPTERS = 3


def _save(name: str, payload: object) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, indent=2)
    )
    path.write_text(text, encoding="utf-8")
    return path


def _resolve_pending(r: Remote, work_id: str, token: str) -> None:
    decs = _api(r, "GET", f"/works/{work_id}/decisions", token=token).get("body") or []
    for item in decs:
        if str(item.get("state") or "").upper() != "PENDING":
            continue
        body = {
            "option_id": item.get("default_option_id"),
            "accept_default": True,
            "confirm_nonce": item.get("confirm_nonce"),
            "client_nonce": f"r3-{secrets.token_hex(4)}",
        }
        script = (
            "import urllib.request, urllib.error, json\n"
            f"url='http://localhost:8000/v1/novel/works/{work_id}/decisions/{item['decision_id']}/resolve'\n"
            f"payload={json.dumps(body, ensure_ascii=False)!r}\n"
            f"token={token!r}\n"
            "req=urllib.request.Request(url, method='POST', data=payload.encode('utf-8'))\n"
            "req.add_header('Content-Type','application/json')\n"
            "req.add_header('Authorization','Bearer '+token)\n"
            "try:\n"
            "    with urllib.request.urlopen(req, timeout=120) as resp:\n"
            "        print(json.dumps({'status': resp.status, 'body': json.loads(resp.read().decode() or '{}')}))\n"
            "except urllib.error.HTTPError as e:\n"
            "    data=e.read().decode()\n"
            "    try: b=json.loads(data)\n"
            "    except Exception: b={'raw': data[:400]}\n"
            "    print(json.dumps({'status': e.code, 'body': b}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'status': 0, 'body': {'error': str(e)[:300]}}))\n"
        )
        r.write_text("/tmp/_r3_resolve.py", script)
        r.run("docker cp /tmp/_r3_resolve.py regent-api:/tmp/_r3_resolve.py", timeout=15)
        raw = r.run("docker exec regent-api python /tmp/_r3_resolve.py", timeout=180).out.strip()
        try:
            res = json.loads(raw)
        except json.JSONDecodeError:
            res = {"status": -1, "body": {"raw": raw[:400]}}
        print(
            f"    裁决默认项 HTTP {res.get('status')} decision={item.get('decision_id')}"
        )


def wait_canonized(
    r: Remote,
    *,
    work_id: str,
    token: str,
    chapter_no: int,
    limit: int,
) -> tuple[str, dict]:
    deadline = time.time() + limit
    last = ""
    while time.time() < deadline:
        time.sleep(20)
        resp = _api(r, "GET", f"/works/{work_id}/runs", token=token)
        prog = resp.get("body", {}) or {}
        # 重启/竞态时可能拿到空壳或错页：跳过本轮，不误判终态。
        if int(resp.get("status") or 0) not in (200, 202) or not isinstance(prog, dict):
            print(f"    {time.strftime('%H:%M:%S')} poll_skip status={resp.get('status')}")
            continue
        if "state" not in prog and "current_step" not in prog:
            print(f"    {time.strftime('%H:%M:%S')} poll_skip empty_body keys={list(prog.keys())[:6]}")
            continue
        state = prog.get("state", "?")
        step = prog.get("current_step", "")
        ch = prog.get("chapter_no")
        done = [k for k, v in (prog.get("steps") or {}).items() if v == "SUCCEEDED"]
        tag = f"ch{ch} {state}/{step}/{len(done)}步"
        if tag != last:
            print(f"    {time.strftime('%H:%M:%S')} {tag}")
            last = tag
        if state == "PENDING_DECISION":
            _resolve_pending(r, work_id, token)
            continue
        if state == "CANONIZED":
            if ch is not None and int(ch) != int(chapter_no):
                # 偶发读到旧 progress；再等一轮
                continue
            # 二次确认：避免进度接口短暂脏读（曾出现 ch2 CANONIZED 但 DB 仍 RUNNING、字数 0）
            confirm = q(
                r,
                "SELECT state, coalesce(word_count,0)::text "
                "FROM novel_chapter_runs "
                f"WHERE work_id='{work_id}' AND chapter_no={int(chapter_no)} "
                "ORDER BY attempt DESC LIMIT 1",
            ).strip()
            db_state, _, db_wc = confirm.partition("|")
            if db_state != "CANONIZED" or int(db_wc or "0") <= 0:
                print(
                    f"    {time.strftime('%H:%M:%S')} canon_skip api=CANONIZED db={confirm or '?'}"
                )
                continue
            return state, prog
        if state in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
            # 刚 regenerate 后偶发仍读到旧 TERMINAL；章节号不对则忽略
            if ch is not None and int(ch) != int(chapter_no):
                continue
            confirm = q(
                r,
                "SELECT state FROM novel_chapter_runs "
                f"WHERE work_id='{work_id}' AND chapter_no={int(chapter_no)} "
                "ORDER BY attempt DESC LIMIT 1",
            ).strip()
            if confirm and confirm != state:
                print(
                    f"    {time.strftime('%H:%M:%S')} fail_skip api={state} db={confirm}"
                )
                continue
            return state, prog
    return "TIMEOUT", {"last": last}


def memory_snapshot(r: Remote, work_id: str, chapter_no: int) -> dict:
    items_raw = q(
        r,
        "SELECT source_chapter_no::text, kind, state, item_key, "
        "COALESCE(NULLIF(resolved_chapter_no,0)::text,''), "
        "COALESCE(invalidated_at::text,''), left(content, 100) "
        f"FROM novel_memory_items WHERE work_id='{work_id}' "
        "ORDER BY source_chapter_no, kind, item_key",
    )
    edges = q(
        r,
        f"SELECT count(*)::text FROM novel_memory_edges WHERE work_id='{work_id}'",
    )
    by_ch = q(
        r,
        "SELECT source_chapter_no::text, count(*)::text "
        f"FROM novel_memory_items WHERE work_id='{work_id}' "
        "GROUP BY 1 ORDER BY 1",
    )
    run_meta = q(
        r,
        "SELECT id::text, state, chapter_no::text, "
        "COALESCE(generation_context->'memory' IS NOT NULL, false)::text "
        f"FROM novel_chapter_runs WHERE work_id='{work_id}' "
        f"AND chapter_no={int(chapter_no)} "
        "ORDER BY created_at DESC LIMIT 1",
    )
    memory_keys: list[str] = []
    memory_kinds: list[str] = []
    open_promises = 0
    resolved = 0
    ch1_in_ctx = False
    if run_meta:
        run_id = run_meta.split("|", 1)[0]
        # Pull compact memory payload from generation_context
        blob = q(
            r,
            "SELECT generation_context->'memory' "
            f"FROM novel_chapter_runs WHERE id='{run_id}'",
        )
        if blob and blob not in ("", "null"):
            try:
                mem = json.loads(blob)
            except json.JSONDecodeError:
                mem = []
            # assemble 写入的是 MemoryBundle.as_payload() → list[dict]
            items: list = []
            if isinstance(mem, list):
                items = mem
            elif isinstance(mem, dict):
                items = mem.get("items") or mem.get("entries") or []
                if not items:
                    for _k, v in mem.items():
                        if isinstance(v, list):
                            items.extend(x for x in v if isinstance(x, dict))
            for it in items:
                if not isinstance(it, dict):
                    continue
                memory_keys.append(str(it.get("key") or ""))
                memory_kinds.append(str(it.get("kind") or ""))
                src = it.get("source_chapter_no")
                try:
                    src_i = int(src) if src is not None else None
                except (TypeError, ValueError):
                    src_i = None
                if src_i == 1 and chapter_no >= 2:
                    ch1_in_ctx = True
                if str(it.get("kind")) == "promise" and str(it.get("state")) == "OPEN":
                    open_promises += 1
                if str(it.get("state")) == "RESOLVED":
                    resolved += 1

    rows = []
    for line in (items_raw or "").splitlines():
        parts = line.split("|", 6)
        if len(parts) < 7:
            continue
        rows.append(
            {
                "source_chapter_no": int(parts[0]) if parts[0].isdigit() else parts[0],
                "kind": parts[1],
                "state": parts[2],
                "key": parts[3],
                "resolved_chapter_no": parts[4] or None,
                "invalidated_at": parts[5] or None,
                "content_preview": parts[6],
            }
        )
    count = len(rows)
    promise_rows = [x for x in rows if x["kind"] == "promise"]
    resolved_promises = [x for x in promise_rows if x["state"] == "RESOLVED"]
    # DB-level: chapter1 items exist when chapter>=2
    ch1_db = [x for x in rows if x["source_chapter_no"] == 1]
    if chapter_no >= 2 and ch1_db:
        # even if context parse failed, note DB survival
        pass
    return {
        "chapter_no": chapter_no,
        "item_count": count,
        "edge_count": int(edges or "0"),
        "by_chapter": by_ch,
        "items": rows,
        "run_meta": run_meta,
        "context_memory_keys_sample": memory_keys[:20],
        "context_memory_kinds_sample": memory_kinds[:20],
        "context_has_chapter1_item": ch1_in_ctx or (chapter_no >= 2 and bool(ch1_db) and bool(memory_keys)),
        "context_parse_note": (
            "ch1 keys present in ctx"
            if ch1_in_ctx
            else (
                "ch1 items in DB; ctx keys empty/unparsed"
                if chapter_no >= 2 and ch1_db
                else "n/a"
            )
        ),
        "promise_count": len(promise_rows),
        "resolved_promise_count": len(resolved_promises),
        "open_promises_in_ctx": open_promises,
        "gate_items_nonzero": count > 0,
    }


def pick_correction_statement(snap: dict) -> tuple[str, str]:
    """Pick a FACT statement + subject from chapter-1 memory."""
    for item in snap.get("items") or []:
        if item.get("source_chapter_no") != 1:
            continue
        if item.get("invalidated_at"):
            continue
        content = (item.get("content_preview") or "").strip()
        key = (item.get("key") or "").strip()
        # subject: first entity-like token from key (often persona:xxx)
        subject = ""
        if ":" in key:
            subject = key.split(":", 1)[-1].split("/")[0]
        if content:
            return content[:200], subject
        if key:
            return f"关于{key}的既有记忆需要纠正", subject
    return "第一章已确立的关键事实需要纠正", ""


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
    ART.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"chapters": [], "correction": None, "work_id": None}

    with Remote() as r:
        auth = _api(
            r,
            "POST",
            "/auth/session",
            params=f"subject=r3-{secrets.token_hex(4)}&display_name=R3",
        )
        token = auth.get("body", {}).get("token", "")
        if not token:
            print("[FAIL] token", auth)
            return 1
        print("[1] 已认证")

        work = _api(
            r,
            "POST",
            "/works",
            token=token,
            body={
                "raw_intent": INTENT,
                "client_nonce": f"r3-{secrets.token_hex(4)}",
            },
        )
        work_id = work.get("body", {}).get("work_id", "")
        if not work_id:
            print("[FAIL] 建作品", work)
            return 1
        evidence["work_id"] = work_id
        print(f"[2] 作品 {work_id}")

        clarify = _api(
            r,
            "POST",
            f"/works/{work_id}/clarify",
            token=token,
            body={
                "answers": {
                    "genre": "现实主义魔幻",
                    "tone": "温暖而悬疑",
                    "length": "中篇",
                }
            },
            timeout=600,
        )
        cards = clarify.get("body", {}).get("directions") or []
        if not cards:
            print("[FAIL] 无方向卡", json.dumps(clarify, ensure_ascii=False)[:500])
            return 1
        card_id = cards[0].get("card_id") or cards[0].get("id")
        print(f"[3] 方向卡 {card_id}")

        conf = _api(
            r,
            "POST",
            f"/works/{work_id}/directions",
            token=token,
            body={"card_id": card_id, "client_nonce": f"r3-{secrets.token_hex(4)}"},
            timeout=1800,
        )
        print(f"[4] 方向确认 HTTP {conf.get('status')}")
        if int(conf.get("status") or 0) < 200 or int(conf.get("status") or 0) >= 400:
            print(json.dumps(conf, ensure_ascii=False)[:600])
            return 1

        lock = _api(
            r,
            "POST",
            f"/works/{work_id}/world-bible/lock",
            token=token,
            body={"client_nonce": f"r3-lock-{secrets.token_hex(4)}"},
            timeout=180,
        )
        print(f"[4b] 锁定世界书 HTTP {lock.get('status')}")
        if int(lock.get("status") or 0) < 200 or int(lock.get("status") or 0) >= 400:
            print(json.dumps(lock, ensure_ascii=False)[:600])
            return 1

        for chapter_no in range(1, TARGET_CHAPTERS + 1):
            print(f"\n===== 第 {chapter_no} 章 =====")
            started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={})
            print(f"  起章 HTTP {started.get('status')} body_keys={list((started.get('body') or {}).keys())}")
            if started.get("status", 0) >= 400:
                print(json.dumps(started, ensure_ascii=False)[:800])
                _save("evidence.json", evidence)
                return 1

            state, prog = wait_canonized(
                r, work_id=work_id, token=token, chapter_no=chapter_no, limit=limit
            )
            ch_body = _api(
                r, "GET", f"/works/{work_id}/chapters/{chapter_no}", token=token
            ).get("body", {})
            snap = memory_snapshot(r, work_id, chapter_no)
            entry = {
                "chapter_no": chapter_no,
                "state": state,
                "word_count": ch_body.get("word_count"),
                "progress": {
                    "state": prog.get("state"),
                    "step": prog.get("current_step"),
                    "chapter_no": prog.get("chapter_no"),
                    "run_id": prog.get("run_id"),
                },
                "memory": snap,
            }
            evidence["chapters"].append(entry)
            _save(f"ch{chapter_no}_memory.json", snap)
            _save("evidence.json", evidence)
            print(
                f"  终态={state} 字数={ch_body.get('word_count')} "
                f"memory_items={snap['item_count']} edges={snap['edge_count']} "
                f"promises={snap['promise_count']} resolved={snap['resolved_promise_count']}"
            )
            if state != "CANONIZED":
                print(f"[FAIL] 第 {chapter_no} 章未 CANONIZED")
                dump_diagnostics(r, work_id)
                return 1
            if not snap["gate_items_nonzero"]:
                print("[FAIL] R3 否决：CANON 后 novel_memory_items 仍为 0")
                dump_diagnostics(r, work_id)
                return 1
            if chapter_no >= 2:
                ch1_alive = any(
                    x.get("source_chapter_no") == 1 for x in snap.get("items") or []
                )
                entry["memory"]["chapter1_items_survive"] = ch1_alive
                if not ch1_alive:
                    print("[FAIL] 章 1 记忆未跨章存活")
                    return 1
            time.sleep(2)

        # --- correction after chapter 3 ---
        print("\n===== FACT 纠错 =====")
        first = evidence["chapters"][0]["memory"]
        statement, subject = pick_correction_statement(first)
        report_body = {
            "statement": statement,
            "chapter_no": 1,
            "kind": "FACT",
            "client_nonce": f"r3-corr-{secrets.token_hex(4)}",
            "subject": subject,
        }
        report = _api(
            r,
            "POST",
            f"/works/{work_id}/facts/report",
            token=token,
            body=report_body,
            timeout=180,
        )
        print(f"  report HTTP {report.get('status')}")
        print(json.dumps(report.get("body"), ensure_ascii=False)[:800])
        corr: dict = {
            "request": report_body,
            "response": report,
        }
        body = report.get("body") or {}
        actions = body.get("available_actions") or []
        if "resume_then_replay" in actions:
            resume = _api(
                r,
                "POST",
                f"/works/{work_id}/resume-correction",
                token=token,
                body={"ticket_id": body.get("ticket_id") or ""},
                timeout=120,
            )
            print(f"  resume HTTP {resume.get('status')}")
            corr["resume"] = resume
            # wait a bit for queued replay
            time.sleep(30)
            prog = _api(r, "GET", f"/works/{work_id}/runs", token=token).get("body", {})
            corr["after_resume_progress"] = prog
            if prog.get("state") in ("QUEUED", "RUNNING", "PENDING_DECISION"):
                st, p2 = wait_canonized(
                    r,
                    work_id=work_id,
                    token=token,
                    chapter_no=int(prog.get("chapter_no") or 1),
                    limit=limit,
                )
                corr["replay_final_state"] = st
                corr["replay_progress"] = p2

        # invalidate probe
        inv = q(
            r,
            "SELECT count(*)::text FROM novel_memory_items "
            f"WHERE work_id='{work_id}' AND invalidated_at IS NOT NULL",
        )
        corr["invalidated_count"] = int(inv or "0")
        corr["post_memory"] = memory_snapshot(r, work_id, 3)
        evidence["correction"] = corr
        _save("correction.json", corr)
        _save("evidence.json", evidence)

        if not body.get("accepted"):
            print("[FAIL] facts/report 未受理")
            return 1
        if not (body.get("affected_chapters") or body.get("replay_scope")):
            print("[WARN] 无 affected_chapters/replay_scope，仍记入证据")
        print(
            f"[OK] R3 三章完成 work={work_id} "
            f"replay_scope={body.get('replay_scope')} "
            f"invalidated={corr['invalidated_count']}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
