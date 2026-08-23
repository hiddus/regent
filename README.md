# Regent

> **围绕经营目标持续工作的智能体团队。**

Regent 是一支能够自由探索、持续实践、自我组织并从真实经营结果中进化的智能体经营团队。它连接业务数据，主动发现增长机会、执行获授权的行动，并根据真实结果持续调整——而不是一个聊天助手、一次性工作流或多智能体开发框架。

当前商业切入点是**互联网产品增长经营**：围绕一个明确增长指标，在付费试点内完成经营体检、机会发现、低风险实验、结果验证与周期复盘。应用生成是团队可调用的能力，而非 Regent 本身。

- 永久定义（唯一规范源）：[`docs/definitions/REGENT-DEFINITION-3.0.txt`](./docs/definitions/REGENT-DEFINITION-3.0.txt)
- 产品定义：[`Regent-PRD.md`](./Regent-PRD.md) · 技术架构：[`Regent-Technical-Spec.md`](./Regent-Technical-Spec.md) · 编码执行清单：[`Regent-Plan.md`](./Regent-Plan.md)

---

## 核心能力

- **目标治理闭环**：目标发布 → 边界确认 → 可行性分析 → 目标锁定 → 正式执行，每阶段可审计、可恢复。
- **受治理的执行**：LLM 只提出结构化 Command，状态转换由确定性应用服务执行；不可逆/外部效应须前置授权（ExecutionPermit）。
- **证据与观测**：执行过程产生 Evidence / Observation，用于接触现实、比较路径、更新认知，并支撑周期复盘。
- **应用生成与发布**：Core 依据目标、证据与约束生成应用，支持预览发布与可审计的交付评审。
- **预算与护栏**：每个目标设置正数预算上限；成本、权限、数据、不可逆影响均在护栏内运行。

## 架构边界

| 层 | 职责 |
|---|---|
| **Core Kernel** | 状态机、治理、证据、审计、恢复、预算与安全边界。 |
| **Certified Capability Pool** | 可声明、可验证、可替换的通用能力。 |
| **Generated Apps** | 由 Core 根据目标、证据与约束生成，不由 Core 预置业务页面。 |

核心不变式：**LLM 只能提出结构化 Command，状态转换由确定性 Application Service 执行。** 详见 [`core/README.md`](./core/README.md)。

> 近期代码演进（2026-08-13 之后）：组织修复脚手架已落地并接入执行主链——hub-and-spoke 执行纪律（`config.max_subagent_depth=1`）、规则式目标分类、组织模式选择、运行时行为监测（含 SPA JS 深度分析）、行为修复环自动再调度与 worker 周期监测 tick（护栏：ACTIVE / 无存活 run / `max_iterations` 上限 / 预算）。详见 [`STATUS.md`](./STATUS.md) 与 [`Regent-Technical-Spec.md`](./Regent-Technical-Spec.md) §25。

---

## 快速开始

### 方式 A：本地 Compose（推荐，一键起全套）

需要 Docker 与 Docker Compose。该方式启动 `postgres` + `api` + `worker` 三个服务（开发便利端口，**非生产安全基线**）。

```bash
# 1. 配置环境变量（模型 API Key 等必填项；端口、沙箱模式可选）
#    具体变量与取值见 docs/deployment.md
cp .env.example .env        # 若仓库未提供示例，请按 deployment.md 手动创建

# 2. 构建并启动
docker compose up --build

# 3. 健康检查
curl http://localhost:8000/health/ready
```

API 默认监听 `:8000`，PostgreSQL 默认发布到宿主 `:5432`（仅开发用）。

### 方式 B：本地开发（源码安装）

```bash
# 仓库根（pyproject.toml 在仓库根，包 regent-core 构建自 core/src/regent）
pip install -e ".[dev]"     # 安装运行 + 开发依赖（要求 Python >=3.12,<3.14）

regent-api                  # 启动 FastAPI 服务（REST API + 静态资源）
regent-worker               # 启动 Worker（消费执行队列并持久化）

pytest                      # 运行测试
ruff check core/src         # 代码风格 / 静态检查
mypy                        # 严格类型检查
```

> 更完整的运行入口、子包结构、已关闭偏差与本地开发细节见 [`core/README.md`](./core/README.md)。
> 生产 / 服务器手工编排（Path A）与上述 Compose（Path B）服务名、网络、端口策略**不相同**，请勿混读——见 [`docs/deployment.md`](./docs/deployment.md)。

---

## 仓库结构

| 路径 | 说明 |
|---|---|
| `core/` | Regent 后端核心（FastAPI + Worker，Python） |
| `capabilities/` | 认证能力池 |
| `apps/regent-console/` | Web 控制台 |
| `apps/regent-desktop/` | 桌面端（探索性，非目标） |
| `tests/` | architecture / integration / unit 测试 |
| `fixtures/` | 评测固定数据 |
| `scripts/` | 仓库级辅助脚本 |
| `ops/` | 运维脚本与门禁 |
| `deploy/` | 部署相关 |
| `docs/` | 规范、计划、审计、决策记录 |
| `compose.yaml` | 本地 Compose（与服务器 Path A 手工编排不同） |
| `capabilities/` | 认证能力池（声明 + resolver/sandbox 运行器） |
| `archive/` | 历史产物 / 报告归档（非核心） |
| `canvases/` | 设计画布产物（`.canvas.tsx`，非核心） |
| `deliverables/` | 生成的对外交付物（非核心） |
| `regent-pptx/` | PPT 构建工程（非核心） |

各子目录均有自己的 `README.md`，可逐级深入。

## 文档索引

| 文档 | 用途 |
|---|---|
| [`Regent-PRD.md`](./Regent-PRD.md) | 产品定义与需求（权威执行基线） |
| [`Regent-Technical-Spec.md`](./Regent-Technical-Spec.md) | 技术架构与实施规范（含 §21 双列 API 对照） |
| [`Regent-Measurement-Decision-Framework.md`](./Regent-Measurement-Decision-Framework.md) | 测量与决策框架 |
| [`Regent-Plan.md`](./Regent-Plan.md) | 唯一编码执行清单与开发切片 |
| [`docs/README.md`](./docs/README.md) | 文档总索引（含决策记录、状态机、附录） |
| [`docs/deployment.md`](./docs/deployment.md) | 两套部署路径与沙箱支持矩阵 |
| [`docs/definitions/REGENT-DEFINITION-3.0.txt`](./docs/definitions/REGENT-DEFINITION-3.0.txt) | 唯一规范定义源（冻结，不可修改） |

编码冲突时：产品语义以 PRD 为准，技术实现以 Technical-Spec 为准，阶段顺序以 Plan 为准；任何冲突必须通过 ADR 或 DecisionRecord 解决。

## 状态与变更

内部开发状态、已知阻断与决策记录快照统一维护在 **[`STATUS.md`](./STATUS.md)**（按日期更新），不在本 README 展开，以保持项目门面整洁。

## License

见仓库根目录 [`LICENSE`](./LICENSE)。
