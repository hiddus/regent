# 完整三向对齐校验：需求文档 ↔ 代码实现 ↔ README（2026-07-30）

> **修复后状态（2026-07-30 收尾轮）**：本文件原为静态三向校验报告。针对「未对齐 / 建议项」已做代码核实与落地：
> - **PRD §7.1–7.3 隐私** → ✅ **已修**（consent 表+API+采集闸门；PII 分级最小化策略；`privacy_retention_days` + Worker/API 超期匿名化）
> - **ops 历史脚本** → ✅ **已修**（`diag_*`/`fix_*`/`_server_*` 迁入 `ops/archive/oneoff/`）
> - **G0 provider query→resolve** → ✅ **推进**（`ReconciliationWorker.tick` 在 RECONCILING 后走 durable probe / 超期 MANUAL_REVIEW；完整跨 provider 生产 I/O 仍见 Spec §25）
> - README 四处修正、registered 补登：保持有效；详见文末「附录：修复后状态表」。

> 方法：两个审计代理分别做「README 对齐审计」与「需求→代码完整功能清单」，本人对关键发现做了 trust-but-verify（亲读 deploy/desktop/ops/tests README、privacy_service.py、ops 根目录脚本清单、compose.yaml 与 registered-unimplemented 清单）。本轮为静态校验，未实跑 pytest（依赖 PostgreSQL + 模型 Provider）。
> 前序报告：`docs/gap-analysis-2026-07-30.md`、`docs/gap-recheck-2026-07-30.md`、`docs/gap-recheck2-2026-07-30.md`。

## 一、总体结论

**三向（需求 ↔ 代码 ↔ README）整体对齐度很高。** 在 18 份项目自有 README 中，14 份与代码/需求一致；本轮发现并**已修正 4 处 README 失实/缺失**；发现并**已补登 1 处真实代码差距**（PRD §7.1–7.3 隐私）。需求→代码功能清单除 1 处新发现的隐私缺口与若干"设计上候选/已知限制"外，P0/P1/P2 主体均已真实落地。

> 收尾轮后：该隐私缺口与 ops 迁移建议已关闭；G0 对账路径已再推进一刀（见附录）。

## 二、README 对齐审计（18 份项目自有 README）

> 已排除 `node_modules/` `.venv/` `.pytest_cache/` 下第三方包 README。

| README | 结论 | 处理 |
|---|---|---|
| `docs/README.md`、`docs/definitions/README.md`、`docs/archive/README.md` | 一致 | — |
| `capabilities/README.md` | 一致 | — |
| `scripts/README.md`、`canvases/README.md` | 一致 | — |
| `core/README.md`、`core/migrations/README.md`、`agent/README.md`、`api/README.md`、`application/README.md` | 一致（结构清单由真实文件生成） | — |
| `apps/regent-console/src/README.md`、`apps/regent-desktop/src/README.md`、`src-tauri/README.md` | 一致 | — |
| **`deploy/README.md`** | **失实（中）**：称 `compose.yaml` 编排 `regent-egress`（Squid），实际默认编排仅 3 服务，出口代理未接入 | **已修正**：标注 squid 为参考配置、默认未接入、fail-closed 属规划态 |
| **`apps/regent-desktop/README.md`** | **失实（中）**：把探索性非目标当正式交付特性呈现 | **已修正**：顶部加 PRD §12/Spec §25「探索性非目标，不计入验收」声明 |
| **`ops/README.md`** | **失实（中）**：称脚本已规整进 `archive/oneoff/`，实际根目录仍遗留 ≥30 个 `diag_*`/`fix_*`/`_server_*` 历史脚本 | **已修正（描述）→ 收尾轮已迁完脚本并更新 README** |
| **`tests/README.md`** | **缺失标注（低）**：P2 条件/候选测试未标状态 | **已修正**：为 3 个 P2 测试加「候选/条件承诺」标注 |

## 三、需求 → 代码功能清单（节选关键结论）

完整状态表见审计代理产出（P0 全链路、P1 R1–R8、P2-1..P2-8、G0、隐私、北极星、护栏、证据五分类、ReleaseCandidate 闸门等均逐项核验）。要点：

