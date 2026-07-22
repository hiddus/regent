"""P1 Acceptance Test Suite.

Chain integrity checks against a deployed server.
API observation injection is a plumbing probe only — it does NOT satisfy
P1 DoD / P2-0 graduation (real non-developer core-task completion).
"""
import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://118.31.171.159:8000"


def api_get(path: str) -> dict:
    """GET request."""
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def api_post(path: str, data: dict | None = None) -> dict:
    """POST request."""
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def test_health() -> None:
    """Test health endpoints."""
    print("\n=== 1. Health Check ===")
    live = api_get("/health/live")
    print(f"  /health/live: {live}")
    assert live["status"] == "ok"

    ready = api_get("/health/ready")
    print(f"  /health/ready: {ready}")
    assert ready["status"] == "ok"
    assert ready["database"] == "ok"
    print("  PASS")


def test_create_draft() -> dict:
    """Create an app project draft (triggers goal creation + spec generation)."""
    print("\n=== 2. Create App Draft ===")
    result = api_post("/v1/app-projects/drafts", {
        "idea": "A simple Python Flask web app that displays a hello world page with the current timestamp",
        "actor": "p1-acceptance-tester",
    })
    print(f"  Project ID: {result['project']['id']}")
    print(f"  Goal ID: {result['goal_id']}")
    print(f"  Goal Status: {result['goal_status']}")
    print(f"  Spec Status: {result['goal_spec_status']}")
    print(f"  Spec Hash: {result['goal_spec_hash']}")
    return result


def test_confirm_project(project_id: str, spec_hash: str) -> dict:
    """Confirm the app project (freezes spec)."""
    print("\n=== 3. Confirm Project ===")
    result = api_post(f"/v1/app-projects/{project_id}/confirm", {
        "actor": "p1-acceptance-tester",
        "expected_spec_hash": spec_hash,
    })
    print(f"  Goal ID: {result['goal_id']}")
    print(f"  Goal Status: {result['goal_status']}")
    print(f"  Spec Status: {result['goal_spec_status']}")
    return result


def test_start_goal(goal_id: str) -> dict:
    """Start the goal to trigger execution chain."""
    print("\n=== 4. Start Goal ===")
    result = api_post(f"/v1/goals/{goal_id}/start", {
        "actor": "p1-acceptance-tester",
        "idempotency_key": f"p1-acceptance-{goal_id[:8]}",
    })
    print(f"  Status: {result.get('status', 'N/A')}")
    return result


def test_monitor_goal(goal_id: str, timeout_sec: int = 180) -> dict:
    """Monitor the goal execution chain progress."""
    print(f"\n=== 5. Monitor Event Chain (timeout={timeout_sec}s) ===")
    start = time.time()
    last_key = ""
    while time.time() - start < timeout_sec:
        try:
            goal = api_get(f"/v1/goals/{goal_id}")
            metadata = goal.get("metadata") or {}
            stage = metadata.get("execution_stage", "unknown")
            status = goal.get("status", "unknown")
            spec_status = goal.get("spec_status", "unknown")
            decision = metadata.get("last_iteration_decision")
            gate = metadata.get("last_gate_status")

            key = f"{stage}:{status}:{spec_status}:{decision}:{gate}"
            if key != last_key:
                elapsed = time.time() - start
                print(
                    f"  [{elapsed:.1f}s] stage={stage} status={status} "
                    f"spec={spec_status} gate={gate} decision={decision}"
                )
                last_key = key

            if status in (
                "COMPLETED",
                "FAILED",
                "STOPPED",
                "ARCHIVED",
                "ACHIEVED",
                "EXHAUSTED",
                "CANCELLED",
            ):
                print(f"  Terminal state reached at {time.time()-start:.1f}s")
                return goal

            if stage == "PREVIEW_SUCCEEDED":
                print(f"  Chain reached PREVIEW_SUCCEEDED at {time.time()-start:.1f}s")
                return goal

            time.sleep(3)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(3)

    print("  Timeout reached.")
    return api_get(f"/v1/goals/{goal_id}")


