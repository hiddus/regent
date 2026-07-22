# Regent P1 Core 编码启动审查

> 状态：已被技术架构审查废止。最终编码基线为 p1-core-final-technical-spec.md。


> 日期：2026-07-18  
> 结论：Core T1、T2、T3、T6 可以立即编码；生产 Preview 需先完成 S0。

## 产品审查

GO。正确的 P1 产品是 Core 能力本身。AI 从业者 Goal 不预设 App 形态。Product Discovery、候选比较和 AppRequirement 生成是必经步骤。

验收工程只冻结 Goal、约束、资源和评价口径，不冻结用户细分、功能、页面和技术栈。

## 架构审查

GO，约束如下：

- ProductHypothesis 和 AppRequirement 使用通用 schema；
- Challenge 业务字段只在 JSON Artifact 和生成 App 内；
- Core 不引用 apps；
- App 不引用 regent；
- Workspace、Build、Release、Deployment 是通用交付资源；
- Build 和 Deployment 只由 Worker 驱动；
- 外部副作用必须 Permit；
- 动态组织不是默认路径。

需要新增静态架构测试，扫描 Core 是否出现首个 Challenge 的业务实体或硬编码分支。

## 技术审查

本地编码 GO：0011 数据模型、通用 schema、候选差异检查器和 Workspace 安全基元。

生产发布 NO-GO：

- Docker 构建存储损坏；
- 当前源码挂载不是不可变发布；
- Core 管理 API 尚无正式鉴权；
- 8000 端口直接暴露；
- 尚未完成 HTTPS、限流、备份和回滚演练。

这些不阻塞本地 T1、T2、T3、T6，但阻塞 Preview 上线。

## 首批 Definition of Done

- Ruff、Mypy strict、Pytest 通过；
- 迁移 upgrade、downgrade 和从 0010 升级通过；
- 状态转换写 Audit 和 Outbox；
- 写接口幂等并检查 expected_version；
- Artifact 使用 canonical JSON 和 SHA-256；
- 模型输出经过结构校验和纠正重试；
- 没有 AI 行业 App 方案硬编码；
- 不改变 P0 实验和 DecisionRecord；
- 文档和实现一致。

## 启动顺序

第一并行批：

1. T1：0011 ProductHypothesis/Decision 模型；
2. T2：通用 ProductHypothesisProposal/HypothesisSelection；
3. T3：候选差异检查器；
4. T6：Workspace 安全基元。

第二批：

5. T4：ProductDiscoveryService；
6. T5：Discovery API。

结论：需求决策、领域边界、首批接口、迁移顺序、测试门禁和依赖都已明确，可直接启动编码。
