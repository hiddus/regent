"""部署前暂停其他作品的自动续写，部署后只按快照恢复。

用法：python deploy/novel/continuation_pause.py pause|restore|check

- pause：把正在续写的作品的 continuation_policy 存进快照，再统一关掉。
- restore：只把快照里的作品恢复成快照里的策略，不碰快照外的作品。
- check：列出关着但不在快照里的作品，核对恢复没有误开或漏开。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import Remote  # noqa: E402
from run_chapter1 import q  # noqa: E402

SNAP = Path(__file__).resolve().parent / "artifacts" / "r3" / "continuation_pause.json"
ENABLED = "COALESCE(story_bible->'continuation_policy'->>'enabled','false') IN ('true','True')"
WHERE = f"deleted_at IS NULL AND state IN ('RUNNING','FAILED','READY') AND {ENABLED}"


def pause(r: Remote) -> None:
    raw = q(
        r,
        "SELECT id::text || chr(9) || (story_bible->'continuation_policy')::text "
        f"FROM novel_works WHERE {WHERE}",
    )
    rows = []
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        work_id, policy = line.split("\t", 1)
        rows.append({"id": work_id, "policy": json.loads(policy)})
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    SNAP.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if not rows:
        print("no continuation to pause")
        return
    updated = q(
        r,
        "UPDATE novel_works SET story_bible = jsonb_set("
        "story_bible, '{continuation_policy,enabled}', 'false'::jsonb, false) "
        f"WHERE {WHERE} RETURNING id::text",
    )
    ids = [line for line in updated.splitlines() if len(line.strip()) == 36]
    print(f"snapshot {len(rows)} paused {len(ids)}")


def restore(r: Remote) -> None:
    rows = json.loads(SNAP.read_text(encoding="utf-8"))
    for row in rows:
        policy = json.dumps(row["policy"], ensure_ascii=False)
        q(
            r,
            "UPDATE novel_works SET story_bible = jsonb_set("
            "story_bible, '{continuation_policy}', "
            f"$pol${policy}$pol$::jsonb, true) WHERE id = '{row['id']}'",
        )
    print(f"restored {len(rows)}")


def check(r: Remote) -> None:
    saved = {row["id"] for row in json.loads(SNAP.read_text(encoding="utf-8"))}
    raw = q(
        r,
        "SELECT id::text || chr(9) || (story_bible->'continuation_policy')::text "
        "FROM novel_works WHERE deleted_at IS NULL "
        "AND story_bible ? 'continuation_policy' AND NOT " + ENABLED,
    )
    missing = [line for line in raw.splitlines() if "\t" in line and line.split("\t")[0] not in saved]
    print(f"snapshot {len(saved)} disabled_not_in_snapshot {len(missing)}")
    for line in missing:
        print(" ", line[:240])
    print(
        "enabled_now",
        q(r, f"SELECT count(*) FROM novel_works WHERE deleted_at IS NULL AND {ENABLED}").strip(),
    )


def main() -> None:
    actions = {"pause": pause, "restore": restore, "check": check}
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in actions:
        raise SystemExit("usage: continuation_pause.py pause|restore|check")
    with Remote() as r:
        actions[action](r)


if __name__ == "__main__":
    main()
