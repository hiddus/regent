# 对话式完整交付 — 统一开发计划（2026-07-31）



> 状态：ACTIVE（编码执行清单；与 `Regent-Plan.md` §14 互指）  

> 吸收来源：

> 1. [`conversational-delivery-architecture-review-2026-07-31.md`](./conversational-delivery-architecture-review-2026-07-31.md)（同事双专家评审 + §9 基线对照修正）

> 2. 本侧产品/技术差距审查（交付状态机接线、失败契约、可审阅交付物）

> 配套：[`decision-note-auto-start-journey-2026-07-31.md`](./decision-note-auto-start-journey-2026-07-31.md) · [`decision-note-gq4-pending-2026-07-31.md`](./decision-note-gq4-pending-2026-07-31.md)



## 0. 统一结论



**方向**：缺的不是「有没有 agent」，而是「合规接通 + 对话层产品化 + 交付可靠性接线」。



**归因校正**（以同事 §9 为准）：



| 误判 / 过时说法 | 校正后 |

|---|---|

| 「agentic 默认关闭是暗启动缺陷」 | ❌ 撤销。是 PRD/Tech-Spec 规定的 GQ-2→GQ-3→GQ-4 门禁；缺的是合规实验前置，不是偷偷打开开关 |

| 「`status.agents` 后端未填充」 | ❌ 证伪。`app_guidance_service` 已填；前端回退仅在空列表时 |

| 「创建即 auto-start = 伪造人类确认」 | 🔽 降级。审计诚实；缺的是 CURRENT 文档 DecisionRecord（已补） |

| 「delivery_state 已解决交付质量」 | ❌ 否。预算缩放已活；`decide_delivery_verdict` 已接线（CD-1.2 ✅） |



**真正卡住的两件事（同事）+ 第三条（本侧）**：



1. **C2**：agent 工具在 worker 宿主执行，违反 Tech-Spec 沙箱规范 → **阻塞 GQ-3 合规开跑**。

2. **对话层从未被规划为 agent loop** → **新增 PRD 条目后才能编码**（不是 bugfix）。

3. **交付可靠性装饰层**：verdict 已接线（CD-1.2）；失败契约字符串化、门禁盲区、默认路径失败丢产物仍部分待 CD-1.1/1.4。



**护城河（明确不做）**：不删 Permit / Outbox / Evidence / Audit / Reconciler；不让审批类 HumanTask 超时自动放行；agent 只提 Command，状态转换仍由 Application Service 执行。



---



## 1. 吸收矩阵



| 来源项 | 吸收？ | 落入阶段 | 说明 |

|---|---|---|---|

| R0-1 / C2 沙箱隔离 | ✅ 必吸 | CD-0 | 规范违反；GQ-3 前置 |

| R0-2 transcript 不可静默丢 | ✅ 必吸 | CD-0 | 可观测性红线 |

| R0-3 / C1 确认语义 | ✅ 已吸 | 文档 | DecisionNote ACCEPTED + PRD/Tech 同步 |

| R1-1 打开 agentic | ✅ 改吸 | CD-2 | **仅在 CD-0 完成后**走既定 GQ-3 窗，禁止跳过 canary |

| R1-2 过程可见 | ✅ | CD-1 / CD-3 | 先扩展 `on_event`；SSE 推流可后置 |

| R1-3 自适应修复预算 | ✅ | CD-1 | 与 delivery 画像预算耦合 |

| R2 对话即 agent loop | ✅ | CD-4 | **新需求**；先 PRD（已写 §4.4）再编码 |

| R2-2 入口合一 | ✅ | CD-4 | `/conversations` 接 guidance 或明确废弃 |

| R2-3 capabilities→ToolSpec | ✅ | CD-4+ | 依赖 capability.json parameters Schema |

| R2-4 Evidence/对话入上下文 | ✅ | CD-4 | 检索而非全量 |

| §5 两级 Effect 模型 | ✅ | Tech-Spec | 沙箱内事后审计 / 外部前置 Permit |

| 本侧 verdict 接线 | ✅ | CD-1 | `decide_delivery_verdict` 进 orchestrator |

| 本侧 AC1 门禁盲区 | ✅ | CD-0 | AST 切分 + TARGETS + CI |

| 本侧 goal_intent 早交人 | ✅ | CD-1 | 文档曾误称已存在 |

