# Multi-Agent 同步评审（2026-07-31）

> 评审对象：Regent-PRD.md §10.1–10.4、Regent-Technical-Spec.md §17.1/§18.1–18.7、Regent-Plan.md §12
> 对照基线：docs/research-multiagent-landscape-2026-07-30.md
> 性质：一致性 / 合理性评审（非执行改动）
>
> **口径更新（2026-08-11，定义 3.0）**：下文成稿时的「强单 Agent 默认 / 多 Agent 须证明净收益 / 自适应受 P2-4 Gate 约束」只约束**晋级生产默认与扩大不可逆现实权限**，不约束沙箱内候选拓扑、角色创造或组织试验。净收益与冻结实验是生产晋级门槛，不是探索前置。

## 一、四项结论合理性

| 结论 | 外部证据支撑 | 判定 |
|---|---|---|
| 强单 Agent 默认，多 Agent 须证明净收益（**当时口径**；现仅约束生产晋级） | Scaling 论文均值 −3.5%、45% 饱和规则、全行业「multi-agent is a tool not a goal」 | ✅ 强支撑（证据仍成立；适用范围已按 3.0 收窄） |
| 不引入 CrewAI / LangGraph 替换 Kernel | CrewAI Token 足迹最重；LangGraph checkpoint 与 Outbox/Lease/Permit 职责重叠但治理更弱 | ✅ 合理 |
| 优先补齐协作评测 / 失败归因 / 模板整体认证 / 长任务耐久 | MAST（41.8% 失败源于系统设计）、TeamTR（整体认证）、Deep Agents harness 四件套 | ✅ 直接对应 |
| 自适应组织继续受 P2-4 统计 Gate 约束（**生产扩权**；沙箱试验开放） | 87% 架构预测器本身依赖冻结基准；论文证明架构选择必须实证 | ✅ 合理（Gate 管现实权限，不管沙箱探索） |

## 二、三文档一致性核对

| 条款 | PRD | Tech-Spec | Plan | 状态 |
|---|---|---|---|---|
| 三冻结指标定义 | §10.1 | §18.3（含分母/缓存单列/缺值 INSUFFICIENT_EVIDENCE） | MA-0/MA-1 | ✅ 对齐 |
| 指标名 | coordination_token_share / error_amplification_factor / dispatch_entropy | 同 | 同 | ✅ 一致 |
| 45% 裁剪规则 | §10.1 规则1 | §18.1 TaskFeatures 提供输入 | MA-4 | ✅ 对齐 |
| MAST 失败码 | §10.2（9 类） | §18.4（9 码 MAST_ 前缀） | WP-FAILURE 九类 | ✅ 一致 |
| 固定模板整体认证 | §10.3 三要素 | §18.5 五类 hash 摘要 | MA-2 | ✅ 对齐 |
| 长任务耐久 | §10.4 可恢复合同 | §18.6 ExecutionPlanItem / 20k 阈值 / Transcript Artifact | MA-3 | ✅ 对齐 |
| A2A 边界 | §12 非目标 | §17.1 状态机映射表 | MA-6 | ✅ 对齐 |
| MCP 工具面边界 | — | §18.7 只做工具发现/调用 | 渐进 | ✅ 合理 |
| 批次依赖拓扑 | — | — | MA-0→MA-1/2/3→MA-4→MA-5→MA-6 | ✅ 拓扑正确 |

## 三、发现问题

### F1（中）：生产 opt-in 与 MA-2 认证尚未完成的张力
PRD §12 称固定模板 `pm-dev-independent-qa-v1` 已通过 `REGENT_AAR1_CERTIFIED_HIVE=true` 在生产启用；但 §10.3 新增强调整体认证（成员契约+五类 hash+整体回归），且 Plan MA-2 仍将其列为待补强项。
→ 当前「CERTIFIED」flag 实际上早于 §10.3 认证合同存在，存在「先宣称认证、后补认证合同」的语义错位。
→ 建议：在 §10.3 或 §12.1 补一句「当前生产 opt-in 为 MA-2 前的预认证状态，须在 MA-2 完成整体认证与回归后重新核验该 flag」，避免把未达新合同要求的模板称为已认证。

**闭合（2026-07-31）**：核实 MA-2 代码与 Plan §12.6 已完成整体认证；文案改为「flag 现受 §10.3/MA-2 合同约束，digests 一致才有效」，而非继续声称预认证待核验。已写入 PRD §10.3/§12、Tech Spec §25、Plan §12.1。

### F2（低）：MA-3 并行机会未充分利用
§12.3 已注明 MA-3 是「并行可靠性线」，仅依赖 MA-0；但用户给的线性顺序将其排在 MA-2 之后。资源允许时可与 MA-1/MA-2 并行以缩短周期。非矛盾，仅提示。

**闭合（2026-07-31）**：Plan §12.3 补半句「可与 MA-1/MA-2 并行；与线性编号不矛盾」。不改实现顺序。

### F3（低/观察）：OTel GenAI 语义约定未显式入验收
调研报告 §6.2 #9 建议对齐 OTel GenAI span，计划里仅在 MA-1「过程 span」隐式涵盖。若未来要接供应商中立评测层，建议在 MA-1 验收（WP-METRICS/可观测）显式要求符合 OTel GenAI semantic conventions，而非仅内部 span。

**闭合（2026-07-31）**：Plan MA-1 门禁与 `WP-METRICS` 显式写入 OTel GenAI conventions 验收要求，并标注为后续对齐（本轮不接完整 OpenTelemetry 供应商栈）。

## 四、总体判定
四项结论合理、可追溯、与外部权威研究一致；三文档在指标定义、失败码、认证、长任务、协议映射、批次依赖上完全自洽。F1/F2/F3 已按最小文档补丁闭合。
