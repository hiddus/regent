"""Playwright P1 DoD: browser completes core task → real Observation → Gate decision.

This is the graduation proof that non-developer UI interaction (not API inject)
drives CONTINUE/REVISE/STOP.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("REGENT_ACCEPTANCE_BASE_URL", "http://118.31.171.159:8000")
TIMEOUT_CHAIN_SEC = int(os.environ.get("REGENT_ACCEPTANCE_TIMEOUT", "360"))


def api_get(path: str) -> dict[str, Any]:
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def api_post(path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def wait_preview(goal_id: str) -> dict[str, Any]:
    print(f"\n=== Wait for PREVIEW_SUCCEEDED (timeout={TIMEOUT_CHAIN_SEC}s) ===")
    start = time.time()
    last = ""
    while time.time() - start < TIMEOUT_CHAIN_SEC:
        goal = api_get(f"/v1/goals/{goal_id}")
        meta = goal.get("metadata") or {}
        stage = meta.get("execution_stage")
        gate = meta.get("last_gate_status")
        key = f"{stage}:{gate}"
        if key != last:
            print(f"  [{time.time()-start:.1f}s] stage={stage} gate={gate}")
            last = key
        if stage == "PREVIEW_SUCCEEDED":
            return goal
        time.sleep(3)
    raise TimeoutError("timed out waiting for PREVIEW_SUCCEEDED")


def public_preview_url(endpoint: str, deployment_id: str) -> str:
    url = endpoint.replace("http://regent-api:8000", BASE_URL)
    if "deployment_id=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}deployment_id={deployment_id}"
    return url


def browser_complete_core_task(preview_url: str) -> None:
    """Act as a non-developer: open Preview and click the core-task control."""
    from playwright.sync_api import sync_playwright

    print("\n=== Playwright: complete core user task ===")
    print(f"  url={preview_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(preview_url, wait_until="domcontentloaded", timeout=60_000)
        hook = page.locator("[data-regent-event]")
        if hook.count() == 0:
            browser.close()
            raise AssertionError("preview has no data-regent-event control (DoD fail)")
        # Prefer a clickable control; fall back to the hook element itself.
        target = page.locator("[data-regent-event] button, button[data-regent-event], [data-regent-event]").first
        target.click(timeout=15_000)
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-regent-obs') === 'ok'",
            timeout=20_000,
        )
        obs = page.get_attribute("html", "data-regent-obs")
        browser.close()
    print(f"  browser observation post: {obs}")
    assert obs == "ok"


def evaluate_gate(deployment_id: str) -> dict[str, Any]:
    print("\n=== Evaluate Gate after browser observation ===")
    result = api_post(
        f"/v1/deployments/{deployment_id}/evaluate",
        {"actor": "p1-playwright-tester"},
    )
    print(f"  gate={result.get('status')} decision={result.get('decision')}")
    return result


def main() -> None:
    print("=" * 60)
    print("P1 DoD Playwright Acceptance")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    live = api_get("/health/live")
    assert live["status"] == "ok"
    print("\n=== Health PASS ===")

    draft = api_post(
        "/v1/app-projects/drafts",
        {
            "idea": (
                "A simple static hello world web page that shows the current time "
                "and has a clearly labeled button with data-regent-event=activation "
                "so a visitor can complete the core task by clicking it."
            ),
            "actor": "p1-playwright-tester",
        },
    )
    project_id = str(draft["project"]["id"])
    goal_id = str(draft["goal_id"])
    spec_hash = str(draft["goal_spec_hash"])
    print(f"  project={project_id}")
    print(f"  goal={goal_id}")

    api_post(
        f"/v1/app-projects/{project_id}/confirm",
        {"actor": "p1-playwright-tester", "expected_spec_hash": spec_hash},
    )
    api_post(
        f"/v1/goals/{goal_id}/start",
        {
            "actor": "p1-playwright-tester",
            "idempotency_key": f"p1-pw-{goal_id[:8]}",
        },
    )

    goal = wait_preview(goal_id)
    meta = goal.get("metadata") or {}
    deployment_id = meta.get("last_deployment_id")
    endpoint = meta.get("last_preview_endpoint")
    if not deployment_id or not endpoint:
        raise AssertionError("missing last_deployment_id or last_preview_endpoint")

    if meta.get("last_gate_status") == "INSUFFICIENT_EVIDENCE":
        print("  gate correctly waiting for real browser observation")

    preview_url = public_preview_url(str(endpoint), str(deployment_id))
    browser_complete_core_task(preview_url)

    gate = evaluate_gate(str(deployment_id))
    status = gate.get("status")
    decision = gate.get("decision")
    if status not in ("PASSED", "FAILED"):
        raise SystemExit(f"FAIL: gate not decided ({status})")
    if decision not in ("CONTINUE", "REVISE", "STOP"):
        raise SystemExit(f"FAIL: no iteration decision ({decision})")

    final = api_get(f"/v1/goals/{goal_id}")
    fmeta = final.get("metadata") or {}
    print("\n" + "=" * 60)
    print("P1 DoD Playwright PASS")
    print(f"  Goal: {goal_id}")
    print(f"  Deployment: {deployment_id}")
    print(f"  Gate: {status}")
    print(f"  Decision: {decision}")
    print(f"  Goal metadata decision: {fmeta.get('last_iteration_decision')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
