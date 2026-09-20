"""A-06 反证：把刚修好的盲评硬门逐个改回旧写法，确认新测试真的会失败。

出现 "STILL PASS" 说明该测试没有咬住问题。脚本结束会把所有文件还原。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"

D = ROOT / "core/src/regent/novel/domain/evaluation.py"
A = ROOT / "core/src/regent/novel/application/evaluation.py"
T = "tests/unit/novel/test_eval_and_canary.py"

MUTATIONS: list[tuple[str, Path, str, str]] = [
    (
        "M1 按评分行数计数（多评者虚高样本量）",
        D,
        "    return ArmReport(arm=arm, n=len(by_sample), ratings=len(rows), means=means)",
        "    return ArmReport(arm=arm, n=len(rows), ratings=len(rows), means=means)",
    ),
    (
        "M2 收分不做样本/评者注册校验",
        A,
        """        sample = records.get(str(score.sample_id))
        if sample is None:
            raise GuardViolation(
                f"样本未注册：{score.sample_id}", available_actions=["add_samples"]
            )
        if raters and str(score.rater) not in raters:
            raise GuardViolation(
                f"评者未预注册：{score.rater}", available_actions=["register_rater"]
            )
        if score.arm not in sample.arms_in_order:
            raise GuardViolation(
                f"样本 {score.sample_id} 上没有 arm {score.arm}"
            )
""",
        "        if False:\n            pass\n",
    ),
    (
        "M3 裁决不做证据齐备检查",
        D,
        """    missing = missing_evidence(evidence, arms=arms, budget=budget, sample=sample)
    if missing:
        return "HOLD", missing
""",
        "    missing = ()\n",
    ),
    (
        "M4 信任库里那份配置指纹（不重算）",
        A,
        """    current = domain.fingerprint_of(config)
    if current != str(row.config_fingerprint or ""):""",
        """    current = str(row.config_fingerprint or "")
    if False:""",
    ),
    (
        "M5 不核对报告指纹",
        A,
        """    expected = domain.report_fingerprint_of(report, config_fingerprint=current)
    if expected != str(row.report_fingerprint or ""):""",
        """    expected = str(row.report_fingerprint or "")
    if False:""",
    ),
    (
        "M6 评者载荷里带上 arm 顺序（盲评失明）",
        D,
        '            "options": [chr(ord("A") + i) for i in range(len(self.arms_in_order))],',
        '            "options": [chr(ord("A") + i) for i in range(len(self.arms_in_order))],\n'
        '            "arms_in_order": list(self.arms_in_order),',
    ),
    (
        "M7 成本证据缺失也当作已提供",
        A,
        "        cost_provided=tuple(a for a in arms if int(cost.get(a, 0)) > 0),",
        "        cost_provided=tuple(arms),",
    ),
]


def run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", T, "-q", "--no-header", "--tb=no", "-x"],
        cwd=ROOT, capture_output=True, text=True,
    )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return proc.returncode == 0, (lines[-1] if lines else proc.stdout[-200:])


def main() -> int:
    paths = {p for _n, p, _o, _x in MUTATIONS}
    originals = {p: p.read_text(encoding="utf-8") for p in paths}
    bad = 0
    try:
        for name, path, old, new in MUTATIONS:
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"[SKIP] {name}: 锚点命中 {text.count(old)} 次")
                bad += 1
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            passed, tail = run_tests()
            verdict = "STILL PASS（没咬住）" if passed else "FAILED（咬住了）"
            print(f"[{verdict}] {name}\n          {tail}")
            if passed:
                bad += 1
            path.write_text(originals[path], encoding="utf-8")
    finally:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")
    print("\n=== 反证结束：%s ===" % ("全部咬住" if bad == 0 else f"{bad} 项没咬住"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
