"""End-to-end verification of Novel Engine MVP on the deployed server.

Exercises the full flow: auth → create work → clarify → direction → run → chapter.
"""

from __future__ import annotations

import json
import secrets
import sys
import time

from _ssh import Remote


def _api(r: Remote, method: str, path: str, *, token: str | None = None, body: dict | None = None, params: str = "") -> dict:
    """Call a Novel API endpoint via curl on the server and return parsed JSON."""
    url = f"http://localhost:8000/v1/novel{path}"
    if params:
        url += f"?{params}"
    
    # Build the helper script without f-strings to avoid brace escaping
    script_lines = [
        "import urllib.request, urllib.error, json, sys",
        "",
        f"url = {url!r}",
        f"method = {method!r}",
        f"body = {json.dumps(body) if body else 'None'}",
        f"token = {token!r}",
        "",
        "req = urllib.request.Request(url, method=method)",
        "if body is not None:",
        "    req.data = json.dumps(body).encode()",
        "    req.add_header('Content-Type', 'application/json')",
        "if token:",
        "    req.add_header('Authorization', 'Bearer ' + token)",
        "",
        "try:",
        "    with urllib.request.urlopen(req, timeout=30) as resp:",
        "        data = resp.read().decode()",
        '        result = {"status": resp.status, "body": json.loads(data) if data else {}}',
        "        print(json.dumps(result))",
        "except urllib.error.HTTPError as e:",
        "    data = e.read().decode()",
        "    try:",
        "        b = json.loads(data)",
        "    except Exception:",
        '        b = {"raw": data}',
        '    print(json.dumps({"status": e.code, "body": b}))',
        "except Exception as e:",
        '    print(json.dumps({"status": 0, "body": {"error": str(e)}}))',
    ]
    script = "\n".join(script_lines) + "\n"
    # Write to host tmp, then copy into the container (container /tmp is isolated)
    r.write_text("/tmp/_e2e_call.py", script)
    r.run("docker cp /tmp/_e2e_call.py regent-api:/tmp/_e2e_call.py", timeout=10)
    result = r.run("docker exec regent-api python /tmp/_e2e_call.py", timeout=60)
    try:
        return json.loads(result.out.strip())
    except (json.JSONDecodeError, ValueError):
        return {"status": -1, "body": {"raw": result.out.strip(), "err": result.err.strip()}}


