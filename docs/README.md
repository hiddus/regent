# 文档索引

本目录只承载当前开发/部署资料、技术契约、ADR 和历史归档。产品需求、技术规格与执行计划位于仓库根目录。

## 当前权威源

- [Novel Engine PRD](../Novel-Engine-PRD.md)
- [Novel Engine Tech Spec](../Novel-Engine-Tech-Spec.md)
- [Novel Engine Plan](../Novel-Engine-Plan.md)
- [Regent Core 内核职责](../Regent-PRD.md)
- [Regent Core 技术兼容说明](../Regent-Technical-Spec.md)
- [Regent Core 保留与退役清单](../Regent-Plan.md)

## 当前支持文档

- [开发说明](./development.md)
- [部署说明](./deployment.md)
- [迁移策略](./migration-policy.md)
- [ADR](./adr/README.md)
- [状态机与接口契约](./contracts/README.md)
- [架构附录](./appendices/README.md)

## 历史资料

- [旧 Regent 产品路线归档](./archive/legacy-regent-2026/README.md)
- [更早版本归档](./archive/README.md)
- `archive/legacy-regent-2026/graduation-evidence/`：历史验收证据，不代表当前 Novel Engine 已通过验收。
- `archive/legacy-regent-2026/experiments/`：历史实验资料，除非被当前三件套显式引用，否则不构成需求。

## 文档规则

1. 不在 `docs/` 新建第二份产品 PRD、技术总规范或执行计划。
2. 新产品决策直接更新三件套，并在需要时增加 ADR/DecisionRecord。
3. 带日期的审计、探测和一次性执行报告完成后进入归档，不放在活跃文档根目录。
4. 历史文档中的经营产品定位、市场和里程碑均已失效。