- **已实现且对接需求**：P0 全链路（含 P0#5 仓内可复核 DecisionRecord）、P1 R1–R6、P2-1 调度、P2-2 Runtime Profile、P2-3 Impact Graph、P2-4 Eval Harness、P2-5 HMAC（opt-in）、证据五分类、隐私 §7.4 导出/删除、北极星+护栏、控制台实时名册。
- **设计上候选/已知限制（非差距，按治理不得宣称验收，已登记）**：SelfImprovementRun（P2-8）、Hive 自由拓扑、桌面端 Tauri、P2-6 Champion/Challenger、P2-7 受控生产发布、G0 生产级闭环（部分）、R7 无 Playwright 时 dry-run。

## 四、新发现的真实代码差距（已补登）

| 项 | 来源 | 证据 | 状态 |
|---|---|---|---|
| 隐私告知同意 / PII 分级最小化 / 保留期匿名化 | PRD §7.1 / §7.2 / §7.3 | 审计时 `privacy_service.py` 仅实现 §7.4 | **收尾轮已修**：见附录 |

> 说明（审计当时）：§7.4 导出已做 PII 截断，但 §7.1 / §7.2 / §7.3 均无实现。收尾轮已补全。

## 五、本轮已执行的"补充对齐"动作

1. `deploy/README.md` — 删除虚构的 `regent-egress` 服务描述，标注 squid 为参考配置、默认未接入。
2. `apps/regent-desktop/README.md` — 顶部加「探索性非目标，不计入 P0/P1/P2 验收」声明。
3. `ops/README.md` — 如实描述根目录历史脚本待迁移现状，列 cleanup 待办。
4. `tests/README.md` — 为 P2 条件/候选集成测试加状态标注。
5. `docs/registered-unimplemented-2026-07-30.md` — 补登 PRD §7.1–7.3 隐私差距，使权威状态清单与代码现实对齐。

## 六、建议（未在本轮执行，供决策）

- ~~**隐私 §7.1–7.3**~~ → **收尾轮已实现**。
- ~~**ops 历史脚本**~~ → **收尾轮已迁移**。
- **最终裁定**：实跑 `pytest`（PG + 模型 Provider）作为 P0 毕业与全链路通过的最终硬证据（治理建议，非本审计代码缺口）。

## 七、无新增未登记差距扫描

除 PRD §7.1–7.3 隐私外，未发现在需求中存在、代码未做、且权威清单未登记的其它真实差距；应用层无 `NotImplementedError`/`TODO`/`STUB` 残留。

---

## 附录：修复后状态表（2026-07-30 收尾轮）

| # | 审计项 | 修复后状态 | 证据 |
|---|---|---|---|
| A | README ×4 失实/缺失 | ✅ 已修（审计轮） | deploy / desktop / ops / tests README |
| B | PRD §7.1 告知同意 | ✅ **已修** | `privacy_consents`（0038）+ `grant/withdraw/notice` API；Goal 创建时登记同意；采集路径 `require_consent_for_scope` |
| C | PRD §7.2 PII 分级最小化 | ✅ **已修** | `PiiClass` + `PII_FIELD_POLICY` + `classify_and_minimize`；Observation 拒收 RESTRICTED；对话内容脱敏 |
| D | PRD §7.3 保留期匿名化 | ✅ **已修** | `REGENT_PRIVACY_RETENTION_DAYS`；`anonymize_expired`；Worker 周期 + `/v1/governance/privacy/anonymize-expired` |
| E | ops `diag_*`/`fix_*`/`_server_*` | ✅ **已修** | 迁入 `ops/archive/oneoff/`；`ops/README.md` 更新 |
| F | G0 provider query→resolve | ⚠️ **半落地→推进** | `resolve_reconciling_via_query`（Deployment durable probe + 超期 MANUAL_REVIEW）；跨 provider 真实网络 query 仍属后续 |
| G | 设计候选（P2-8/Hive/桌面等） | ℹ️ 非本轮差距 | 保持登记，不得宣称验收 |
