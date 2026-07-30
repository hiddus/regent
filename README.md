# Regent

Regent 是一个可治理、可审计、可恢复的自主产品生成 Core。它从产品目标出发，获取证据、形成候选假设、修订需求、解析能力、生成应用、完成隔离构建与发布，并根据真实观测决定继续、修订或停止。

## 当前状态

- P0 已形成可运行闭环：目标、工作项、执行、审批、证据、观测、恢复与审计。
- P1 已完成至 `0022`：确认后由 Worker 持久化启动、生成、检查和发布 Preview；对话可查询进度、失败可重试，Outbox 不再无限空转。
- 首个验证项目是“AI 业内人员 App”，它只是验证合同，不会作为预置产品功能写入 Core。
- P1 仍保持整体交付，不拆成 P1A/P1B；开发批次只是实施顺序，不改变验收口径。

## 架构边界

1. Core Kernel：状态机、治理、证据、审计、恢复、预算和安全边界。
2. Certified Capability Pool：可声明、可验证、可替换的通用能力。
3. Generated Apps：由 Core 根据目标、证据与约束生成，不由 Core 预置各种业务页面。

## 开发入口

- [产品定义与需求](./Regent-PRD.md)（合并自 Definition-v3 + PRD-v2）
- [技术架构与实施规范](./Regent-Technical-Spec.md)（合并自 TechSpec-v2 + Architecture-v3）
- [测量与决策框架](./Regent-Measurement-Decision-Framework.md)
- [交付计划](./Regent-Plan.md)
- [P1 Core 能力需求](./docs/p1-core-capability-requirements.md)
- [AI 业内人员 App 验证合同](./docs/p1-ai-practitioner-validation-contract.md)
- [文档索引](./docs/README.md)
- [本地开发](./core/README.md)

编码冲突时：产品语义以 PRD 为准，技术实现以 Technical-Spec 为准，阶段顺序以 Plan 为准；任何冲突必须通过 ADR 或 DecisionRecord 解决。

## 仓库结构

| 路径 | 说明 | 详情 |
|---|---|---|
| `core/` | Regent 后端核心（FastAPI + Worker），源码包 `core/src/regent` | [core/README.md](./core/README.md) |
| `capabilities/` | 认证能力池（引导声明 + 解析器/沙箱） | [capabilities/README.md](./capabilities/README.md) |
| `apps/regent-console/` | Web 控制台（React 19 + Vite + TS） | [apps/regent-console/README.md](./apps/regent-console/README.md) |
| `apps/regent-desktop/` | 桌面端封装（Tauri） | [apps/regent-desktop/README.md](./apps/regent-desktop/README.md) |
| `tests/` | 三层测试：architecture / integration / unit | [tests/README.md](./tests/README.md) |
| `fixtures/` | 测试与评测固定数据（eval_task_set_v1.json） | [fixtures/README.md](./fixtures/README.md) |
| `scripts/` | 仓库级辅助脚本（凭据扫描、发布打标） | [scripts/README.md](./scripts/README.md) |
| `ops/` | 运维工具与一次性脚本归档 | [ops/README.md](./ops/README.md) |
| `deploy/` | 部署配置（Squid 出口代理等） | [deploy/README.md](./deploy/README.md) |
| `docs/` | 规范文档、契约、ADR、附录与归档 | [docs/README.md](./docs/README.md) |
| `canvases/` | 可视化画布生成物 | [canvases/README.md](./canvases/README.md) |
| `archive/` | 归档：根目录清理出的临时/实验产物 | — |

> 仓库根只允许白名单内的正式文件；一次性诊断/热修脚本必须放入 `ops/archive/oneoff/`，由 `ops/check_repo_hygiene.py` 在 CI 中强制。详见 [ops/README.md](./ops/README.md)。