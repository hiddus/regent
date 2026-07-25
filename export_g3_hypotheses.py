"""Export G3 hypotheses from production DB via SSH."""

from __future__ import annotations

import json
from pathlib import Path

import paramiko
import urllib.request

ROOT = Path(__file__).resolve().parent
EVID = ROOT / "docs" / "graduation-evidence" / "20260722T073327Z"
BASE = "http://118.31.171.159:8000"
GOAL_IDS = [
    "6dc62bcb-58d5-46d3-bef6-46cb067943a9",
    "9c0088b4-06f2-42ad-b5fd-4bc790354e95",
    "2c3a3e77-09c8-4026-ba8e-f4d0ee283306",
    "74abfc0e-632e-496f-aeac-3af058f45a06",
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def main() -> None:
    env = load_env()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        env["SERVER_IP"], username=env["LOGIN_USER"], password=env["LOGIN_PASSWORD"], timeout=20
    )

    def run(cmd: str) -> str:
        _, stdout, stderr = client.exec_command(cmd, timeout=120)
        return (stdout.read().decode() + stderr.read().decode()).strip()

    in_list = ",".join(f"'{g}'" for g in GOAL_IDS)
    # discover table names
    tables = run(
        "docker exec regent-postgres psql -U regent -d regent -At -c "
        "\"SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND tablename ILIKE '%hypoth%' OR tablename ILIKE '%discover%' "
        "ORDER BY 1;\""
    )
    print("tables:\n", tables)

    rounds_raw = run(
        "docker exec regent-postgres psql -U regent -d regent -At -F '|' -c "
        f"\"SELECT goal_id::text, id::text, round, status "
        f"FROM discovery_rounds WHERE goal_id::text IN ({in_list}) ORDER BY 1,3;\""
    )
    print("rounds:\n", rounds_raw)

    rows: list[dict] = []
    for line in rounds_raw.splitlines():
        if "|" not in line:
            continue
        goal_id, round_id, round_no, status = line.split("|", 3)
        # fetch hypotheses via API
        url = f"{BASE}/v1/discovery-rounds/{round_id}/hypotheses"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                hyps = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            hyps = {"error": str(exc)}
        titles = []
        if isinstance(hyps, list):
            for h in hyps:
                content = h.get("content") or {}
                title = (
                    content.get("title")
                    or content.get("name")
                    or h.get("candidate_key")
                    or str(content)[:80]
                )
                titles.append(
                    {
                        "id": h.get("id"),
                        "candidate_key": h.get("candidate_key"),
                        "title": title,
                        "eligibility": h.get("eligibility"),
                        "content_hash": h.get("content_hash"),
                    }
                )
        diffs = sorted({t["title"] for t in titles if t.get("title")})
        rows.append(
            {
                "goal_id": goal_id,
                "round_id": round_id,
                "round": round_no,
                "status": status,
                "hypothesis_count": len(titles),
                "hypotheses": titles,
                "machine_listable_diff_titles": diffs,
                "pass_g3": len(titles) >= 2 and len(diffs) >= 2,
            }
        )

    by_goal: dict[str, list] = {g: [] for g in GOAL_IDS}
    for r in rows:
        by_goal.setdefault(r["goal_id"], []).append(r)

    out = {
        "source": "discovery_rounds via DB + /hypotheses API",
        "goals": [
            {
                "goal_id": g,
                "rounds": by_goal.get(g, []),
                "pass_g3": any(x.get("pass_g3") for x in by_goal.get(g, [])),
            }
            for g in GOAL_IDS
        ],
        "goals_passing_g3": sum(
            1 for g in GOAL_IDS if any(x.get("pass_g3") for x in by_goal.get(g, []))
        ),
    }
    (EVID / "g3_hypotheses.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"goals_passing_g3": out["goals_passing_g3"], "rounds": len(rows)}, indent=2))
    client.close()


if __name__ == "__main__":
    main()
