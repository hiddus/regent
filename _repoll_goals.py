import json
import time
import urllib.request

BASE = "http://118.31.171.159:8000"
goals = [
    "6dc62bcb-xxxx",  # placeholder filled below
]


def get(gid: str) -> dict:
    with urllib.request.urlopen(f"{BASE}/v1/goals/{gid}", timeout=60) as r:
        return json.loads(r.read())


rows = json.loads(
    open(
        r"C:\regent\docs\graduation-evidence\20260722T073327Z\g1_g5_system_goals.json",
        encoding="utf-8",
    ).read()
)
for row in rows:
    gid = row["goal_id"]
    for i in range(36):
        g = get(gid)
        m = g.get("metadata") or {}
        stage = m.get("execution_stage") or g.get("status")
        print(gid[:8], i, stage, bool(m.get("last_preview_endpoint")), m.get("capability_resolution"))
        if (
            str(stage).startswith("PREVIEW")
            or m.get("last_preview_endpoint")
            or m.get("last_iteration_decision")
            or stage in {"FAILED", "BLOCKED", "CANCELLED"}
        ):
            row.update(
                {
                    "execution_stage": stage,
                    "last_preview_endpoint": m.get("last_preview_endpoint"),
                    "last_gate_status": m.get("last_gate_status"),
                    "last_iteration_decision": m.get("last_iteration_decision"),
                    "capability_resolution": m.get("capability_resolution"),
                }
            )
            break
        time.sleep(10)

open(
    r"C:\regent\docs\graduation-evidence\20260722T073327Z\g1_g5_system_goals_repolled.json",
    "w",
    encoding="utf-8",
).write(json.dumps(rows, ensure_ascii=False, indent=2))
print("done")
for row in rows:
    print(row["goal_id"][:8], row.get("execution_stage"), bool(row.get("last_preview_endpoint")))
