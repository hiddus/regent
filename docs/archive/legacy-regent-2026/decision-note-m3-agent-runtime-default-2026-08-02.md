# DecisionNote: M3 — artifact-backed 降为 scaffold；废止 AB↔agentic 平级 Canary

**日期**：2026-08-02  
**状态**：ACCEPTED  
**相关**：[`decision-note-project-agent-session-2026-08-02.md`](decision-note-project-agent-session-2026-08-02.md)

---

## 决策

1. **产品默认执行路径** = `agentic`（`ProjectAgentSession` + `AgentRunner`）。
2. **`artifact-backed`** 角色 = `SCAFFOLD_OR_KILL_SWITCH_FALLBACK`：
   - 显式 `REGENT_GENERATION_STRATEGY=artifact-backed`（scaffold/bootstrap）；
   - kill switch 回落；
   - **不得**作为产品 champion，也不得与 agentic 做平级 Canary。
3. **资格态**（`agentic_qualification_state`）不再把产品路径降级为一次生成器；仅作运维报告信号。
4. **未来实验轴** = Agent 能力配置（工具 / 记忆 / 模型），不是「有没有 Agent」。
5. 历史模块 `generation_strategy_experiment.py` 保留只读/离线分析，**不得**再驱动默认产品路径翻转。

## 实现锚点

- [`generation_strategy_policy.py`](../core/src/regent/application/generation_strategy_policy.py) `resolve_effective_generation_strategy`
- [`config.py`](../core/src/regent/config.py) 默认 `generation_strategy=agentic`
- `peer_ab_agentic_canary_deprecated()` 合同说明

## 运维注意

生产若 `.env` 仍写 `REGENT_GENERATION_STRATEGY=artifact-backed`，会走显式 scaffold 路径。要启用产品 Agent 路径，设为 `agentic`（或删掉该键以吃代码默认）。Kill switch 仍回落 AB。
