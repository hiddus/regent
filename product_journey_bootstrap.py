"""Start PRODUCT evidence window journeys against graduation previews."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://118.31.171.159:8000"
EVID = Path("docs/graduation-evidence/20260722T073327Z")
rows = json.loads((EVID / "g1_g5_system_goals_repolled.json").read_text(encoding="utf-8"))


def api_get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read())


results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for i, row in enumerate(rows):
        goal = api_get(f"/v1/goals/{row['goal_id']}")
        meta = goal.get("metadata") or {}
        endpoint = meta.get("last_preview_endpoint")
        deployment_id = meta.get("last_deployment_id")
        item = {
            "goal_id": row["goal_id"],
            "endpoint": endpoint,
            "deployment_id": deployment_id,
            "ok": False,
            "error": None,
            "gate": None,
        }
        try:
            if not endpoint or not deployment_id:
                raise AssertionError("missing preview metadata")
            url = endpoint.replace("http://regent-api:8000", BASE)
            if "deployment_id=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}deployment_id={deployment_id}"
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            hook = page.locator("[data-regent-event]")
            if hook.count() == 0:
                raise AssertionError("no data-regent-event")
            page.locator(
                "[data-regent-event] button, button[data-regent-event], [data-regent-event]"
            ).first.click(timeout=15_000)
            page.wait_for_function(
                "() => document.documentElement.getAttribute('data-regent-obs') === 'ok'",
                timeout=20_000,
            )
            page.close()
            # evaluate gate
            req = urllib.request.Request(
                f"{BASE}/v1/deployments/{deployment_id}/evaluate",
                data=json.dumps({"actor": f"product-journey-{i}"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                gate = json.loads(resp.read())
            item["gate"] = {
                "status": gate.get("status"),
                "decision": gate.get("decision"),
            }
            item["ok"] = True
        except Exception as exc:  # noqa: BLE001
            item["error"] = str(exc)
        results.append(item)
        print(item)
    browser.close()

(EVID / "g6_journey_batch1.json").write_text(
    json.dumps(
        {
            "actor_class": "automation_bootstrap_not_nondev_users",
            "note": (
                "These journeys bootstrap Observation plumbing only. "
                "PRODUCT_EVIDENCE still requires ≥5 distinct non-developer users "
                "over ≥7 days."
            ),
            "results": results,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("journeys ok", sum(1 for r in results if r["ok"]), "/", len(results))