| 本侧 DeliveryRejection 类型化 | ✅ | CD-1 | 消掉字符串魔法 |

| 本侧可审阅工程包 | ✅ | CD-3 | plan/diff/验证/README+tests |

| 本侧交人带选项+代价 | ✅ | CD-3 | WorkBuddy「带答案回来」 |

| 统一 Verification 闸门 | ✅ | CD-2 | 与生成器解耦；O1 升级为门禁候选 |



---



## 2. 阶段计划（依赖序）



### CD-0 · 止血与可信绿灯（约 2–3 天）— 不做不能开 GQ-3



| ID | 项 | 类型 | 验收 | 状态 |

|---|---|---|---|---|

| CD-0.1 | agent 工具改走 `DockerSandboxDriver`；生产禁止 `sandbox_mode=local` | 修 bug（规范违反） | 逃逸/宿主 RCE 用例失败即门禁红；Tech-Spec §13.7 影子前置满足 | ✅ 已完成 |

| CD-0.2 | 删除 `agent/generator.py` transcript `except: pass`；失败可观测（阻断或 DLQ，二选一并写进 Tech-Spec） | 修 bug | 注入持久化故障时有 Evidence/告警，不静默继续 | ✅ 已完成 |

| CD-0.3 | `ops/delivery_dead_end_gate.py` 改 AST 切分；TARGETS 扩到 application 相关；进 CI；补「故意死端」元测试 | 基建 | 盲区关闭；CON-5 + AC1 独立 step 绿 | ✅ 已完成 |

| CD-0.4 | 架构测试：`decide_delivery_verdict` 必须有非测试生产调用者（先红，驱动 CD-1） | 基建 | 红→绿绑定接线 | ✅ 已完成 |



### CD-1 · 交付状态机成真（约 3–5 天）



| ID | 项 | 验收 | 状态 |

|---|---|---|---|

| CD-1.1 | 定义 `DeliveryRejection`（gap_kind / reasons / draft_uri）；6 处生产者改造；orchestrator 去 `str.split` | 措辞漂移不再静默误分类 | ✅ 已完成 |

| CD-1.2 | `_apply_delivery_verdict` 消费 `decide_delivery_verdict`；`delivery_state` 写入 goal metadata | AUTO_RECOVERING / DELIVERED_FOR_REVIEW 真实出现 | ✅ 已完成 |

| CD-1.3 | `goal_intent`（及等价 needs_human）在阶梯耗尽**前**短路交评 | 不再白烧 10–15 轮 | ✅ 已完成 |

| CD-1.4 | artifact-backed 失败保留草稿 URI（AC4 收口） | 默认路径人接手不空手 | ✅ 已完成 |

| CD-1.5 | 修复预算与 `recovery_budget_multiplier` 对齐；嵌套修复次数可配置 | 可修复缺陷在预算内自愈 | ✅ 已完成 |



### CD-2 · 合规点亮 agentic（约 1 实验窗 + 1 迭代）



| ID | 项 | 验收 | 状态 |

|---|---|---|---|

| CD-2.1 | CD-0.1 完成后开 GQ-3 真实流量窗（`canary_gate` + percent） | 报告含 95% CI；影子在独立 sandbox | 🟡 门禁就绪，实验窗待运维开启 |

| CD-2.2 | Verification 提升为**交付统一闸门**（至少 compileall + 起服务 + 路由；pytest 按合同） | 非仅 agentic 私有能力 | ✅ 已完成 |

| CD-2.3 | 达标后 GQ-4 DecisionRecord + 翻转默认；未达标保持 artifact-backed | 禁止仅靠 `.env` 宣称晋级 | 🟡 门禁就绪，实验窗待运维开启（见 [`decision-note-gq4-pending-2026-07-31.md`](./decision-note-gq4-pending-2026-07-31.md)） |



### CD-3 · WorkBuddy 级交付体验（约 5–8 天）



| ID | 项 | 验收 | 状态 |

|---|---|---|---|

| CD-3.1 | 只读 API：execution plan / transcript 摘要 / 验证结论；Console 审阅面 | 用户能区分海报与产品 | ✅ 已完成 — `GET /v1/app-projects/{id}/delivery-review` + `DeliveryReviewQueryService` |

| CD-3.2 | 交人卡附 2–3 可执行选项 + 代价/轮次；成本与剩余预算可见 | 「带答案回来」 | ✅ 已完成 |

