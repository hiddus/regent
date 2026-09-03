# Regent Core 技术兼容说明

> 状态：ACTIVE INTERNAL KERNEL
>
> 产品技术权威源：[Novel-Engine-Tech-Spec.md](./Novel-Engine-Tech-Spec.md)

本文件说明现有 Regent 代码如何作为 Novel Engine 的基础设施继续存在。旧版完整技术规格已归入 `docs/archive/legacy-regent-2026/`。

## 当前实现

- Python 3.12+、FastAPI、Worker 与持久化队列。
- PostgreSQL、Outbox、不可变 Artifact 和执行恢复机制。
- 模型调用、成本记录、沙箱、权限和审计基础设施。
- React/Vite 控制台作为现有管理界面。

## Novel Engine 接入边界

Novel Engine 应通过稳定端口使用内核：

- `TaskRuntime`：启动、暂停、恢复、取消和进度事件。
- `ModelGateway`：模型路由、配额、成本和重试。
- `ArtifactStore`：章节、快照、导出物和证据对象。
- `PermitService`：重大剧情节点或高影响操作的等待与裁决。
- `ObservationService`：质量、越界、成本和漏斗事件。

领域层拥有自己的仓储与模型，不直接依赖 Regent 的旧经营目标、Generated App、增长实验或交付评审对象。

## 数据要求

- 内部原型可使用文件型参考实现。
- 任何外部用户测试开始前，账户、作品、权限、任务状态和账本必须落在支持事务与租户隔离的持久化存储中。
- 正文与导出物可进入对象存储；元数据、权限、状态、账本和创建留痕进入数据库。
- 用户删除、法定保留、备份恢复和 append-only 留痕之间的冲突，以 Novel Engine PRD 的数据政策和后续法务确认结果为准。

## 禁止的复用方式

- 不把 hub-and-spoke 直接等同于角色信息集裁剪。
- 不把通用 `GoalSpec` 直接暴露为 C 端 `StoryGoal`。
- 不让旧控制台字段决定 C 端交互。
- 不以类名或模块存在证明 Novel Engine 功能已经完成。

## 验收

内核复用必须由 Novel Engine 的端到端场景验收：用户目标进入、关键路径确认、章节生成、暂停恢复、成本阻断和导出。旧 Regent 的经营闭环测试只能作为基础设施回归，不能替代产品验收。
