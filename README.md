# Regent 短剧剧本引擎

> 面向短剧创作者，以全剧规划、分集交付、人工改稿和制作约束为核心的剧本创作工具。

当前目标是从构思持续产出有完整结局、可修改、可供制作讨论的短剧剧本。首版默认验证低成本真人原创短剧；产品假设与支持范围需经客户和真实项目验证。Regent Core 继续提供预算、权限、恢复和持久化基础设施。

本次为产品转向与规范更新，尚未实现或认证短剧交付链路。现有 novel 包、novel-web 及内部 script 协议是待适配基础，不等于完整短剧能力。目录和主文档保留原文件名以兼容既有链接。

## 权威文档

| 文档 | 权威范围 |
|---|---|
| [短剧产品需求](./Novel-Engine-PRD.md) | 客户、完整剧本、编辑/锁定、交付与质量验收 |
| [短剧技术说明书](./Novel-Engine-Tech-Spec.md) | 结构化剧本、版本、依赖修订、运行与迁移 |
| [修改开发计划](./Novel-Engine-Plan.md) | SD-M0～SD-M5、首批任务与阶段出口 |
| [Regent Core 职责](./Regent-PRD.md) | 内核职责与产品边界 |
| [Core 技术兼容说明](./Regent-Technical-Spec.md) | 内核复用端口 |
| [Core 保留清单](./Regent-Plan.md) | 共享底座保留与退役 |

产品语义以 PRD 为准，技术设计以技术说明书为准，实施顺序以开发计划为准。旧小说方向的三动作禁改稿、公共阅读、广告分成和长篇认证路线已被替代。[转向前快照](./docs/archive/novel-before-short-drama-2026-09-22/README.md)仅用于历史追溯。

## 当前产品契约

1. 确认制作规格、完整结局与分集大纲，按集或按批持续交付正文。
2. 创作者可直接编辑、局部改写、比较版本；修改影响确认后在修订分支处理。
3. 定稿显式解锁，已拍摄版本不可覆盖；历史事实、人工稿与费用可追溯。
4. 完整交付包含全剧设计、全部分集正文、人物/场景/道具表及 TXT/DOCX 导出。
5. 首先验证单场、单集、连续三集和完整 12 集样本，再进行客户封测。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `core/` | Regent Core 后端内核（FastAPI、Worker、状态机与基础设施） |
| `apps/novel-web/` | 现有用户端；按短剧开发计划适配 |
| `apps/regent-console/` | 内部运维控制台 |
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

部署与环境说明见 [docs/deployment.md](./docs/deployment.md)，开发说明见 [docs/development.md](./docs/development.md)。不得将旧控制台或小说链路可运行等同于短剧产品验收通过。

## 文档状态

旧方向文档已经归档。归档内容可以用于理解历史实现和决策来源，但其中的产品定位、市场、里程碑和验收口径均已失效。

## License

见 [LICENSE](./LICENSE)。
