"""GQ-4 promotion operator entry (fail-closed).

Dry-run (default):
  python ops/apply_gq4_promotion.py
  python ops/apply_gq4_promotion.py --report docs/gq3-experiment-report-2026-07-31.json

Execute only after PROMOTE + gate pass:
  python ops/apply_gq4_promotion.py --execute --write-accepted-note
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "src"))

from regent.application.generation_strategy_promotion import (  # noqa: E402
    apply_gq4_promotion,
    evaluate_gq4_promotion,
)
from regent.domain.errors import DomainError  # noqa: E402


def latest_report() -> Path:
    cands = sorted((ROOT / "docs").glob("gq3-experiment-report-*.json"))
    if not cands:
        raise SystemExit("no docs/gq3-experiment-report-*.json — run ops/gq3_production_report.py")
    return cands[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--decision-ref", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--write-accepted-note", action="store_true")
    args = parser.parse_args()

    path = args.report or latest_report()
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = payload.get("report") or payload
    decision_ref = args.decision_ref or f"file:{path.name}"

    print(f"report={path}")
    print(f"decision={report.get('decision')} rationale={report.get('rationale')}")
    if report.get("funnel_degraded"):
        print(f"FUNNEL_DEGRADED={json.dumps(report.get('funnel_health'), ensure_ascii=False)}")
    preview = evaluate_gq4_promotion(
        report, kill_switch=False, decision_record_ref=decision_ref
    )
    print(f"gate_preview={json.dumps(preview, ensure_ascii=False)}")

    try:
        gate = apply_gq4_promotion(
            report, kill_switch=False, decision_record_ref=decision_ref
        )
    except DomainError as exc:
        print(f"BLOCKED: {exc}")
        print("GQ4_ACTION=keep PENDING")
        raise SystemExit(2) from exc

    print(f"GATE_PASSED reason={gate.get('reason')}")
    if not args.execute:
        print("DRY_RUN_OK — pass --execute only when Owner accepts GQ-4")
        return

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    note_path = ROOT / "docs" / f"decision-note-gq4-accepted-{stamp}.md"
    if args.write_accepted_note:
        note_path.write_text(
            f"""# Decision Note — GQ-4 晋级（ACCEPTED）

> 状态：**ACCEPTED**  
> 日期：{stamp}  
> 报告：`{path.as_posix()}`

## 结论

GQ-3 报告为 `PROMOTE_AGENTIC_CANDIDATE`；`apply_gq4_promotion` 已通过。  
运行时默认切换为 `agentic`（kill switch 仍可强制回落）。

## 记录

| 字段 | 值 |
|---|---|
| Decision | ACCEPTED |
| Report | {path.name} |
| decision_record_ref | {decision_ref} |
| Author | rechaos |
""",
            encoding="utf-8",
        )
        print(f"wrote {note_path}")
        pending = ROOT / "docs" / "decision-note-gq4-pending-2026-07-31.md"
        if pending.is_file():
            text = pending.read_text(encoding="utf-8")
            pending.write_text(
                text.replace(
                    "**PENDING** — GQ-4 未晋级",
                    f"**SUPERSEDED** — 见 `{note_path.name}`",
                ),
                encoding="utf-8",
            )

    # Flip default via dedicated helper (recreate + must redeploy console).
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ops" / "set_generation_strategy.py"),
            "--strategy",
            "agentic",
        ],
        cwd=ROOT,
    )
    if r.returncode != 0:
        raise SystemExit("set_generation_strategy failed")
    print("GQ4_EXECUTED")


if __name__ == "__main__":
    main()