| CD-3.3 | `on_turn` → `on_event(tool, args, result)`；节点卡展示最近工具轨迹 | 长任务不再像卡住 | ✅ 已完成（最小版）— `generator._on_event` → `on_progress` 前缀解析 + `metadata.tool_events` |

| CD-3.4 | README + 最小 tests 进 acceptance_contract（按 goal_scale 可放宽） | zip 可接手 | ✅ 已完成 — `require-readme`/`require-tests` 审查 + planned_paths 强制 README/tests（非 SMALL） |

| CD-3.5 | 「总是允许」真实持久化或先隐藏假按钮 | 不伤信任 | ✅ 已完成 |



### CD-4 · 对话即 Agent（新需求，约 1–2 迭代）— **PRD §4.4 批准后方可编码**



> ⚠️ CD-4 编码已获 DecisionNote [`decision-note-prd-44-conversational-delivery-2026-07-31.md`](./decision-note-prd-44-conversational-delivery-2026-07-31.md)（ACCEPTED）批准。



| ID | 项 | 验收 | 状态 |

|---|---|---|---|

| CD-4.1 | `AppGuidanceService.guide` 升级为工具循环；现有 `_handle_*` 注册为 ToolSpec | 一句话可多步澄清+执行 | ✅ 已完成 |

| CD-4.2 | `/v1/conversations/{id}/messages` 接入同一 loop，或文档标明废弃死路 | 单一 NL 入口 | ✅ 已完成 |

| CD-4.3 | Evidence / 对话检索段进入 `context_assembler` | agent 决策可引用历史 | ✅ 已完成 |

| CD-4.4 | capabilities → ToolSpec；须先补 parameters Schema | 认证能力可被发现调用 | ✅ 已完成 — `load_capability_tool_specs` + `product-surface-v1.parameters`；执行适配器仍后续 |



### CD-5 · 闭环度量与结构（持续）



| 项 | 状态 |

|---|---|

| `DELIVERY_STATE_CHANGED` 事件 + handoff_rate / 收敛轮次进 north-star guardrail | ✅ 已完成 |

| SSE：自适应轮询（0.25s→1.0s 退避）；LISTEN/NOTIFY 仍为后续增强 | ✅ 已完成（最小版） |

| 抽 `DeliveryRecoveryCoordinator`，降低 orchestrator 体量 | ⚪ 持续 |

| token 流式输出（体验增强） | ⚪ 持续 |



---



## 3. 与既有计划的关系



| 既有 | 关系 |

|---|---|

| `Regent-Plan.md` §13 GQ-* | CD-0 是 GQ-3 **合规前置**；CD-2 执行既定 GQ-3/GQ-4，不另起晋级规则 |

| `docs/delivery-state-machine-2026-07-31.md` | CD-1 完成其「接线」欠账；纠正文档中「AC3 已存在」的错误断言 |

| `docs/gq34-promotion-control-flow-2026-07-31.md` | 仍有效；补充「沙箱未达标前禁止开 canary」 |

| `docs/console-dialog-*` | CD-3.2/3.5 与确认卡合同衔接，不重复造 DecisionPreference |



---



## 4. 编码门禁



1. **CD-6 未全绿之前**（N-3 / N-3c / N-3d / N-3b / N-2 + T1–T6；见执行级计划）：禁止提高 `canary_percent` / 打开 `canary_gate` 于生产。禁止将「运维 `.env=agentic`」表述为 GQ-4 完成。禁止仅用 `echo ok` 宣称沙箱闭环。

2. **CD-7（技 P1-1…4 + N-4/N-6）未绿之前**：禁止开 GQ-3 真实流量窗。

3. **CD-4 未走 PRD 修订批准前**：禁止把 guidance 改成 agent loop 并宣称验收（§4.4 DecisionNote 已 ACCEPTED；本条保留为历史门禁说明。）

4. 任一阶段完成须有可复算测试或实验报告；文档、代码、迁移、部署一致。

---

## 5. 下一步（重订）

CD-0…CD-5 代码侧已完成。后续以 **ACTIVE 重订** 为准：

| 文档 | 用途 |
|---|---|
| [`conversational-delivery-next-plan-2026-07-31.md`](./conversational-delivery-next-plan-2026-07-31.md) | 批次级 CD-6…CD-12 |
| [`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md) | CD-6 工作包展开 |
