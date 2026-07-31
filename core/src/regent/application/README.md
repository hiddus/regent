# core/src/regent/application

应用服务层：目标服务、执行编排（execution_orchestrator）、组织引擎（organization_engine）、能力解析与构建、交付批次与评审、实验平台、调度、许可（permit_service）、策略引擎（policy_engine）、恢复与对账（reconciliation_worker）、Outbox 死信（outbox_dead_letter_service）等。

**本层是状态转换的唯一执行者**：LLM 只能提出结构化 Command，所有状态写入由本层的确定性 Application Service 完成。

## 状态说明（F-8 已闭环）

- `_apply_delivery_verdict` **已完成生产接线**（调用点见 `execution_orchestrator.py:3719/3726/3734`）。Technical-Spec §25 曾将 CD-1 记为「未接线」属文档低报，现已更正（`Regent-Technical-Spec.md:767`）。
- 交付拒绝已类型化为 `delivery_rejection.py:10` 的 `DeliveryRejection(DomainError)`；`:32` 保留 legacy 字符串仅作向后兼容，不再是唯一判据。
- `app_guidance_service.py` 的 `guide()`（:252）已升格为**多步工具循环**（`:278` 受 `_MAX_GUIDANCE_STEPS` 约束），guidance handler 经 `available_tools()`（:229）注册为 `ToolSpec` —— 即 PRD §4.4.2 / CD-4 已落地，不再是单次意图分类器。

## 目录内容

文件：
- `__init__.py`
- `aar1_contract.py`
- `agent_envelope.py`
- `agent_lifecycle_service.py`
- `agent_mesh.py`
- `agent_task_service.py`
- `app_guidance_service.py`
- `app_preview_service.py`
- `app_project_service.py`
- `auto_fix_service.py`
- `baseline_service.py`
- `budget_ledger.py`
- `build_service.py`
- `capability_acquire_service.py`
- `capability_build_service.py`
- `capability_ladder.py`
- `capability_resolution_service.py`
- `compliance_risk_service.py`
- `conversation_service.py`
- `csv_summary.py`
- `delivery_batch_pipeline.py`
- `delivery_batch_service.py`
- `delivery_gap_recovery.py`
- `delivery_review_service.py`
- `discovery_round_service.py`
- `discovery_worker.py`
- `envelope_v1.py`
- `eval_harness_service.py`
- `event_engine.py`
- `evidence_policy.py`
- `evt_gap_service.py`
- `evt_summary.py`
- `execution_events.py`
- `execution_orchestrator.py`
- `execution_service.py`
- `experiment_platform.py`
- `experiment_service.py`
- `external_operation_service.py`
- `feedback_service.py`
- `generation_service.py`
- `goal_anchor_service.py`
- `goal_eligibility_service.py`
- `goal_execution_service.py`
- `goal_interpreter.py`
- `goal_service.py`
- `hive_runtime.py`
- `hive_skill_seed.py`
- `human_task_service.py`
- `iteration_loop_service.py`
- `live_action.py`
- `mcp_governance_service.py`
- `memory_service.py`
- `milestone_service.py`
- `north_star_metrics.py`
- `observation_service.py`
- `organization_engine.py`
- `organization_service.py`
- `outbox_dead_letter_service.py`
- `p1_contracts.py`
- `p1_ports.py`
- `permit_service.py`
- `planning_service.py`
- `policy_engine.py`
- `privacy_service.py`
- `product_discovery_service.py`
- `provider_capability.py`
- `reconciliation_worker.py`
- `release_service.py`
- `requirement_revision_repository.py`
- `research_more_recovery.py`
- `run_advancement.py`
- `runtime_profile_service.py`
- `scheduler_service.py`
- `secret_broker.py`
- `self_improvement_service.py`
- `side_effect_service.py`
- `smoke_test_service.py`
- `tool_governance.py`
- `transition_service.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
