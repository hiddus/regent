"""反证：缺陷 8 的事件去重必须被测试真守住。

退回旧行为（``extend`` 全量累加）→ 探针必须红；不相关的护栏必须仍绿。

用法：python deploy/novel/mutate_check_d8.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRECTION = os.path.join(
    ROOT, "core", "src", "regent", "novel", "application", "direction.py"
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
T = "tests/unit/novel/test_d8_dupe_events.py"

M1_REAL = """    seen = {str(e.get("statement", "")) for e in take["events"]}
    for event in events:
        dumped = event.model_dump(mode="json")
        key = str(dumped.get("statement", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        take["events"].append(dumped)"""
M1_MUTANT = """    take["events"].extend(e.model_dump(mode="json") for e in events)"""

M2_REAL = """        if not key or key in seen:
            continue"""
M2_MUTANT = """        if False:
            continue"""

M3_REAL = """        if not key or key in seen:
            continue
        seen.add(key)"""
M3_MUTANT = """        if key and key in seen:
            continue"""

PROBE_TESTS = (
    "test_echoed_events_are_not_accumulated",
    "test_repeated_echoes_across_beats_stay_at_one_copy",
    "test_duplicates_within_one_beat_are_collapsed",
    "test_new_events_still_land_and_order_is_preserved",
    "test_other_event_fields_survive_the_merge",
)

MUTATIONS = (
    ("1 退回 extend 全量累加", M1_REAL, M1_MUTANT,
     "test_repeated_echoes_across_beats_stay_at_one_copy",
     ("test_new_events_still_land_and_order_is_preserved",
      "test_other_event_fields_survive_the_merge")),
    ("2 去掉 seen.add（同批次内重复不再收敛）", M3_REAL, M3_MUTANT,
     "test_duplicates_within_one_beat_are_collapsed",
     ("test_new_events_still_land_and_order_is_preserved",
      "test_other_event_fields_survive_the_merge")),
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def mutate(real: str, mutant: str, label: str, probe: str, guards: tuple[str, ...]) -> bool:
    with open(DIRECTION, encoding="utf-8") as fh:
        original = fh.read()
    if original.count(real) != 1:
        print(f"[FAIL] {label}: 找不到唯一锚点（count={original.count(real)}）"
              "——改动后请同步本脚本")
        return False
    try:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original.replace(real, mutant))
        rc_probe = run(f"{T}::{probe}")
        rc_guards = [run(f"{T}::{g}") for g in guards]
    finally:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original)
    ok = rc_probe != 0 and all(rc == 0 for rc in rc_guards)
    print(f"  {label}: 探针 rc={rc_probe}（期望非 0） 护栏 {rc_guards}（期望全 0）"
          f" → {'OK' if ok else 'BAD'}")
    return ok


def main() -> int:
    probes = [f"{T}::{p}" for p in PROBE_TESTS]
    before = [run(p) for p in probes]
    if any(rc != 0 for rc in before):
        bad = [p for p, rc in zip(probes, before) if rc != 0]
        print("[FAIL] 变异前就有测试不过，反证无意义:", bad)
        return 2

    results = [mutate(r, m, label, probe, guards)
               for label, r, m, probe, guards in MUTATIONS]

    after = [run(p) for p in probes]
    print("还原后探针 rc =", set(after), "（期望 {0}）")
    ok = all(results) and all(rc == 0 for rc in after)
    print("[PASS] 缺陷 8 的事件去重被真守卫" if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
