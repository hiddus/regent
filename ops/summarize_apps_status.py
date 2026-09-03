"""Summarize probe_apps_status output."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
raw = subprocess.check_output([sys.executable, str(ROOT / "ops" / "probe_apps_status.py")], text=True)
i = raw.find("{")
data = json.loads(raw[i:])
print("=== queue ===", json.dumps(data.get("outbox_GenRun"), ensure_ascii=False))
print("=== runs ===", json.dumps(data.get("generation_runs"), ensure_ascii=False))
print("=== 7d goal status x stage (top) ===")
for row in (data.get("goal_status_x_stage_7d") or [])[:12]:
    print(f"  {row['status']:14} {(row['stage'] or '-'):36} n={row['n']}")
apps = data.get("recent_apps") or []
print("=== recent apps by generation_progress ===", dict(Counter(a.get("generation_progress") for a in apps)))
print("--- sample (name / goal_status / progress / preview?) ---")
for a in apps[:18]:
    name = (a.get("name") or "")[:24]
    prev = "yes" if a.get("preview") else "no"
    print(
        f"  {name:24} {str(a.get('goal_status')):14} "
        f"{str(a.get('generation_progress')):14} preview={prev}"
    )
