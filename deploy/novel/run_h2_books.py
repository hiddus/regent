"""Harness² 扩书验证：新建一本书、走完建书流程、连写几章，逐章留证。

灰度按作品 id 分桶，落哪个臂由不得脚本：落进 H2 臂的书验证递归、覆盖层与删段，
落进默认臂的书正好给默认臂的删段触发率与成本攒数据。两种都是有效样本，如实记录。

逐章记：执行臂、终态、调用次数与花费、首过、删段（前后字数、是否截断）、
递归轮次与触发来源、交付来源、硬门失败。终止失败不重写——那正是要看的数据。

用法：python deploy/novel/run_h2_books.py <题材编号 0-3> [章数] [每章轮询上限秒] [执行臂]
      给了执行臂时，建作品后先判桶，不是这个臂就换一本（澄清之前判，不白花建书调用）。
      python deploy/novel/run_h2_books.py --work <work_id> <起章> <止章> [每章轮询上限秒]
      续写已有的书：按作品的 owner 重新登录。
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


def bucket(r: Remote, work_id: str) -> str:
    return r.run(
        "docker exec regent-api python -c \""
        "from regent.novel.application import executor as ex;"
        f"print(ex.choose_executor('{work_id}'))\"",
        timeout=120,
    ).out.strip()


def create_work(r: Remote, token: str, book: dict, want_arm: str = "") -> str:
    for _ in range(8):
        tag = secrets.token_hex(4)
        work = _api(
            r, "POST", "/works", token=token,
            body={"raw_intent": book["intent"], "client_nonce": f"h2b-{tag}"},
        )
        work_id = str(work.get("body", {}).get("work_id") or "")
        if not work_id:
            raise SystemExit(f"[FAIL] 建作品 {json.dumps(work, ensure_ascii=False)[:400]}")
        arm = bucket(r, work_id)
        print(f"[建书] {work_id} {book['answers']['genre']} 分桶 {arm}", flush=True)
        if not want_arm or arm == want_arm:
            return work_id
    raise SystemExit(f"[FAIL] 连建 8 本都没落进 {want_arm}")


def onboard(r: Remote, token: str, book: dict, want_arm: str = "") -> str:
    tag = secrets.token_hex(4)
    work_id = create_work(r, token, book, want_arm)
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
    writes = [row for row in call_rows if row.get("purpose") == "chapter_write"]
    try:
        overlay = json.loads(pin.get("harness_overlay") or "null") or {}
    except json.JSONDecodeError:
        overlay = {}
    review = prod.get("arc_review") if isinstance(prod.get("arc_review"), dict) else {}
    return {
        "state": state,
        "executor": pin.get("executor"),
        "harness_id": pin.get("harness_id"),
        "overlay_id": pin.get("harness_overlay_id"),
        "overlay_prompts": sorted((overlay.get("prompts") or {}) if isinstance(overlay, dict) else {}),
        "write_in_tokens": int(writes[0]["input_tokens"]) if writes else 0,
        "write_out_tokens": int(writes[0]["output_tokens"]) if writes else 0,
        "keeps_reading": review.get("keeps_reading"),
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
        if not _ok(started) and "ending_undecided" in json.dumps(started) and _expand(r, token, work_id):
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


def _expand(r: Remote, token: str, work_id: str) -> bool:
    """卷写完、终局待定：以作者身份确认扩下一卷，章节测试接着跑。"""
    volume = int(q(r, f"SELECT total_volume_count FROM novel_works WHERE id = '{work_id}'").strip()) + 1
    resp = _api(r, "POST", f"/works/{work_id}/volumes/{volume}/expand", token=token, body={}, timeout=600)
    body = resp.get("body") or {}
    print(f"[扩卷] 第 {volume} 卷 {resp.get('status')} 第 {body.get('start_chapter_no')}-{body.get('end_chapter_no')} 章", flush=True)
    return _ok(resp)


def _login(r: Remote, subject: str) -> str:
    auth = _api(r, "POST", "/auth/session", params=f"subject={subject}&display_name=H2Books")
    token = str(auth.get("body", {}).get("token") or "")
    if not token:
        raise SystemExit(f"[FAIL] token {auth}")
    return token


def _owner_subject(r: Remote, work_id: str) -> str:
    return q(
        r,
        "SELECT p.subject FROM novel_works w JOIN novel_principals p ON p.id = w.owner_id "
        f"WHERE w.id = '{work_id}'",
    ).strip()


def write_chapters(subject: str, work_id: str, first: int, last: int, limit: int) -> list[dict]:
    """逐章写；SSH 断了就重连，从这一章接着等（已起的运行不会重起）。"""
    digests: list[dict] = []
    chapter_no = first
    drops = 0
    while chapter_no <= last:
        try:
            with Remote() as r:
                token = _login(r, subject)
                while chapter_no <= last:
                    digest = run_chapter(r, token, work_id, chapter_no, limit)
                    digests.append(digest)
                    if digest.get("state") != "CANONIZED":
                        return digests
                    chapter_no += 1
        except (OSError, EOFError) as exc:
            drops += 1
            print(f"[断线] 第 {chapter_no} 章 {type(exc).__name__}: {exc}；重连第 {drops} 次", flush=True)
            if drops > 5:
                raise
        except Exception as exc:  # noqa: BLE001
            if "SSH" not in str(exc) and "session" not in str(exc).lower():
                raise
            drops += 1
            print(f"[断线] 第 {chapter_no} 章 {exc}；重连第 {drops} 次", flush=True)
            if drops > 5:
                raise
    return digests


def _summarize(work_id: str, book: dict | None, arm: str, digests: list[dict], name: str) -> None:
    with Remote() as r:
        summary = {
            "work_id": work_id,
            "book": book,
            "arm": arm,
            "chapters": digests,
            "library": library(r, work_id),
        }
    _save(work_id, name, summary)
    print(f"[完成] {work_id} 证据 {ART / work_id}", flush=True)


def main() -> int:
    if sys.argv[1] == "--work":
        work_id, first, last = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 2400
        with Remote() as r:
            subject = _owner_subject(r, work_id)
            arm = bucket(r, work_id)
        if not subject:
            print(f"[FAIL] 找不到作品 {work_id} 的 owner")
            return 1
        print(f"[续写] {work_id} 第 {first}-{last} 章 分桶 {arm}", flush=True)
        digests = write_chapters(subject, work_id, first, last, limit)
        _summarize(work_id, None, arm, digests, f"summary_ch{first}-{last}.json")
        return 0
    index = int(sys.argv[1])
    chapters = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 2400
    want_arm = sys.argv[4] if len(sys.argv) > 4 else ""
    book = BOOKS[index]
    subject = f"h2b-{secrets.token_hex(4)}"
    with Remote() as r:
        work_id = onboard(r, _login(r, subject), book, want_arm)
        arm = bucket(r, work_id)
    print(f"[分桶] {arm}", flush=True)
    digests = write_chapters(subject, work_id, 1, chapters, limit)
    _summarize(work_id, book, arm, digests, "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