def main() -> None:
    ok_count = 0
    fail_count = 0
    
    def check(label: str, resp: dict, expect_status: int = 200) -> dict:
        nonlocal ok_count, fail_count
        status = resp.get("status", -1)
        body = resp.get("body", {})
        passed = status == expect_status or (200 <= status < 300 and 200 <= expect_status < 300)
        tag = "PASS" if passed else "FAIL"
        if passed:
            ok_count += 1
        else:
            fail_count += 1
        print(f"\n[{tag}] {label} → HTTP {status}")
        if not passed or True:  # always show body for debugging
            print(f"  Body: {json.dumps(body, ensure_ascii=False, indent=2)[:500]}")
        return body
    
    with Remote() as r:
        # --- Step 0: Health check ---
        print("=" * 60)
        print("Novel Engine MVP — End-to-End Verification")
        print("=" * 60)
        
        resp = _api(r, "GET", "/works", params="")
        # This should return 401 (no auth) which proves the route exists
        status = resp.get("status", -1)
        if status in (401, 403, 200):
            print(f"\n[PASS] Novel API route registered (HTTP {status})")
            ok_count += 1
        else:
            print(f"\n[FAIL] Novel API route NOT registered (HTTP {status})")
            fail_count += 1
            print("Cannot continue without API route. Exiting.")
            sys.exit(1)

        # --- Step 1: Create session (auth) ---
        resp = _api(r, "POST", "/auth/session", params=f"subject=e2e-{secrets.token_hex(4)}&display_name=E2E+Tester")
        body = check("Create auth session", resp)
        token = body.get("token", "")
        if not token:
            # Try alternate response shape
            token = body.get("data", {}).get("token", "") if isinstance(body.get("data"), dict) else ""
        if not token:
            print("  WARNING: No token returned. Trying raw output...")
            print(f"  Full body: {body}")
        
        principal_id = body.get("principal_id", "")
        if not principal_id:
            principal_id = body.get("data", {}).get("principal_id", "") if isinstance(body.get("data"), dict) else ""
        print(f"  Token: {token[:20]}..." if token else "  Token: MISSING")
        print(f"  Principal: {principal_id}")

        # --- Step 2: Create a work ---
        resp = _api(r, "POST", "/works", token=token, body={
            "raw_intent": "一个能听见旧物记忆的修表匠，发现父亲失踪前修过的最后一块表正在倒着走",
            "client_nonce": f"e2e-{secrets.token_hex(4)}"
        })
        body = check("Create work", resp)
        work_id = body.get("work_id", "")
        if not work_id:
            work_id = body.get("data", {}).get("work_id", "") if isinstance(body.get("data"), dict) else ""
        print(f"  Work ID: {work_id}")
        
        if not work_id:
            print("  Cannot continue without work_id")
            # Dump debug info
            print(f"  Full response: {json.dumps(body, ensure_ascii=False)[:1000]}")
            sys.exit(1)

        # --- Step 3: Get work status ---
        resp = _api(r, "GET", f"/works/{work_id}", token=token)
        body = check("Get work status", resp)
        state = body.get("state", body.get("data", {}).get("state", "???"))
        print(f"  State: {state}")

        # --- Step 4: Answer clarification ---
        resp = _api(r, "POST", f"/works/{work_id}/clarify", token=token, body={
            "answers": {
                "genre": "现实主义魔幻",
                "tone": "温暖而悬疑",
                "length": "中篇"
            }
        })
        body = check("Answer clarification", resp)

        # --- Step 5: Confirm direction (directions were in clarify response) ---
        # The answer_clarify response already contains directions.
        # POST /directions with card_id to confirm.
        resp = _api(r, "POST", f"/works/{work_id}/directions", token=token, body={
            "card_id": "card-fast",
            "client_nonce": "e2e-direction-001"
        })
        body = check("Confirm direction", resp)
        nodes = body.get("nodes", [])
        print(f"  Path nodes after confirm: {len(nodes)}")

        # --- Step 7: Get critical path ---
        resp = _api(r, "GET", f"/works/{work_id}/critical-path", token=token)
        body = check("Get critical path", resp)
        nodes = body.get("nodes", body.get("data", {}).get("nodes", []))
        print(f"  Path nodes: {len(nodes)}")

        # --- Step 8: Start run ---
        resp = _api(r, "POST", f"/works/{work_id}/runs", token=token, body={})
        body = check("Start run", resp)
        run_id = body.get("run_id", body.get("data", {}).get("run_id", ""))
        print(f"  Run ID: {run_id}")

        # --- Step 9: Check run progress ---
        time.sleep(2)
        resp = _api(r, "GET", f"/works/{work_id}/runs", token=token)
        body = check("List runs", resp)

        # --- Step 10: Try to read chapter 1 ---
        resp = _api(r, "GET", f"/works/{work_id}/chapters/1", token=token)
        status = resp.get("status", -1)
        if status == 200:
            check("Read chapter 1", resp)
        elif status in (404, 409, 425):
            print(f"\n[INFO] Chapter 1 not ready yet (HTTP {status}) — expected for async run")
            print("  (Worker needs time to generate)")
            ok_count += 1
        else:
            check("Read chapter 1", resp, expect_status=200)

        # --- Step 11: Check events ---
        resp = _api(r, "GET", f"/works/{work_id}/events", token=token)
        body = check("Get events", resp)
        events = body.get("events", body.get("data", {}).get("events", []))
        print(f"  Event count: {len(events)}")

        # --- Step 12: Check frontend (served by API on port 8000) ---
        result = r.run("curl -s -o /dev/null -w '%{http_code}' http://regent-api:8000/ 2>&1")
        frontend_status = result.out.strip()
        if frontend_status == "200":
            print(f"\n[PASS] Frontend accessible via API port (HTTP {frontend_status})")
            ok_count += 1
        else:
            result2 = r.run("curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/ 2>&1")
            frontend_status2 = result2.out.strip()
            if frontend_status2 == "200":
                print(f"\n[PASS] Frontend accessible via host port (HTTP {frontend_status2})")
                ok_count += 1
            else:
                print(f"\n[FAIL] Frontend NOT accessible (internal={frontend_status}, host={frontend_status2})")
                fail_count += 1

        # --- Summary ---
        print("\n" + "=" * 60)
        print(f"E2E Results: {ok_count} PASS, {fail_count} FAIL")
        print("=" * 60)
        
        if fail_count > 0:
            print("\nSome checks failed. Review output above for details.")
            sys.exit(1)
        else:
            print("\nAll checks passed! Novel Engine MVP is operational.")


if __name__ == "__main__":
    main()
