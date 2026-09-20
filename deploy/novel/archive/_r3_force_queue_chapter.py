"""Force-queue attempt N+1 for a failed chapter tip (ops recovery for R3)."""
from __future__ import annotations

import json
import secrets
import shlex
import sys
import time
import uuid

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote
from run_chapter1 import _api, q
from run_r3_three_chapters import _resolve_pending, memory_snapshot

WID = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 2400


def main() -> int:
    r = Remote()
    tip = q(
        r,
        "SELECT id::text, chapter_no::text, attempt::text, state, branch_id::text "
        f"FROM novel_chapter_runs WHERE work_id='{WID}' "
        "ORDER BY chapter_no DESC, attempt DESC LIMIT 1",
    )
    print("tip", tip)
    run_id, ch, attempt, state, branch = tip.split("|")
    ch_i, att_i = int(ch), int(attempt)
    if state != "TERMINAL_FAILED":
        print("not failed tip")
        return 0
    # Mark old tip cancelled so progress/UI aren't confused; new attempt carries work.
    print(
        q(
            r,
            f"UPDATE novel_chapter_runs SET state='CANCELLED', updated_at=now() "
            f"WHERE id='{run_id}' RETURNING id::text, state",
        )
    )
    # Copy generation_context from the cancelled tip as starting point for replay.
    # Worker expects a QUEUED run with steps; simplest is to clone via API report with
    # a known cast subject. Fetch persona names:
    personas = q(
        r,
        f"SELECT name FROM novel_personas WHERE work_id='{WID}' ORDER BY name LIMIT 5",
    )
    print("personas", personas)
    subject_name = (personas.splitlines() or ["主角"])[0].strip() or "主角"
    subject_owner = q(
        r,
        "SELECT p.subject FROM novel_principals p JOIN novel_works w ON w.owner_id=p.id "
        f"WHERE w.id='{WID}'",
    )
    auth = _api(
        r, "POST", "/auth/session", params=f"subject={subject_owner}&display_name=R3Force"
    )
    token = auth.get("body", {}).get("token", "")
    body = {
        "statement": f"{subject_name}相关事实需要纠正并重做第{ch_i}章",
        "chapter_no": ch_i,
        "kind": "FACT",
        "client_nonce": f"r3-force-{secrets.token_hex(4)}",
        "subject": subject_name,
    }
    report = _api(r, "POST", f"/works/{WID}/facts/report", token=token, body=body, timeout=180)
    print("report", json.dumps(report, ensure_ascii=False)[:1200])
    rb = report.get("body") or {}
    if not rb.get("affected_chapters"):
        # Fallback: insert QUEUED attempt manually by cloning tip row fields via SQL.
        new_id = str(uuid.uuid4())
        new_att = att_i + 1
        # Pull context from cancelled tip
        print(
            q(
                r,
                "INSERT INTO novel_chapter_runs ("
                "id, work_id, branch_id, chapter_no, attempt, state, current_step, "
                "title, content, word_count, generation_context, performances, review, "
                "user_guidance, auto_advance, input_version, version, created_at, updated_at"
                ") "
                "SELECT "
                f"'{new_id}'::uuid, work_id, branch_id, chapter_no, {new_att}, 'QUEUED', 'ASSEMBLE', "
                "title, '', 0, generation_context, '[]'::jsonb, '{}'::jsonb, "
                "'{}'::jsonb, true, input_version, 1, now(), now() "
                f"FROM novel_chapter_runs WHERE id='{run_id}' "
                "RETURNING id::text, chapter_no::text, attempt::text, state",
            )
        )
        # Seed steps like a fresh run — worker may recreate; ensure ASSEMBLE pending.
        # If step rows are required, copy from tip with reset.
        print(
            q(
                r,
                "INSERT INTO novel_chapter_steps (id, run_id, step, state, attempt, created_at, updated_at) "
                "SELECT gen_random_uuid(), "
                f"'{new_id}'::uuid, step, "
                "CASE WHEN step='ASSEMBLE' THEN 'PENDING' ELSE 'PENDING' END, "
                "0, now(), now() "
                f"FROM novel_chapter_steps WHERE run_id='{run_id}' "
                "RETURNING step, state",
            )
        )
    if "resume_then_replay" in (rb.get("available_actions") or []):
        _api(
            r,
            "POST",
            f"/works/{WID}/resume-correction",
            token=token,
            body={"ticket_id": rb.get("ticket_id") or ""},
            timeout=120,
        )
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
            _resolve_pending(r, WID, token)
            continue
        if prog.get("state") == "CANONIZED" and int(prog.get("chapter_no") or 0) >= ch_i:
            snap = memory_snapshot(r, WID, ch_i)
            print(
                "OK",
                json.dumps(
                    {
                        "items": snap["item_count"],
                        "ch1_survive": any(
                            x.get("source_chapter_no") == 1 for x in snap.get("items") or []
                        ),
                        "promises": snap["promise_count"],
                        "resolved": snap["resolved_promise_count"],
                        "ctx_ch1": snap.get("context_has_chapter1_item"),
                    },
                    ensure_ascii=False,
                ),
            )
            return 0
        if prog.get("state") in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
            # keep waiting if a newer attempt got queued
            runs = q(
                r,
                "SELECT chapter_no::text, attempt::text, state "
                f"FROM novel_chapter_runs WHERE work_id='{WID}' AND chapter_no={ch_i} "
                "ORDER BY attempt",
            )
            print("ch runs", runs)
            if "QUEUED" in runs or "RUNNING" in runs:
                continue
            print("FAIL", prog.get("state"))
            return 1
    print("TIMEOUT", last)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
