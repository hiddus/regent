"""Queue a new attempt for a TERMINAL_FAILED tip chapter via facts/report."""
from __future__ import annotations

import json
import secrets
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote
from run_chapter1 import _api, q
from run_r3_three_chapters import memory_snapshot, pick_correction_statement, wait_canonized

WID = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 2400


def main() -> int:
    r = Remote()
    subject = q(
        r,
        "SELECT p.subject FROM novel_principals p JOIN novel_works w ON w.owner_id=p.id "
        f"WHERE w.id='{WID}'",
    )
    auth = _api(r, "POST", "/auth/session", params=f"subject={subject}&display_name=R3Retry")
    token = auth.get("body", {}).get("token", "")
    tip = q(
        r,
        "SELECT id::text, chapter_no::text, attempt::text, state "
        f"FROM novel_chapter_runs WHERE work_id='{WID}' "
        "ORDER BY chapter_no DESC, attempt DESC LIMIT 1",
    )
    print("tip", tip)
    latest = int(q(r, f"SELECT latest_chapter_no FROM novel_works WHERE id='{WID}'") or "0")
    print("latest_chapter", latest)
    parts = tip.split("|")
    tip_ch = int(parts[1])
    tip_state = parts[3]
    if tip_state != "TERMINAL_FAILED":
        print("tip not TERMINAL_FAILED; nothing to recover")
        return 0
    # Use chapter-1 memory statement so subjects resolve; report from tip chapter.
    snap = memory_snapshot(r, WID, 1)
    statement, subject_name = pick_correction_statement(snap)
    body = {
        "statement": statement or "需要重做未完成章节以恢复一致性",
        "chapter_no": tip_ch,
        "kind": "FACT",
        "client_nonce": f"r3-retry-{secrets.token_hex(4)}",
        "subject": subject_name,
    }
    report = _api(r, "POST", f"/works/{WID}/facts/report", token=token, body=body, timeout=180)
    print("report", json.dumps(report, ensure_ascii=False)[:1000])
    rb = report.get("body") or {}
    if "resume_then_replay" in (rb.get("available_actions") or []):
        resume = _api(
            r,
            "POST",
            f"/works/{WID}/resume-correction",
            token=token,
            body={"ticket_id": rb.get("ticket_id") or ""},
            timeout=120,
        )
        print("resume", resume.get("status"), json.dumps(resume.get("body"), ensure_ascii=False)[:400])
    # Poll until tip chapter advances or timeout
    deadline = time.time() + LIMIT
    last = ""
    while time.time() < deadline:
        time.sleep(20)
        prog = _api(r, "GET", f"/works/{WID}/runs", token=token).get("body", {}) or {}
        tag = f"{prog.get('state')}/{prog.get('current_step')}/ch{prog.get('chapter_no')}"
        if tag != last:
            print(time.strftime("%H:%M:%S"), tag)
            last = tag
        if prog.get("state") == "PENDING_DECISION":
            from run_r3_three_chapters import _resolve_pending

            _resolve_pending(r, WID, token)
            continue
        if prog.get("state") == "CANONIZED" and int(prog.get("chapter_no") or 0) >= tip_ch:
            snap3 = memory_snapshot(r, WID, tip_ch)
            print("OK CANONIZED", json.dumps({"items": snap3["item_count"], "promises": snap3["promise_count"], "resolved": snap3["resolved_promise_count"]}, ensure_ascii=False))
            return 0
        if prog.get("state") in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
            print("FAIL", prog.get("state"))
            return 1
    print("TIMEOUT", last)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
