"""Harness² 扩书验证：新建一本书、走完建书流程、连写几章，逐章留证。

灰度按作品 id 分桶，落哪个臂由不得脚本：落进 H2 臂的书验证递归、覆盖层与删段，
落进默认臂的书正好给默认臂的删段触发率与成本攒数据。两种都是有效样本，如实记录。

逐章记：执行臂、终态、调用次数与花费、首过、删段（前后字数、是否截断）、
递归轮次与触发来源、交付来源、硬门失败。终止失败不重写——那正是要看的数据。

用法：python deploy/novel/run_h2_books.py <题材编号 0-3> [章数] [每章轮询上限秒]
证据：deploy/novel/artifacts/h2_books/<work_id>/
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402
from run_chapter1 import _api, q  # noqa: E402
from run_h2_chapter import calls, cost, library, pinning, production, steps  # noqa: E402
from run_r3_three_chapters import wait_canonized  # noqa: E402

ART = Path(__file__).resolve().parent / "artifacts" / "h2_books"

BOOKS = (
    {
        "intent": "被逐出宗门的炼丹学徒，发现自己炼废的丹药能让死人开口说一句话",
        "answers": {"genre": "东方玄幻", "tone": "热血而克制", "length": "中篇"},
    },
    {
        "intent": "快递分拣员在一件无人认领的包裹里发现了自己十年后的工牌",
        "answers": {"genre": "都市悬疑", "tone": "冷静紧张", "length": "中篇"},
    },
    {
        "intent": "空间站唯一的维修工发现站上的 AI 每天凌晨都在悄悄删除一名船员的记录",
        "answers": {"genre": "硬科幻", "tone": "压抑而好奇", "length": "中篇"},
    },
    {
        "intent": "替嫁的庶女在新婚夜发现夫君书房里挂着自己生母的画像",
        "answers": {"genre": "古言宫斗", "tone": "隐忍而锋利", "length": "中篇"},
    },
)


def _save(work_id: str, name: str, payload: object) -> None:
    folder = ART / work_id
    folder.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2, default=str
    )
    (folder / name).write_text(text, encoding="utf-8")


def _ok(resp: dict) -> bool:
    return 200 <= int(resp.get("status") or 0) < 400


def onboard(r: Remote, token: str, book: dict) -> str:
    tag = secrets.token_hex(4)
    work = _api(r, "POST", "/works", token=token, body={"raw_intent": book["intent"], "client_nonce": f"h2b-{tag}"})
    work_id = str(work.get("body", {}).get("work_id") or "")
    if not work_id:
        raise SystemExit(f"[FAIL] 建作品 {json.dumps(work, ensure_ascii=False)[:400]}")
    print(f"[建书] {work_id} {book['answers']['genre']}", flush=True)
    clarify = _api(
        r, "POST", f"/works/{work_id}/clarify", token=token, body={"answers": book["answers"]}, timeout=600
    )
    cards = clarify.get("body", {}).get("directions") or []
    if not cards:
        raise SystemExit(f"[FAIL] 无方向卡 {json.dumps(clarify, ensure_ascii=False)[:400]}")
    card_id = cards[0].get("card_id") or cards[0].get("id")
    conf = _api(
        r, "POST", f"/works/{work_id}/directions", token=token,
        body={"card_id": card_id, "client_nonce": f"h2b-dir-{tag}"}, timeout=1800,
    )
    if not _ok(conf):
        raise SystemExit(f"[FAIL] 方向确认 {json.dumps(conf, ensure_ascii=False)[:400]}")
    lock = _api(
        r, "POST", f"/works/{work_id}/world-bible/lock", token=token,
        body={"client_nonce": f"h2b-lock-{tag}"}, timeout=180,
    )
    if not _ok(lock):
        raise SystemExit(f"[FAIL] 锁定世界书 {json.dumps(lock, ensure_ascii=False)[:400]}")
    print(f"[建书] 方向与世界书就绪", flush=True)
    return work_id


def _latest_run(r: Remote, work_id: str, chapter_no: int) -> str:
    return q(
        r,
        "SELECT id::text FROM novel_chapter_runs "
        f"WHERE work_id='{work_id}' AND chapter_no={chapter_no} ORDER BY attempt DESC LIMIT 1",
    ).strip()


def chapter_digest(prod: dict, pin: dict, call_rows: list[dict], spent: str, state: str) -> dict:
    history = prod.get("h2_history") or []
    return {
        "state": state,
        "executor": pin.get("executor"),
        "overlay_id": pin.get("harness_overlay_id"),
        "calls": len(call_rows),
        "cost": spent,
        "first_pass": prod.get("first_pass"),
        "fit": [
            {k: item.get(k) for k in ("tag", "chars_before", "chars_after", "units_dropped", "truncated")}
            for item in prod.get("fit_results") or []
        ],
        "h2_rounds": [
            {k: item.get(k) for k in ("round", "trigger", "decision", "deliver")} for item in history
        ],
        "delivered_from": prod.get("h2_delivered_from"),
        "check_fails": prod.get("check_fails"),
        "rewrites": prod.get("rewrites"),
        "repairs": prod.get("repairs"),
        "last_error": prod.get("last_error"),
    }


def run_chapter(r: Remote, token: str, work_id: str, chapter_no: int, limit: int) -> dict:
    # 连续写作开着时上一章入正史后会自动起下一章，这时再起章是 409
    if not _latest_run(r, work_id, chapter_no):
        started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={}, timeout=180)
        if not _ok(started) and not _latest_run(r, work_id, chapter_no):
            return {"state": "START_FAILED", "response": started}
    state, _ = wait_canonized(r, work_id=work_id, token=token, chapter_no=chapter_no, limit=limit)
    run_id = _latest_run(r, work_id, chapter_no)
    prod = production(r, run_id)
    pin = pinning(r, run_id)
    call_rows = calls(r, run_id)
    evidence = {
        "run_id": run_id,
        "pinning": pin,
        "steps": steps(r, run_id),
        "calls": call_rows,
        "production": prod,
    }
    _save(work_id, f"ch{chapter_no}_evidence.json", evidence)
    digest = chapter_digest(prod, pin, call_rows, cost(r, run_id), state)
    print(f"[第 {chapter_no} 章] {json.dumps(digest, ensure_ascii=False)}", flush=True)
    return digest


def main() -> int:
    index = int(sys.argv[1])
    chapters = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 2400
    book = BOOKS[index]
    with Remote() as r:
        auth = _api(r, "POST", "/auth/session", params=f"subject=h2b-{secrets.token_hex(4)}&display_name=H2Books")
        token = str(auth.get("body", {}).get("token") or "")
        if not token:
            print("[FAIL] token", auth)
            return 1
        work_id = onboard(r, token, book)
        arm = r.run(
            "docker exec regent-api python -c \""
            "from regent.novel.application import executor as ex;"
            f"print(ex.choose_executor('{work_id}'))\"",
            timeout=120,
        ).out.strip()
        print(f"[分桶] {arm}", flush=True)
        digests = []
        for chapter_no in range(1, chapters + 1):
            digest = run_chapter(r, token, work_id, chapter_no, limit)
            digests.append(digest)
            if digest.get("state") != "CANONIZED":
                break
        summary = {
            "work_id": work_id,
            "book": book,
            "arm": arm,
            "chapters": digests,
            "library": library(r, work_id),
        }
        _save(work_id, "summary.json", summary)
        print(f"[完成] {work_id} 证据 {ART / work_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
