"""R3 续跑：在已有 CANONIZED 作品上从 latest+1 跑到第 3 章，再 FACT 纠错。

用法：
  python deploy/novel/run_r3_continue.py <work_id> [每章上限秒]
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402
from run_chapter1 import _api, dump_diagnostics, q  # noqa: E402
from run_r3_three_chapters import (  # noqa: E402
    ART,
    _save,
    memory_snapshot,
    pick_correction_statement,
    wait_canonized,
)

TARGET = 3


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_r3_continue.py <work_id> [limit]")
        return 2
    work_id = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
    ART.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"mode": "continue", "work_id": work_id, "chapters": [], "correction": None}

    with Remote() as r:
        subject = q(
            r,
            "SELECT p.subject FROM novel_principals p "
            "JOIN novel_works w ON w.owner_id=p.id "
            f"WHERE w.id='{work_id}'",
        )
        if not subject:
            print("[FAIL] no subject for work")
            return 1
        auth = _api(
            r,
            "POST",
            "/auth/session",
            params=f"subject={subject}&display_name=R3Cont",
        )
        token = auth.get("body", {}).get("token", "")
        if not token:
            print("[FAIL] token", auth)
            return 1
        latest = int(
            q(r, f"SELECT latest_chapter_no FROM novel_works WHERE id='{work_id}'") or "0"
        )
        print(f"[0] work={work_id} subject={subject} latest={latest}")

        # Snapshot existing chapters
        for ch in range(1, latest + 1):
            snap = memory_snapshot(r, work_id, ch)
            evidence["chapters"].append(
                {
                    "chapter_no": ch,
                    "state": "PREEXISTING_CANONIZED",
                    "memory": snap,
                }
            )
            _save(f"cont_ch{ch}_memory.json", snap)
            print(
                f"  preexisting ch{ch}: items={snap['item_count']} "
                f"promises={snap['promise_count']} resolved={snap['resolved_promise_count']}"
            )
            if ch == 1 and not snap["gate_items_nonzero"]:
                print("[FAIL] preexisting ch1 has zero memory items")
                return 1

        for chapter_no in range(latest + 1, TARGET + 1):
            print(f"\n===== 续写第 {chapter_no} 章 =====")
            started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={})
            print(f"  起章 HTTP {started.get('status')}")
            if started.get("status", 0) >= 400:
                print(json.dumps(started, ensure_ascii=False)[:800])
                _save("continue_evidence.json", evidence)
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
            ch1_alive = any(
                x.get("source_chapter_no") == 1 for x in snap.get("items") or []
            )
            entry["memory"]["chapter1_items_survive"] = ch1_alive
            evidence["chapters"].append(entry)
            _save(f"cont_ch{chapter_no}_memory.json", snap)
            _save("continue_evidence.json", evidence)
            print(
                f"  终态={state} 字数={ch_body.get('word_count')} "
                f"items={snap['item_count']} edges={snap['edge_count']} "
                f"ch1_survive={ch1_alive} ctx_ch1={snap.get('context_has_chapter1_item')} "
                f"promises={snap['promise_count']} resolved={snap['resolved_promise_count']}"
            )
            if state != "CANONIZED":
                print(f"[FAIL] 第 {chapter_no} 章未 CANONIZED")
                dump_diagnostics(r, work_id)
                return 1
            if not snap["gate_items_nonzero"]:
                print("[FAIL] CANON 后 memory_items=0")
                return 1
            if not ch1_alive:
                print("[FAIL] 章1记忆未跨章存活")
                return 1
            time.sleep(2)

        print("\n===== FACT 纠错 =====")
        # Prefer chapter-1 snapshot from evidence
        first_mem = next(
            (c["memory"] for c in evidence["chapters"] if c["chapter_no"] == 1),
            memory_snapshot(r, work_id, 1),
        )
        statement, subject = pick_correction_statement(first_mem)
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
        print(json.dumps(report.get("body"), ensure_ascii=False)[:900])
        corr: dict = {"request": report_body, "response": report}
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
            time.sleep(20)
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

        # If await_local_replay, poll active run briefly
        if "await_local_replay" in actions:
            deadline = time.time() + min(limit, 900)
            last = ""
            while time.time() < deadline:
                time.sleep(20)
                prog = _api(r, "GET", f"/works/{work_id}/runs", token=token).get("body", {})
                tag = f"{prog.get('state')}/{prog.get('current_step')}/ch{prog.get('chapter_no')}"
                if tag != last:
                    print(f"    replay poll {tag}")
                    last = tag
                if prog.get("state") == "PENDING_DECISION":
                    from run_r3_three_chapters import _resolve_pending

                    _resolve_pending(r, work_id, token)
                    continue
                if prog.get("state") in (
                    "CANONIZED",
                    "TERMINAL_FAILED",
                    "CANCELLED",
                    "SUPERSEDED",
                ):
                    corr["replay_poll_final"] = prog
                    break
                # If no active replay (still showing prior CANONIZED), stop early
                if prog.get("state") == "CANONIZED" and not prog.get("auto_advance"):
                    corr["replay_poll_final"] = prog
                    break

        inv = q(
            r,
            "SELECT count(*)::text FROM novel_memory_items "
            f"WHERE work_id='{work_id}' AND invalidated_at IS NOT NULL",
        )
        corr["invalidated_count"] = int(inv or "0")
        corr["post_memory"] = memory_snapshot(r, work_id, TARGET)
        evidence["correction"] = corr
        _save("continue_correction.json", corr)
        _save("continue_evidence.json", evidence)

        if report.get("status", 0) >= 400 or not body.get("accepted"):
            print("[FAIL] facts/report 未成功受理")
            return 1
        print(
            f"[OK] R3 continue done work={work_id} "
            f"scope={body.get('replay_scope')} affected={body.get('affected_chapters')} "
            f"invalidated={corr['invalidated_count']}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
