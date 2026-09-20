"""一次性补丁：文档记录 P0-4 完成状态。"""
from __future__ import annotations

import pathlib


def patch(path_str: str, subs: list[tuple[str, str]]) -> None:
    path = pathlib.Path(path_str)
    text = path.read_text(encoding="utf-8")
    for old, new in subs:
        assert text.count(old) == 1, f"{path}: count={text.count(old)} for {old[:60]!r}"
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print("patched", path)


P04_PLAN_OLD = "- [ ] **P0-4 运行版本与租约隔离**：提交和释放租约核验 owner、fencing token 与输入版本；用户改意/路径变更递增 input_version 并使旧产物失效；调用输入指纹覆盖模型与采样等配置。验收：租约过期后旧 worker 无法提交；改意前的返回结果不能覆盖新方向；重复恢复无额外调用。"

P04_PLAN_NEW = "- [x] **P0-4 运行版本与租约隔离**（2026-09-08 完成）：新增 `lease_is_valid()`/`require_run_lease()`，提交前复核 owner + fencing token + 有效期；`advance_step` 在调用窗口结束后**回查数据库**（不信任 `expire_on_commit=False` 的内存对象）复核租约与 `input_version`，失效则丢弃产出并留 `chapter.result_discarded` 事件。新增 `works.bump_input_version()`，用户提交指导与关键路径变更时递增 `input_version` 并使在途章节旧方向产出作废。调用配置指纹 `config_fingerprint(model, sampling)` 写入 `ModelCall.sampling.config_hash`，同键换模型或 temperature 判为 `CallConflict`。验收证据：`test_lease_version_isolation.py` 8 例（token 失配拒绝、过期失效、接管后 token 递增、改意持久化与留痕、旧结果被丢弃、配置指纹冲突不复用）。真实 PostgreSQL 并发验证仍属 P0-5。"

patch(
    "Novel-Engine-Plan.md",
    [
        (P04_PLAN_OLD, P04_PLAN_NEW),
        (
            "| 2026-09-08 | v6.5 | P0-1~P0-3 落地：新增迁移 `20260908_0052`（`reconcile_count`）、`recover_novel_calls()` 与 Worker 启动/周期恢复、失败异常保留 `request_id`、预算改按已结算金额校验并做按章原子预留。小说域回归 99 例通过。 |",
            "| 2026-09-08 | v6.5 | P0-1~P0-3 落地：新增迁移 `20260908_0052`（`reconcile_count`）、`recover_novel_calls()` 与 Worker 启动/周期恢复、失败异常保留 `request_id`、预算改按已结算金额校验并做按章原子预留。 |\n"
            "| 2026-09-08 | v6.6 | P0-4 落地：租约 owner/fencing token/有效期复核、调用窗口后回查数据库判定失效、`bump_input_version()` 使用户改意与路径变更作废旧方向产出、调用配置指纹入幂等键。小说域回归 107 例通过。 |",
        ),
    ],
)

patch(
    "Novel-Engine-Tech-Spec.md",
    [
        (
            """- logical call 与 attempt 分离；已成功 logical call 恢复时复用，不重复内部结算；外部不确定结果按 §4.4 处理。""",
            """- logical call 与 attempt 分离；已成功 logical call 恢复时复用，不重复内部结算；外部不确定结果按 §4.4 处理。
- **同键判定必须包含调用配置**：`config_fingerprint(model, sampling)` 写入 `ModelCall.sampling.config_hash`，同键换模型或采样参数是 `CallConflict`，不是复用。
- **运行租约复核（P0-4）**：写回结果前复核 owner、fencing token 与有效期，三者缺一即丢弃产出；模型调用在事务外进行，会话默认 `expire_on_commit=False`，因此必须**回查数据库列值**，不能读内存中的 run 对象。
- **输入版本（P0-4）**：用户提交指导或关键路径变更时递增 `input_version`；调用窗口内版本变化即判定本次产出属于旧方向，作废并留痕，不得覆盖新方向。""",
        ),
        (
            """**仍未闭合，不能按目标协议推定已经生效**：真实 PostgreSQL 下的并发抢占与崩溃注入未验证（P0-5），迁移 0051/0052 只做过 SQLite 升级；提交/释放路径尚未按 fencing token 与 input_version 做数据库条件校验（P0-4）；供应商查询能力依赖 provider 实现 `lookup_call`，默认无查询能力时只能按估价结清。""",
            """运行租约与输入版本隔离已接入（P0-4，2026-09-08）：`production.lease_is_valid()`/`require_run_lease()` 复核 owner、fencing token 与有效期；`advance_step` 在调用窗口后回查数据库列值判定失效并留 `chapter.result_discarded`；`works.bump_input_version()` 由用户指导与关键路径变更触发。调用配置指纹入幂等键。

**仍未闭合，不能按目标协议推定已经生效**：真实 PostgreSQL 下的并发抢占与崩溃注入未验证（P0-5），迁移 0051/0052 只做过 SQLite 升级；供应商查询能力依赖 provider 实现 `lookup_call`，默认无查询能力时只能按估价结清；`input_version` 变更只作废未写回的产出，已接受的场景产物尚不做依赖级重演。""",
        ),
        (
            "| 2026-09-08 | v5.4 | §4.4 补 UNKNOWN 按对账次数终止、失败保留 `request_id`、恢复清扫与静默期、预算按已结算金额校验；§5 配额结算键加 attempt 维度；§14 同步 P0-1~P0-3 已落地与剩余 P0-4/P0-5 边界。 |",
            "| 2026-09-08 | v5.4 | §4.4 补 UNKNOWN 按对账次数终止、失败保留 `request_id`、恢复清扫与静默期、预算按已结算金额校验；§5 配额结算键加 attempt 维度；§14 同步 P0-1~P0-3 已落地与剩余 P0-4/P0-5 边界。 |\n"
            "| 2026-09-08 | v5.5 | §5 补调用配置指纹、租约复核须回查数据库、input_version 使旧方向产出作废；§14 同步 P0-4 已落地与剩余 P0-5 边界。 |",
        ),
    ],
)
