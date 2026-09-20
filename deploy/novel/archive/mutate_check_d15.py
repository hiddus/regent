"""反证：缺陷 15（引文覆盖判据忽略空白）必须被测试真守住。"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRECTION = os.path.join(
    ROOT, "core", "src", "regent", "novel", "application", "direction.py"
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
T = "tests/unit/novel/test_d15_whitespace.py"

# 1 把归一拆掉——回到字节级比对，paragraph-compressed 引文就被判成伪造
M1_REAL = """    q = _normalize_ws(quote)
    t = _normalize_ws(text)
    if q in t:"""
M1_MUTANT = """    q = quote
    t = text
    if q in t:
        return True"""

# 2 把归一改成无操作——同上
M2_REAL = """def _normalize_ws(s: str) -> str:
    return _WS.sub("", s.translate(_FULLWIDTH_PUNCT))"""
M2_MUTANT = """def _normalize_ws(s: str) -> str:
    return s"""

# 3 fabrication 漏放：把"整段"误判成"非空白归一后才不通过"
# 这条不需要——已有的 _covered 仍然在用，只是 0.6 覆盖 + 12 字符下界；fabrication
# 0 覆盖，必被拒。留作显式护栏：M1 红时它不能红。

MUTATIONS = (
    ("1 不归一直接比对", M1_REAL, M1_MUTANT,
     T + "::test_is_grounded_treats_whitespace_as_formatting",
     (T + "::test_quote_check_still_rejects_fabrication_after_whitespace_normalization",)),
    ("2 归一退化成无操作", M2_REAL, M2_MUTANT,
     T + "::test_is_grounded_treats_whitespace_as_formatting",
     (T + "::test_quote_check_still_rejects_fabrication_after_whitespace_normalization",)),
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    with open(DIRECTION, encoding="utf-8") as fh:
        original = fh.read()

    failures: list[str] = []
    try:
        for label, real, mutant, probe, guards in MUTATIONS:
            if original.count(real) != 1:
                failures.append(f"{label}: 锚点命中 {original.count(real)} 次，应为 1")
                continue
            mutated = original.replace(real, mutant, 1)
            if mutated == original:
                failures.append(f"{label}: 变异未生效")
                continue
            try:
                with open(DIRECTION, "w", encoding="utf-8") as fh:
                    fh.write(mutated)
                probe_rc = run(probe)
                guard_rcs = {g: run(g) for g in guards}
            finally:
                with open(DIRECTION, "w", encoding="utf-8") as fh:
                    fh.write(original)
                with open(DIRECTION, encoding="utf-8") as fh:
                    if fh.read() != original:
                        failures.append(f"{label}: 复原失败")
                        return 1

            if probe_rc == 0:
                failures.append(f"{label}: 探针仍绿")
            for g, rc in guard_rcs.items():
                if rc != 0:
                    failures.append(f"{label}: 护栏变红 -> {g}")
            status = "OK" if probe_rc != 0 and all(v == 0 for v in guard_rcs.values()) else "FAIL"
            print(f"  [{status}] {label}")
    finally:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original)

    if failures:
        print("\n".join(failures))
        return 1
    print("全部变异均被抓住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
