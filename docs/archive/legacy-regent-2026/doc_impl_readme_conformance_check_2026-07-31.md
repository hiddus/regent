# Regent 实现 ↔ 需求文档 ↔ README 一致性核对（2026-07-31）

> 方法：以 `Regent-PRD.md`(CURRENT)、`Regent-Technical-Spec.md`(CURRENT)、`README.md` 为权威基线，对其声称的功能逐项做**代码级抽查**（下场读 `core/src/regent` 源码 + 既有对齐审计 `docs/doc-implementation-alignment-audit-2026-07-31.md` + `docs/registered-unimplemented-2026-07-30.md`）。
> 结论先行：**实现与文档总体对应度高，绝大多数"已完成"声明属实**；但存在 3 类反向不一致——①权威文档把已做的列为未做；②README 把已修的仍列为阻断；③个别"已落地"模块实为仅定义未接线。

---

## 1. 总览对照表

| 功能域 | PRD/Tech-Spec 声称 | README 声称 | 代码实测 | 判定 |
|---|---|---|---|---|
| P0 全链路（Goal/Work/Run/Outbox/Artifact/Evidence/Audit） | 已完成 | 已运行闭环 | CSV_SUMMARY_BASELINE 通过（文档陈述） | ✅ 一致 |
| GQ-0~GQ-4 生成策略控制流 | 已实现但默认不可启用 | 同 | `generator_factory.py:137`、`generation_strategy_policy.py:28/99`、`generation_strategy_experiment.py:333`、`generation_strategy_promotion.py:45/27`、`generator_metadata.py:67` 均存在且被 `worker/main.py:255`、`execution_orchestrator.py:1436`、`generation_service.py:211` 调用；默认 `config.py:31=artifact-backed`、`:36 canary_gate=False`、`:34 canary_percent=0` | ✅ 一致（"默认禁用"属实） |
| 5 个 API router 挂载 | F-1 已修复 | 已挂载 | `api/main.py:266-294` 含 `human_tasks:290/uploads:291/webhooks:292/reports:293/public_deploy:294` | ✅ 一致 |
| 交付状态机 decide_delivery_verdict | 已接线（CD-1） | 已落地 | `delivery_state.py:55` 被 `execution_orchestrator.py:3795/3817/3824/3832/3865/3881` 真实调用；`GET /v1/app-projects/{id}/delivery-review` handler 在 `app_projects.py:106` | ✅ 主链一致；⚠️ 残留魔法字符串（见 §3-D） |
| 多 Agent 补足 member_contract/TaskFeatures/DispatchDecision/ExecutionPlanItem | 已落地（MA-0~MA-6） | 同 | 四件套均定义并接线：`member_contract.py`→`organization_engine.py:319/339/346/354`；`task_features.py:18`→`organization_engine.py:320`；`models.py:1908 DispatchDecision`→`hive_runtime.py:463`；`models.py:1868 ExecutionPlanItem`→`agent/generator.py:106`→`agent_runner.py:230` | ✅ 一致（默认走单 Agent 冠军路径，符合条件门控） |
| **MAST 失败码体系** | §18.4 作为 MA-0~MA-6 交付物 | 未单列 | `mast_failure.py:16-25` 定义 9 码，但**全库零生产引用**（grep `MAST_` 仅命中 `mast_failure.py` 自身 + 测试） | ❌ **仅定义未用**（见 §3-C） |
| **P2-3 Impact Graph** | PRD §12：**已登记未实现** | 未提 | `impact_graph_service.py:88/140/181/231/258` 已有环检测/级联撤销/批量撤销/指数衰减，落库 `models.py:1707`，`memory_service.py:86/123/149` 接线，且有单测 | ❌ **文档错列未实现**（见 §3-A） |
| **P2-5 AgentEnvelope HMAC** | PRD §12：**已登记未实现** | 未提 | `envelope_v1.py:89 sign / :108 verify`（HMAC-SHA256 + compare_digest + 过期/越权/重放）；`agent_envelope.py:75 verify_hmac`；`agent_mesh.py:289-300` 在 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 配置下强制校验 | ❌ **文档错列未实现**（见 §3-A） |
| **G0 ExternalOperation 闭环** | PRD §12：**已登记未实现** | 未提 | 服务层 `external_operation_service.py:88/157/181/213/252`（operation_key 幂等/dispatch_generation/对账/超时收敛）+ `reconciliation_worker.py:44` 已挂主循环 `worker/main.py:90-95` + 模型 `models.py:1469-1508` + 单测 | ⚠️ **核心闭环已实现**；仅"跨 provider 真实网络 query→resolve 全路径"待合入（见 §3-A/§4） |
| N-3 沙箱 entrypoint 错配 | 审计/§8：**阻断生产** | §8 列为已知阻断 | `sandbox.py:237-245` 已显式传 `--entrypoint sh`；`capabilities/bootstrap/sandbox/Dockerfile:5` ENTRYPOINT=`python /opt/sandbox/main.py`；`apply_host_path_map` sandbox.py:185 fail-closed | ❌ **README 该条已过期**（entrypoint 已修；uid/path 残留见 §3-B） |
| 隐私治理（PRD §7） | 已修复 | 未提 | `privacy_consents`(0038) + API + `classify_and_minimize` + 保留期 Worker（registered-unimplemented 文档确认） | ✅ 一致 |

