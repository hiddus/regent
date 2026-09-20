"""变异反证：VALIDATE/ACCEPT 默认末场路径若被恢复，定位接线测试必须变红。

用法：`$env:PYTHONPATH='core/src'; python deploy/novel/mutate_check_repair_locate.py`
退出码非 0 = 变异未被咬住（测试无意义）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/directing_script_loop.py"
TEST = ROOT / "tests/unit/novel/test_repair_locate_wiring.py"

# 变异：在 _begin_located_scene_repair 失败时静默改回末场（旧行为）
MUTATE_SNIPPET = """
def _begin_located_scene_repair(
    production, sp, *, fails, instruction_prefix, take=None
):
    texts = list(sp.get("scene_texts") or [])
    last_i = len(texts) - 1 if texts else 0
    sp["scene_index"] = last_i
    if texts:
        sp["scene_texts"] = texts[: last_i + 1]
    sp["scene_revision_instruction"] = str(instruction_prefix) + "（mutant last-scene）"
    production["phase"] = "WRITE_SCENE"
"""


def _source_has_locate_call() -> bool:
    src = TARGET.read_text(encoding="utf-8")
    return (
        "_begin_located_scene_repair(" in src
        and "locate_fail_messages" in src
        and "禁止默认修末场" in src
    )


def _inject_mutant(src: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_begin_located_scene_repair":
            start, end = node.lineno - 1, node.end_lineno
            return "".join(lines[:start]) + MUTATE_SNIPPET + "".join(lines[end:])
    raise SystemExit("anchor _begin_located_scene_repair not found")


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    if not _source_has_locate_call():
        print("[FAIL] 生产源码未包含定位接线，反证无意义")
        return 2
    mutant = _inject_mutant(src)
    backup = src
    TARGET.write_text(mutant, encoding="utf-8")
    print("[MUTANT] injected last-scene fallback into _begin_located_scene_repair")
    try:
        import subprocess

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(TEST),
            "-p",
            "no:cacheprovider",
            "-q",
            "--tb=line",
        ]
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out[-4000:])
        need_red = [
            "test_validate_locates_front_dup_not_last_scene",
            "test_accept_render_locates_from_validation_issues",
            "test_validate_unlocatable_stops_no_write_scene",
        ]
        red_ok = proc.returncode != 0 and any(n in out for n in need_red)
        # 变异恢复默认末场后：定位用例应 FAILED（returncode != 0）
        failed_names = [
            n
            for n in need_red
            if any(
                line.startswith("FAILED") and n in line
                for line in out.splitlines()
            )
            or (n in out and "failed" in out.lower())
        ]
        if not red_ok or not failed_names:
            # 至少要有明确 FAILED 行
            has_failed_line = any(line.startswith("FAILED") for line in out.splitlines())
            if proc.returncode == 0 or not has_failed_line:
                print("[FAIL] 变异后定位测试未变红，测试无守卫力")
                return 1
        print("[OK] 变异被咬住：默认末场恢复后定位用例变红")
        return 0
    finally:
        TARGET.write_text(backup, encoding="utf-8")
        print("[RESTORE] directing_script_loop.py 已还原")


if __name__ == "__main__":
    raise SystemExit(main())
