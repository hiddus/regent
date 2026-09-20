"""反证：缺陷 4 的两处修复必须被测试真守住。

1. 「事务不得跨越模型调用」——去掉包装器的收口 → 探针必须红；
   护栏（chat 也受保护 / 没开事务不凭空提交 / 属性透传）必须仍绿。
2. 同上，针对第二个网络出口 ``chat``：漏一个就等于没保护。
3. 「重试必须有间隔」——把后退改回"一律立刻释放租约" → 探针必须红；
   护栏是另一份文件的收口测试（不相关维度必须不受影响），必须仍绿。

用法：python deploy/novel/mutate_check_d4.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROD = os.path.join(ROOT, "core", "src", "regent", "novel", "application", "production.py")
WORKS = os.path.join(ROOT, "core", "src", "regent", "novel", "application", "works.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

T_TX = "tests/unit/novel/test_d4_transaction_free.py"
T_RETRY = "tests/unit/novel/test_d4_coordination_retry.py"

STRUCTURED_REAL = """    async def generate_structured(self, **kwargs: Any) -> Any:
        await self._detach()
        return await self._inner.generate_structured(**kwargs)"""
STRUCTURED_MUTANT = """    async def generate_structured(self, **kwargs: Any) -> Any:
        return await self._inner.generate_structured(**kwargs)"""

CHAT_REAL = """    async def chat(self, **kwargs: Any) -> Any:
        await self._detach()
        return await self._inner.chat(**kwargs)"""
CHAT_MUTANT = """    async def chat(self, **kwargs: Any) -> Any:
        return await self._inner.chat(**kwargs)"""

# 退回旧行为：失败后一律立刻释放租约（三个 worker 同瞬间抢光预算）
BACKOFF_REAL = """        if run.state == ChapterRunState.TERMINAL_FAILED.value:
            await release_run_lease(session, run=run, owner=_LEASE_OWNER)
        else:
            # 可重试的失败按住一会儿再放回场上；终态才彻底释放。
            await acquire_run_lease(
                session, run=run, owner=_LEASE_OWNER, ttl=_RETRY_BACKOFF
            )"""
BACKOFF_MUTANT = """        await release_run_lease(session, run=run, owner=_LEASE_OWNER)"""

TX_PROBES = (
    "test_generate_commits_before_the_request_goes_out",
    "test_chat_is_protected_too",
    "test_no_commit_when_there_is_no_open_transaction",
    "test_unwrapped_attributes_pass_through",
    "test_every_call_detaches_again_after_new_writes",
)
RETRY_PROBES = (
    "test_coordination_failure_keeps_the_run_leased",
    "test_no_worker_picks_the_run_up_while_it_is_held",
    "test_the_hold_is_a_bounded_delay_not_a_dead_end",
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def mutate(target: str, real: str, mutant: str, label: str,
           probe: str, guards: tuple[str, ...]) -> bool:
    with open(target, encoding="utf-8") as fh:
        original = fh.read()
    if original.count(real) != 1:
        print(
            f"[FAIL] {label}: 找不到唯一锚点（count={original.count(real)}）"
            "——改动后请同步本脚本"
        )
        return False
    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(original.replace(real, mutant))
        rc_probe = run(probe)
        rc_guards = [run(g) for g in guards]
    finally:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(original)
    ok = rc_probe != 0 and all(rc == 0 for rc in rc_guards)
    print(f"  {label}: 探针 rc={rc_probe}（期望非 0）, 护栏 {list(zip(guards, rc_guards))}（期望全 0）")
    return ok


def main() -> int:
    probes = [f"{T_TX}::{p}" for p in TX_PROBES] + [f"{T_RETRY}::{p}" for p in RETRY_PROBES]
    before = [run(p) for p in probes]
    if any(rc != 0 for rc in before):
        print("[FAIL] 变异前就有测试不过，反证无意义:", list(zip(probes, before)))
        return 2

    print("变异 1：generate_structured 不再收口事务")
    ok1 = mutate(
        PROD, STRUCTURED_REAL, STRUCTURED_MUTANT, "structured-detach",
        probe=f"{T_TX}::test_generate_commits_before_the_request_goes_out",
        guards=(
            f"{T_TX}::test_chat_is_protected_too",
            f"{T_TX}::test_no_commit_when_there_is_no_open_transaction",
            f"{T_TX}::test_unwrapped_attributes_pass_through",
        ),
    )
    print("变异 2：chat 不再收口事务（第二个网络出口漏保护）")
    ok2 = mutate(
        PROD, CHAT_REAL, CHAT_MUTANT, "chat-detach",
        probe=f"{T_TX}::test_chat_is_protected_too",
        guards=(
            f"{T_TX}::test_generate_commits_before_the_request_goes_out",
            f"{T_TX}::test_unwrapped_attributes_pass_through",
        ),
    )
    print("变异 3：失败后立刻释放租约（重试不再有间隔）")
    ok3 = mutate(
        WORKS, BACKOFF_REAL, BACKOFF_MUTANT, "retry-backoff",
        probe=f"{T_RETRY}::test_no_worker_picks_the_run_up_while_it_is_held",
        guards=(
            f"{T_TX}::test_generate_commits_before_the_request_goes_out",
            f"{T_TX}::test_chat_is_protected_too",
        ),
    )

    after = [run(p) for p in probes]
    print("还原后探针 rc =", after, "（期望全 0）")
    ok = ok1 and ok2 and ok3 and all(rc == 0 for rc in after)
    print("[PASS] 缺陷 4 的两处修复都被真守卫" if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