---

## 2. README 与权威文档自身是否一致？

- README §8「已知阻断」列 **N-3 / N-3c / N-3d / N-3b / N-2**，日期 2026-07-31，与对齐审计 §8 同源。
- 但代码显示 **N-3 的 entrypoint 子项已修复**（`sandbox.py` 已加 `--entrypoint sh`），README 未同步更新 → **README 滞后于代码**。
- **N-3c（uid 65534 vs 65532 写权限）/ N-3d（容器路径当宿主路径）**：代码已加 fail-closed 兜底，但**未强制校验 uid 一致**，残留写权限隐患仍在，属"未完全闭环"，README 描述方向未错，措辞偏绝对。

---

## 3. 重点发现（文档 ≠ 实现的反向案例）

### A. 权威文档把"已做的"列为"未实现" —— 本次最重要发现
`Regent-PRD.md §12`（第 467 行附近）白纸黑字：
> 下列能力**已登记但未实现**（不得宣称验收）：P2-3 Impact Graph、P2-5 AgentEnvelope HMAC、G0 ExternalOperation 完整 EO 闭环。

但：
- `docs/registered-unimplemented-2026-07-30.md`（更细的状态文档，早一天）**已更正**这三项为「已实现 / 已接线 / 半落地」。
- 代码实测三项均有实质实现 + 单测（详见 §1 表格证据）。

**结论**：PRD §12 与 Tech-Spec 的"未实现"登记**已过时**，应以代码与 `registered-unimplemented` 文档为准。这构成"文档 ↔ 实现"的反向失真——不是吹嘘未做，而是**低估了已完成度**，会误导验收口径。

### B. README N-3 entrypoint 已修但未同步
README §8 把 N-3 整体列为阻断。代码 `sandbox.py:237-245` 已显式 `--entrypoint sh`，entrypoint 错配子项已解决。README 应将该子项移除或标注"entrypoint 已修，uid/path 残留待验"。

### C. MAST 失败码：定义完整、零生产接线
Tech-Spec §18.4 要求 MAST_ 命名空间作为多 Agent 补足交付物；§25 将 MA-0~MA-6 标为"已落地"。实测 `mast_failure.py` 定义 9 个码 + 分类器，但除测试外**无任何生产代码引用**（grep `MAST_` 全局仅命中文件自身）。Tech-Spec §18.4 还要求"分类器输出必须带轨迹引用和置信度……进入人工/离线复核"——该接入逻辑**缺失**。属"模块落地但集成未完成"，文档口径偏乐观。

### D. decide_delivery_verdict 残留魔法字符串（违反自身规范）
Tech-Spec §13.8.3 明令："**禁止**仅依赖魔法字符串 `delivery-review-v1 rejected...` 作为唯一契约。" 实测：
- `execution_orchestrator.py:2503` 仍保留 `"delivery-review-v1" in str(exc)` 兜底；
- `delivery_rejection.py:35` 仍生成 legacy 字符串。
类型化 `DeliveryRejection` 已存在，但旧路径未清理干净 → 与规范自相矛盾。

### E. 测试用源码字符串断言（违反 Spec §23:721）
`tests/unit/application/test_delivery_state.py:138`：
```python
assert "decide_delivery_verdict(" in src
```
Spec §23 第 721 行明确"禁止用源码字符串检查、类名存在或伪 Observation 代替行为验证"。此测试正是被禁写法，且对口功能（verdict 接线）已有真实行为可测。属"修复了 bug 但没守住修复"的再现（对齐审计 §8.3 已警告过同类问题）。

---

## 4. 真正"文档有、实现无/未闭环"的清单

这些多已在文档中**自披露**为限制/PENDING，非隐藏缺口，列此汇总：

| 缺口 | 文档自披露位置 | 实测 |
|---|---|---|
| P2-4 最小 Eval Harness | Tech-Spec §25「仍实验骨架」 | 实验骨架，非统计 Gate 就绪 |
| P2-5 自适应拓扑 | PRD §12 / Tech-Spec「ROLLOUT_NOT_ALLOWED」 | 禁止启用，属实 |
| GQ-4 晋级 | Tech-Spec §25「PENDING」 | DecisionRecord 未 ACCEPTED，属实 |
| 完整浏览器 R7 gate | Tech-Spec §25 已知限制 #2 | 无 Playwright 时 dry-run，属实 |
| EO 跨 provider 真实网络 query→resolve 全路径 | Tech-Spec §25 已知限制 #3 | 待合入，属实 |
| SelfImprovementRun 产品门禁 | PRD §9.3 候选 | 落地但 `ROLLOUT_NOT_ALLOWED`，属实 |
| 记忆衰减接入生产检索 | 未单列 | agent 发现 `confidence_decay` 仅被测试调用，未进检索/打分路径（**新发现，文档未记**） |
| MAST 分类器集成 | 见 §3-C | 仅定义未用（**新发现**） |