def test_preview_http(goal: dict) -> None:
    """Probe preview HTTP reachability (not a browser core-task proof)."""
    print("\n=== 5b. Preview HTTP Probe ===")
    metadata = goal.get("metadata") or {}
    endpoint = metadata.get("last_preview_endpoint")
    if not endpoint:
        print("  SKIP: no last_preview_endpoint")
        return
    public = endpoint.replace("http://regent-api:8000", BASE_URL)
    req = urllib.request.Request(public)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read(400)
        print(f"  HTTP {resp.status}, bytes={len(body)}")
        text = body.decode("utf-8", errors="replace")
        if "data-regent-event" not in text:
            raise AssertionError("preview HTML missing data-regent-event (P2-0)")
        if "Complete task" in text and "data-regent-event" in text:
            # Synthetic button text must not be auto-injected by provider
            pass
    print("  PASS (HTTP + hook present; browser core-task still required for graduation)")


def test_observation_plumbing(goal: dict) -> None:
    """Plumbing-only: observation API works. Does NOT count as DoD evidence."""
    print("\n=== 5c. Observation API Plumbing (not DoD) ===")
    metadata = goal.get("metadata") or {}
    deployment_id = metadata.get("last_deployment_id")
    if not deployment_id:
        print("  SKIP: no last_deployment_id")
        return
    obs = api_post(
        f"/v1/deployments/{deployment_id}/events",
        {"event_id": f"plumbing-{int(time.time())}", "event_name": "activation"},
    )
    print(f"  observation_id={obs.get('observation_id')}")
    result = api_post(
        f"/v1/deployments/{deployment_id}/evaluate",
        {"actor": "p1-acceptance-tester"},
    )
    print(f"  gate={result.get('status')} decision={result.get('decision')}")
    print(
        "  NOTE: API inject proves plumbing only; "
        "P2-0 requires non-developer browser completion of core task"
    )


def test_check_outbox() -> None:
    """Check outbox status and dead-letter API."""
    print("\n=== 6. Outbox Status ===")
    ready = api_get("/health/ready")
    print(f"  outbox_failed: {ready.get('outbox_failed', 'N/A')}")
    print(f"  outbox_dead_letter: {ready.get('outbox_dead_letter', 'N/A')}")
    dead = api_get("/v1/governance/outbox/dead-letters?limit=5")
    print(f"  dead-letter API items: {len(dead.get('items', []))}")


def main() -> None:
    print("=" * 60)
    print("P1 Chain Integrity Suite (not full DoD graduation)")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    test_health()
    draft = test_create_draft()
    project_id = str(draft["project"]["id"])
    goal_id = str(draft["goal_id"])
    spec_hash = str(draft["goal_spec_hash"])
    test_confirm_project(project_id, spec_hash)
    test_start_goal(goal_id)
    final = test_monitor_goal(goal_id, timeout_sec=360)
    meta = final.get("metadata") or {}

    if meta.get("execution_stage") == "PREVIEW_SUCCEEDED":
        test_preview_http(final)
        test_observation_plumbing(final)

    test_check_outbox()

    print("\n" + "=" * 60)
    print("Chain Integrity Complete")
    print(f"  Goal ID: {goal_id}")
    print(f"  Project ID: {project_id}")
    print(f"  Stage: {meta.get('execution_stage')}")
    print(f"  Gate: {meta.get('last_gate_status')}")
    print(f"  Decision: {meta.get('last_iteration_decision')}")
    print("=" * 60)

    if meta.get("execution_stage") != "PREVIEW_SUCCEEDED":
        raise SystemExit("FAIL: did not reach PREVIEW_SUCCEEDED")
    print(
        "INTEGRITY PASS. Full P1 DoD / P2-0 graduation still requires: "
        "browser core-task by non-developer, real external evidence, "
        "git baseline, and credential rotation evidence."
    )


if __name__ == "__main__":
    main()
