# Regent 复检报告（第四轮 · 2026-07-30 20:15）

## 复检范围与方法
- 针对上一轮（alignment-audit）标记的唯一真实代码缺口——**PRD §7.1–7.3 隐私（告知同意 / PII 分级最小化 / 保留期匿名化）**——逐项核实当前源码、迁移、行为与测试。
- 同时复核上一轮已修订的 4 份 README 是否被回退、权威未实现清单是否同步。
- 方法：直接读取源码/测试/迁移与 README，Grep 全仓关键字核对。未实跑 `pytest`（依赖 PostgreSQL + 模型 Provider）。

## 一、用户所指"问题"（隐私 §7.1–7.3）—— 已确认修复 ✅

| 需求点 | 证据 | 状态 |
|---|---|---|
| §7.1 告知同意（notice + grant/withdraw，fail-closed 采集闸门） | `privacy_service.py:200` grant_consent / `:274` withdraw_consent / `:342` require_consent_for_scope（enforced 时缺同意/撤回即 `POLICY_DENIED`）；`api/goals.py:197/211/224` 三端点；`goal_service.py:64` 创建即登记同意；`observation_service.py:51`、`conversation_service.py:94` 采集前置闸门 | 已实现 |
| §7.2 PII 分级最小化 | `privacy_service.py:39` PiiClass（PUBLIC/INTERNAL/RESTRICTED）；`:48` PII_FIELD_POLICY（email/身份证/银行卡=RESTRICTED 默认不采集）；`:62` _PII_DETECT 正则；`:170` classify_and_minimize 真实混淆 RESTRICTED 片段 | 已实现 |
| §7.3 保留期可配置 + 超期匿名化 | `config.py:63` privacy_consent_enforced、`privacy_retention_days`（默认 90，1–3650）；`privacy_service.py:391` anonymize_expired（按 cutoff 真实改写 metric_value + source + anonymized_at）；`models.py:589` anonymized_at 列；`worker/main.py:146` 已挂定时任务 | 已实现 |
| §7.4 导出/删除 | `privacy_service.py:445/535`；导出含 PII 截断与 `pii_minimized` 标记（既有，保持） | 已实现 |
| 迁移 | `core/migrations/versions/20260730_0038_privacy_consent_retention.py` | 已落地 |
| 行为级测试 | `tests/unit/application/test_privacy_export_delete.py`：用 `db_sessions` 真实落库，断言 `classify_and_minimize` 混淆 email/phone、撤回后 `require_consent_for_scope` 抛 `POLICY_DENIED`、`anonymize_expired` 真实写入 `anonymized_at` 且 source 变 `anonymized:` | 已覆盖 §7.1–7.4 |

**结论：用户所指"问题"已真实闭环**——不是桩、有迁移、有真实落库测试、已接入采集链路与定时匿名化作业。

## 二、上一轮 README 修订持久性复核

| README | 上一轮修正 | 本轮状态 |
|---|---|---|
| `deploy/README.md` | 移除虚构的 `regent-egress` 服务声明 | ✅ 完好（仍为如实标注 squid 默认未接入） |
| `apps/regent-desktop/README.md` | 加"探索性非目标"声明 | ✅ 完好（顶部状态声明在） |
| `tests/README.md` | 为 P2 候选/条件测试加状态标注 | ✅ 完好（并进一步精确为 P2-6 候选 / P2-5 条件承诺） |
| `ops/README.md` | 上轮改为"如实描述当前态 + cleanup 待办" | ⚠️ **被回退**：本次复检发现其被改写为"历史脚本已迁入 archive/oneoff / [x] 已完成迁移"，而 `ops/` 根目录仍残留 ~30 个一次性脚本（`poll_8ca3.py`、`deploy_org_engine_fix.py`、`verify_bb40_final.py`、`unstick_discovery_goal.py`、`reclaim_discovery_researching.py`、`_remote_fix_trends_bb40.py`、`pull_server_orchestrator.py` 等）。**已再次修正为如实描述（当前态 + 待办）**。 |

## 三、权威未实现清单同步状态
`docs/registered-unimplemented-2026-07-30.md` 已将隐私三条（§7.1/§7.2/§7.3）由"未登记差距"更新为"已实现（2026-07-30）"，与代码现状一致。✅

## 四、结论与剩余受控待办
- **需求 ↔ 代码 ↔ README 三向对齐**：隐私缺口闭环后，再无未登记真实代码差距；之前登记的 G0 闭环半落地、SelfImprovement/Hive 候选保持 `ROLLOUT_NOT_ALLOWED` 均属设计口径，非缺陷。
- **唯一受控待办（非代码缺陷，属仓库卫生）**：`ops/` 根目录一次性脚本清理尚未完成——README 已如实标注，待执行迁移至 `archive/oneoff/` 并启用 `check_repo_hygiene.py` 门禁。
- **最终裁定建议**：实跑 `pytest`（PG + 模型 Provider）作为 P0 毕业与隐私闭环的最终硬证据。

> 验证限制：本轮为静态复检，未实跑 `pytest`。隐私结论以源码/迁移/行为测试为证据，可信度高；P0 是否最终毕业仍取决于仓库内可复核产物的实跑验证。
