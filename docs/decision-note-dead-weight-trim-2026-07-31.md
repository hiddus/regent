# DecisionNote: 已剪生成路径两处死重

**日期**：2026-07-31  
**状态**：ACCEPTED  
**依据**：框架臃肿/卡点核证（代码事实）

## 决策

1. **去掉默认路径无效 LLM 语义验证**：`ArtifactBackedCodeGenerator` 写文件后默认不再调用 `validate_goal_alignment_semantic`。该调用**不是**质量验证 / **非** fail-closed 真实验证。能力保留为显式开关 `REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED`（默认 `False`）。真实验证仍为 build / deploy / smoke / pytest。
2. **`GeneratorSelector` 懒构造 agentic**：启动期只构造轻量 artifact-backed；`AgenticCodeGenerator` 在 `select()` 首次命中 agentic 时再构造。保留 `assert_generator_consistency` 等 fail-closed 检查。

## 不做

不砍真实 build/deploy/smoke；不启用 canary / 扩容 Hive / 引入 LangGraph；不擅自改写生产 `REGENT_GENERATION_STRATEGY`。