---

## 5. 行动建议（按优先级）

1. **【高】修订 PRD §12 / Tech-Spec 相关表述**：将 P2-3 Impact Graph、P2-5 AgentEnvelope HMAC 从"已登记未实现"改为"已实现"；G0 ExternalOperation 改为"核心闭环已实现，跨 provider 网络对账待合入"。以 `registered-unimplemented-2026-07-30.md` + 代码为准。
2. **【高】刷新 README §8**：N-3 的 entrypoint 子项标记已修复；保留 N-3c/N-3d 为"uid/path 残留，待生产主机验收"。
3. **【中】清理 D/E**：移除 `execution_orchestrator.py:2503` 魔法字符串兜底、将 `test_delivery_state.py:138` 改为行为断言；二者皆违反自身规范。
4. **【中】MAST 处置二选一**：要么接入分类器生产路径（带轨迹/置信度/人工复核），要么在 Tech-Spec §18.4/§25 明确标注"定义就绪、集成待 P2-4"。
5. **【低】记忆衰减接入**：把 `confidence_decay` 接进检索/打分路径，或记入已知限制。

---

## 6. 核对证据索引（file:line）

- GQ 控制流：`core/src/regent/application/generator_factory.py:137`、`generation_strategy_policy.py:28/99`、`generation_strategy_experiment.py:333`、`generation_strategy_promotion.py:45/27`、`generator_metadata.py:67`、`worker/main.py:255`、`execution_orchestrator.py:1436/1444`、`generation_service.py:211`、`config.py:31/34/36`
- N-3：`infrastructure/sandbox.py:237-245/185/60/155/217`、`capabilities/bootstrap/sandbox/Dockerfile:5`、`core/Dockerfile:13`、`config.py:16`
- Impact Graph：`application/impact_graph_service.py:88/140/181/231/258`、`infrastructure/models.py:1707`、`application/memory_service.py:86/123/149`
- AgentEnvelope HMAC：`application/envelope_v1.py:89/108`、`application/agent_envelope.py:75`、`application/agent_mesh.py:289-300`
- EO：`application/external_operation_service.py:88/157/181/213/252`、`application/reconciliation_worker.py:44`、`worker/main.py:90-95`、`infrastructure/models.py:1469-1508`
- 交付状态机：`application/delivery_state.py:24/55/118`、`execution_orchestrator.py:37/3795/3817/3824/3832/3865/3881/2503`、`application/delivery_rejection.py:35`、`api/app_projects.py:106`、`api/main.py:273`
- 多 Agent：`application/mast_failure.py:16-25/59/63`、`application/member_contract.py`、`application/task_features.py:18`、`infrastructure/models.py:1908/1868`、`application/dispatch_decision.py:127`、`execution/agent/generator.py:106-118`、`execution/agent/agent_runner.py:230-240`、`hive_runtime.py:463-500`
- routers：`api/main.py:266-294`
- 文档：`Regent-PRD.md §12`、`Regent-Technical-Spec.md §9/§13/§18/§21/§25`、`README.md §8`、`docs/doc-implementation-alignment-audit-2026-07-31.md`、`docs/registered-unimplemented-2026-07-30.md`

---

## 7. 修正记录（2026-08-01）

根据上述发现，已对权威文档执行如下修正（纯文档，不含代码改动）：

| 文件 | 修正内容 |
|---|---|
| `Regent-PRD.md §12` | 将原"已登记未实现"三项（P2-3 Impact Graph / P2-5 AgentEnvelope HMAC / G0 ExternalOperation）改为"已实现"，并列出**真正**未实现项（P2-4 Harness 骨架、P2-5 自适应拓扑、GQ-4 PENDING、EO 跨 provider 网络对账、SelfImprovementRun 门禁、MAST 生产接入）；附更正说明。 |
| `README.md §8` | N-3 entrypoint 子项标记为"已修复 2026-08-01（sandbox.py:237-245 已加 `--entrypoint sh`）"；N-3c/N-3d 保留为"残留待生产主机验收"。 |
| `Regent-Technical-Spec.md §25` | 在 MA-0~MA-6 条附更正：MAST 失败码已定义但**未接入生产分类路径**，应视为"定义就绪、集成待 P2-4"；并确认 PRD §12 三项已从"未实现"移除，Spec 不再冲突。 |

**未处理（属代码改动，非文档漏洞，待用户确认是否执行）**：
- `execution_orchestrator.py:2503` 残留魔法字符串 `"delivery-review-v1" in str(exc)`（违反 §13.8.3）；
- `tests/unit/application/test_delivery_state.py:138` 源码字符串断言（违反 §23:721）；
- 记忆 `confidence_decay` 未接入生产检索路径。
