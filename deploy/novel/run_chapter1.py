"""在真实环境里从零跑完第 1 章，并把失败原因摊开。

这是缺陷 4 修复后唯一的验收标准：章能不能真的走到 CANONIZED。
失败时不猜——把步骤错误码、worker 日志、未收口调用一起打出来。

用法：python deploy/novel/run_chapter1.py [轮询上限秒]
"""

from __future__ import annotations

import json
import secrets
import shlex
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402


def _api(
    r: Remote,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    params: str = "",
    timeout: int = 60,
) -> dict:
    """调用 Novel API。

    不用 e2e_verify._api：它把超时写死 30 秒，而「确认方向」会跑 1–2 分钟的
    大纲生成——客户端先断，作品还停在 ONBOARDING，随后起章必然 500。
    慢接口必须给慢超时，否则测出来的是测试脚本的耐心，不是系统的行为。
    """
    url = f"http://localhost:8000/v1/novel{path}"
    if params:
        url += f"?{params}"
    script = "\n".join(
        [
            "import urllib.request, urllib.error, json",
            f"url = {url!r}",
            f"method = {method!r}",
            f"body = {json.dumps(body) if body else 'None'}",
            f"token = {token!r}",
            f"timeout = {timeout!r}",
            "req = urllib.request.Request(url, method=method)",
            "if body is not None:",
            "    req.data = json.dumps(body).encode()",
            "    req.add_header('Content-Type', 'application/json')",
            "if token:",
            "    req.add_header('Authorization', 'Bearer ' + token)",
            "try:",
            "    with urllib.request.urlopen(req, timeout=timeout) as resp:",
            "        data = resp.read().decode()",
            '        print(json.dumps({"status": resp.status, "body": json.loads(data) if data else {}}))',
            "except urllib.error.HTTPError as e:",
            "    data = e.read().decode()",
            "    try:",
            "        b = json.loads(data)",
            "    except Exception:",
            '        b = {"raw": data[:400]}',
            '    print(json.dumps({"status": e.code, "body": b}))',
            "except Exception as e:",
            '    print(json.dumps({"status": 0, "body": {"error": str(e)[:300]}}))',
        ]
    ) + "\n"
    r.write_text("/tmp/_ch1_call.py", script)
    r.run("docker cp /tmp/_ch1_call.py regent-api:/tmp/_ch1_call.py", timeout=15)
    res = r.run(f"docker exec regent-api python /tmp/_ch1_call.py", timeout=timeout + 60)
    try:
        return json.loads(res.out.strip())
    except (json.JSONDecodeError, ValueError):
        return {"status": -1, "body": {"raw": res.out.strip()[:400]}}

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"

INTENT = "一个能听见旧物记忆的修表匠，发现父亲失踪前修过的最后一块表正在倒着走"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


def dump_diagnostics(r: Remote, work_id: str) -> None:
    print("\n---------------- 诊断 ----------------")
    print("步骤：")
    print(
        q(
            r,
            "SELECT s.step, s.state, s.attempt, COALESCE(s.error_code,'') "
            "FROM novel_chapter_steps s JOIN novel_chapter_runs run ON run.id = s.run_id "
            f"JOIN novel_works w ON w.id = run.work_id WHERE w.id = '{work_id}' "
            "ORDER BY run.created_at DESC, s.id LIMIT 12",
        )
        or "(无)"
    )
    print("未收口调用：")
    print(
        q(
            r,
            "SELECT status, error_code, reconcile_count, created_at::text "
            "FROM novel_model_calls WHERE status IN ('RESERVED','UNKNOWN') "
            "ORDER BY created_at DESC LIMIT 5",
        )
        or "(无)"
    )
    print("事务中空闲：")
    print(
        q(
            r,
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname='regent' AND state='idle in transaction'",
        )
    )
    for name in ("regent-worker", "regent-worker-2", "regent-worker-3"):
        logs = r.run(
            f"docker logs --since 8m {name} 2>&1 | grep -iE "
            "'error|traceback|exception|failed|timeout' | tail -6"
        ).out.strip()
        if logs:
            print(f"--- {name} 日志 ---")
            print(logs)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    with Remote() as r:
        auth = _api(
            r,
            "POST",
            "/auth/session",
            params=f"subject=ch1-{secrets.token_hex(4)}&display_name=Ch1",
        )
        token = auth.get("body", {}).get("token", "")
        if not token:
            print("[FAIL] 拿不到 token:", auth)
            return 1
        print(f"[1] 已认证")

        work = _api(
            r,
            "POST",
            "/works",
            token=token,
            body={"raw_intent": INTENT, "client_nonce": f"ch1-{secrets.token_hex(4)}"},
        )
        work_id = work.get("body", {}).get("work_id", "")
        if not work_id:
            print("[FAIL] 建作品失败:", work)
            return 1
        print(f"[2] 作品 {work_id}")

        clarify = _api(
            r,
            "POST",
            f"/works/{work_id}/clarify",
            token=token,
            body={"answers": {"genre": "现实主义魔幻", "tone": "温暖而悬疑", "length": "中篇"}},
        )
        cards = clarify.get("body", {}).get("directions") or []
        if not cards:
            print("[FAIL] 没有方向卡:", json.dumps(clarify, ensure_ascii=False)[:600])
            return 1
        card_id = cards[0].get("card_id") or cards[0].get("id")
        print(f"[3] 澄清完成，方向卡 {len(cards)} 张，取 {card_id}")

        conf = _api(
            r,
            "POST",
            f"/works/{work_id}/directions",
            token=token,
            body={"card_id": card_id, "client_nonce": f"ch1-{secrets.token_hex(4)}"},
            timeout=600,  # 大纲生成 1–2 分钟；这里短了会误判成系统故障
        )
        print(f"[4] 方向确认 HTTP {conf.get('status')}")
        if conf.get("status", 0) >= 400:
            print(json.dumps(conf, ensure_ascii=False)[:600])
            return 1

        started = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={})
        run_id = started.get("body", {}).get("run_id", "")
        print(f"[5] 起章 HTTP {started.get('status')} run={run_id}")
        if started.get("status", 0) >= 400:
            print(json.dumps(started, ensure_ascii=False)[:600])
            return 1

        print(f"[6] 轮询至 CANONIZED（上限 {limit}s）…")
        deadline = time.time() + limit
        last = ""
        while time.time() < deadline:
            time.sleep(20)
            prog = _api(r, "GET", f"/works/{work_id}/runs", token=token).get("body", {})
            state = prog.get("state", "?")
            step = prog.get("current_step", "")
            done = [k for k, v in (prog.get("steps") or {}).items() if v == "SUCCEEDED"]
            tag = f"{state}/{step}/{len(done)}步"
            if tag != last:
                print(f"    {time.strftime('%H:%M:%S')} {tag}")
                last = tag
            if state == "CANONIZED":
                print("[OK] 第 1 章已 CANONIZED")
                ch = _api(r, "GET", f"/works/{work_id}/chapters/1", token=token).get("body", {})
                print(f"    字数 {ch.get('word_count', 0)}")
                return 0
            if state in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
                print(f"[FAIL] 运行终态 {state}（卡在 {step}）")
                dump_diagnostics(r, work_id)
                return 1
        print(f"[FAIL] 超时 {limit}s 仍未完成（最后状态 {last}）")
        dump_diagnostics(r, work_id)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
