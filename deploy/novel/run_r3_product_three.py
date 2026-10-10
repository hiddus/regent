"""产品入口：一次启动 + continuation policy，跑满三章（非 supervise/逐章 regenerate）。

用法：
  python deploy/novel/run_r3_product_three.py [总轮询上限秒]

证据：deploy/novel/artifacts/r3/product_evidence.json
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402
from run_chapter1 import INTENT, _api, dump_diagnostics, q  # noqa: E402
from run_r3_three_chapters import (  # noqa: E402
    ART,
    TARGET_CHAPTERS,
    _resolve_pending,
    _save,
    memory_snapshot,
)

MODE = "product_continuation"


def wait_product_chapters(
    r: Remote,
    *,
    work_id: str,
    token: str,
    target: int,
    limit: int,
) -> dict:
    """Poll until latest_chapter_no >= target with CANONIZED + words, or fail."""
    deadline = time.time() + limit
    last = ""
    evidence_chapters: dict[int, dict] = {}
    while time.time() < deadline:
        time.sleep(20)
        # resolve any pending decisions so auto-continue can proceed
        _resolve_pending(r, work_id, token)

        latest = int(
            q(r, f"SELECT coalesce(latest_chapter_no,0) FROM novel_works WHERE id='{work_id}'")
            or "0"
        )
        prog = _api(r, "GET", f"/works/{work_id}/runs", token=token)
        body = prog.get("body") or {}
        state = body.get("state", "?")
        step = body.get("current_step", "")
        ch = body.get("chapter_no")
        cont = _api(r, "GET", f"/works/{work_id}/continuation", token=token).get("body") or {}

        # per-chapter DB snapshot
        rows = q(
            r,
            "SELECT chapter_no::text, state, coalesce(word_count,0)::text, attempt::text "
            f"FROM novel_chapter_runs WHERE work_id='{work_id}' "
            "AND state not in ('CANCELLED','SUPERSEDED') "
            "ORDER BY chapter_no, attempt DESC",
        )
        by_ch: dict[int, tuple[str, int, int]] = {}
        for line in (rows or "").splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            cno = int(parts[0])
            if cno in by_ch:
                continue  # already have latest attempt
            by_ch[cno] = (parts[1], int(parts[2] or 0), int(parts[3] or 0))

        canonized = [
            c
            for c, (st, wc, _) in by_ch.items()
            if st == "CANONIZED" and wc > 0
        ]
        tag = (
            f"latest={latest} api={ch}/{state}/{step} "
            f"canon={sorted(canonized)} cont_en={cont.get('enabled')} "
            f"tgt={cont.get('target_chapter_no')}"
        )
        if tag != last:
            print(f"    {time.strftime('%H:%M:%S')} {tag}")
            last = tag

        for cno in sorted(by_ch):
            st, wc, att = by_ch[cno]
            if st == "CANONIZED" and wc > 0 and cno not in evidence_chapters:
                snap = memory_snapshot(r, work_id, cno)
                evidence_chapters[cno] = {
                    "chapter_no": cno,
                    "state": st,
                    "word_count": wc,
                    "attempt": att,
                    "memory": snap,
                }
                _save(f"product_ch{cno}_memory.json", snap)
                print(
                    f"  [ch{cno}] CANONIZED words={wc} items={snap['item_count']} "
                    f"promises={snap['promise_count']}"
                )

        if len(canonized) >= target and all(c in canonized for c in range(1, target + 1)):
            return {
                "ok": True,
                "chapters": [evidence_chapters[c] for c in range(1, target + 1)],
                "final_progress": body,
                "continuation": cont,
            }

        # hard fail if any chapter TERMINAL without regen path (product: no auto regen)
        for cno, (st, wc, att) in by_ch.items():
            if st in ("TERMINAL_FAILED",) and cno <= target and cno not in canonized:
                # wait a bit in case continuation/retry kicks; if stuck > one poll cycle after fail
                live = q(
                    r,
                    "SELECT state FROM novel_chapter_runs "
                    f"WHERE work_id='{work_id}' AND chapter_no={cno} "
                    "AND state in ('RUNNING','QUEUED','RETRYABLE_FAILED') "
                    "LIMIT 1",
                )
                if live:
                    continue
                err = q(
                    r,
                    "SELECT coalesce(error_code,'') || '|' || left(coalesce(error_message,''),300) "
                    f"FROM novel_chapter_runs WHERE work_id='{work_id}' AND chapter_no={cno} "
                    "ORDER BY attempt DESC LIMIT 1",
                )
                return {
                    "ok": False,
                    "reason": f"chapter {cno} TERMINAL_FAILED: {err}",
                    "chapters": list(evidence_chapters.values()),
                    "final_progress": body,
                    "continuation": cont,
                }

    return {
        "ok": False,
        "reason": "TIMEOUT",
        "chapters": list(evidence_chapters.values()),
        "final_progress": {},
        "continuation": {},
    }


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 7200
    ART.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "mode": MODE,
        "work_id": None,
        "git_note": "hot-synced novel tree before run",
        "chapters": [],
        "continuation_set": None,
        "start_runs_count": 0,
    }

    with Remote() as r:
        auth = _api(
            r,
            "POST",
            "/auth/session",
            params=f"subject=r3-prod-{secrets.token_hex(4)}&display_name=R3Product",
        )
        token = (auth.get("body") or {}).get("token") or ""
        if not token:
            print("[FAIL] auth", auth)
            return 1
        print("[1] 已认证")

        work = _api(
            r,
            "POST",
            "/works",
            token=token,
            body={
                "raw_intent": INTENT,
                "client_nonce": f"r3p-{secrets.token_hex(4)}",
            },
        )
        work_id = (work.get("body") or {}).get("work_id") or ""
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
        cards = (clarify.get("body") or {}).get("directions") or []
        if not cards:
            print("[FAIL] 无方向卡", json.dumps(clarify, ensure_ascii=False)[:500])
            _save("product_evidence.json", evidence)
            return 1
        card_id = cards[0].get("card_id") or cards[0].get("id")
        print(f"[3] 方向卡 {card_id}")

        conf = _api(
            r,
            "POST",
            f"/works/{work_id}/directions",
            token=token,
            body={"card_id": card_id, "client_nonce": f"r3p-{secrets.token_hex(4)}"},
            timeout=1800,
        )
        print(f"[4] 方向确认 HTTP {conf.get('status')}")
        if int(conf.get("status") or 0) < 200 or int(conf.get("status") or 0) >= 400:
            print(json.dumps(conf, ensure_ascii=False)[:600])
            _save("product_evidence.json", evidence)
            return 1

        lock = _api(
            r,
            "POST",
            f"/works/{work_id}/world-bible/lock",
            token=token,
            body={"client_nonce": f"r3p-lock-{secrets.token_hex(4)}"},
            timeout=180,
        )
        print(f"[4b] 锁定世界书 HTTP {lock.get('status')}")
        if int(lock.get("status") or 0) < 200 or int(lock.get("status") or 0) >= 400:
            print(json.dumps(lock, ensure_ascii=False)[:600])
            _save("product_evidence.json", evidence)
            return 1

        # Product: enable continuation to chapter 3 BEFORE first start
        cont = _api(
            r,
            "PUT",
            f"/works/{work_id}/continuation",
            token=token,
            body={
                "enabled": True,
                "target_chapter_no": TARGET_CHAPTERS,
                "volume_scope": "current",
            },
        )
        evidence["continuation_set"] = cont
        print(
            f"[5] continuation HTTP {cont.get('status')} "
            f"body={json.dumps(cont.get('body'), ensure_ascii=False)[:300]}"
        )
        if int(cont.get("status") or 0) not in (200, 202):
            _save("product_evidence.json", evidence)
            return 1

        # Single product start — do NOT loop POST /runs per chapter
        started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={})
        evidence["start_runs_count"] = 1
        evidence["start_response"] = {
            "status": started.get("status"),
            "body_keys": list((started.get("body") or {}).keys()),
        }
        print(f"[6] 一次起章 HTTP {started.get('status')}")
        if int(started.get("status") or 0) >= 400:
            print(json.dumps(started, ensure_ascii=False)[:800])
            _save("product_evidence.json", evidence)
            return 1

        print(f"[7] 等待产品自动续写至第 {TARGET_CHAPTERS} 章 (limit={limit}s)")
        result = wait_product_chapters(
            r, work_id=work_id, token=token, target=TARGET_CHAPTERS, limit=limit
        )
        evidence["chapters"] = result.get("chapters") or []
        evidence["wait_result"] = {
            "ok": result.get("ok"),
            "reason": result.get("reason"),
            "final_progress": result.get("final_progress"),
            "continuation": result.get("continuation"),
        }
        _save("product_evidence.json", evidence)

        if not result.get("ok"):
            print(f"[FAIL] {result.get('reason')}")
            dump_diagnostics(r, work_id)
            return 1

        # Gates
        for entry in evidence["chapters"]:
            snap = entry.get("memory") or {}
            if not snap.get("gate_items_nonzero"):
                print(f"[FAIL] ch{entry['chapter_no']} memory items == 0")
                return 1
        if len(evidence["chapters"]) >= 2:
            ch2 = evidence["chapters"][1]
            alive = any(
                x.get("source_chapter_no") == 1
                for x in (ch2.get("memory") or {}).get("items") or []
            )
            evidence["chapter1_items_survive_at_ch2"] = alive
            if not alive:
                print("[FAIL] 章1记忆未跨章存活")
                _save("product_evidence.json", evidence)
                return 1

        # Prove we did not manually start ch2/ch3
        start_count = q(
            r,
            "SELECT count(*)::text FROM novel_chapter_runs "
            f"WHERE work_id='{work_id}' AND attempt=1",
        )
        evidence["attempt1_run_count"] = int(start_count or "0")
        _save("product_evidence.json", evidence)

        words = [c.get("word_count") for c in evidence["chapters"]]
        print(
            f"[OK] 产品入口三章完成 work={work_id} words={words} "
            f"mode={MODE} start_runs={evidence['start_runs_count']}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
