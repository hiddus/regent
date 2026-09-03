# Regent Novel Engine

> 面向中国大陆非头部兼职网文作者的导演式长篇小说创作系统；经验证后扩展为创作与阅读双环。

用户说出故事目标、调整关键路径，并在少数重大剧情节点作出选择；持续 Agent loop 负责规划、角色表演、成文、检查、局部修复、断点恢复和续写。

本仓库由两部分组成：

- **Novel Engine 产品层**：当前唯一对外产品方向；目标能力包括 C 端 Web/PWA、小说领域模型、剧本展开、角色演绎、阅读与后续内容分发，尚不表示这些能力已经实现。
- **Regent Core 内核层**：内部基础设施，提供多 Agent 编排、持久化任务、预算、权限、审计、恢复和运行时监测；不再作为独立经营产品对外定义。

私有项目 [`hiddus/novelMaker`](https://github.com/hiddus/novelMaker) 是小说推演链路的正式迁移基线：保留其持续 Agent loop、人机互动修正和状态推进能力，再逐项接入 Regent Core 与 Novel Engine 领域模型；它不构成第三套产品定义。

## 权威文档

| 文档 | 权威范围 |
|---|---|
| [Novel-Engine-PRD.md](./Novel-Engine-PRD.md) | 唯一产品需求源、用户、商业模式与验收标准 |
| [Novel-Engine-Tech-Spec.md](./Novel-Engine-Tech-Spec.md) | 产品技术架构、领域模型与内核复用边界 |
| [Novel-Engine-Plan.md](./Novel-Engine-Plan.md) | 唯一开发顺序、里程碑和出口检查 |
| [Novel-Engine-Implementation-Requirements-v3.11.md](./Novel-Engine-Implementation-Requirements-v3.11.md) | v3.12 落地审查历史快照；已由 v4.0/v5.0 主文档吸收，不再作为独立权威源 |
| [Regent-PRD.md](./Regent-PRD.md) | Regent Core 内核职责与产品边界兼容入口 |
| [Regent-Technical-Spec.md](./Regent-Technical-Spec.md) | 现有内核实现说明与迁移约束 |
| [Regent-Plan.md](./Regent-Plan.md) | 内核保留、复用和退役清单 |

冲突处理顺序：产品语义以 Novel Engine PRD 为准，技术实现以 Novel Engine Tech Spec 为准，执行顺序以 Novel Engine Plan 为准。旧 Regent 产品材料仅供历史追溯，不得作为当前需求依据。

## 当前产品契约

用户主路径只有三类动作：

1. 用自然语言设定故事目标。
2. 调整 10–20 个主线关键节点。
3. 在死亡、背叛、揭露、开战等重大节点拍板。

要求用户维护结构化设定、逐章审核剧本、维护关系图或逐章改文，均视为产品退化。

当前产品阶段：

- 阶段一服务中国大陆非头部兼职网文作者，以 Web/PWA 验证原创导演式创作。
- MVP 只做私有作品与定向试读；锚点仿写、公共池、广告分成和原生 App 后移。
- 终局仍是原创创作与阅读双环，但必须先通过导演式交互、带 AI 标识持续阅读和单位经济三道闸门。
- 当前代码是可复用的 Regent Core 与内部控制台；小说领域 API 和 C 端页面尚未实现。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `core/` | Regent Core 后端内核（FastAPI、Worker、状态机与基础设施） |
| `apps/regent-console/` | 现有控制台；后续按 Novel Engine Plan 演进为 C 端产品界面 |
| `apps/regent-desktop/` | 历史探索性桌面壳，不是阶段一验收入口 |
| `capabilities/` | 可复用能力声明与运行器 |
| `tests/` | 架构、集成与单元测试 |
| `docs/` | 当前开发/部署说明、契约、ADR 与历史归档索引 |
| `docs/archive/legacy-regent-2026/` | 旧经营产品路线的计划、审计、实验与运行快照 |

## 本地开发

当前代码仍沿用 Regent Core 的启动方式：

```bash
pip install -e ".[dev]"
regent-api
regent-worker
pytest
```

部署与环境说明见 [docs/deployment.md](./docs/deployment.md)，开发说明见 [docs/development.md](./docs/development.md)。小说产品包尚未完成迁入时，不得将旧控制台的可运行性等同于 Novel Engine 产品验收通过。

## 文档状态

旧方向文档已经归档。归档内容可以用于理解历史实现和决策来源，但其中的产品定位、市场、里程碑和验收口径均已失效。

## License

见 [LICENSE](./LICENSE)。
