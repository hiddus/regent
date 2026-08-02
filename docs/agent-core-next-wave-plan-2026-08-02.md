# Agent 内核下一波计划（W4 · 2026-08-02）

> 状态：**ACTIVE → 实现完成（待部署验证）**  
> 输入：审计可用部分 · R0–R3 已落地 · qualification ladder §4  
> 默认 **D1=A**（冻结 §4 产品面；Web 工具非本波 P0）  
> P2（流式/MCP/只读并发）**明确后置，本波不做**

---

## 实现账本

| ID | 动作 | 状态 |
|---|---|---|
| P0-1 | CJK token 计权 + `prompt_tokens` EMA 校正 | **DONE** `compact.py` |
| P0-2 | `ops/probe_cache_hit.py` 可观测出口 | **DONE** |
| P0-3 | 计数断言集合化 + subagent submit 契约 | **DONE** |
| P0-4 | 审计勘误脚注 | **DONE** |
| P1-1 | `ops/run_agentic_live_golden.py` + `--live-golden` | **DONE** |
| P1-2 | Skills 中文别名 + CJK 默认注入 | **DONE** |
| P1-3 | `ops/run_skill_ablation.py` | **DONE** |
| P1-4 | `agent_context_window_tokens` → AgentRunner | **DONE** |
| Q3 | CANARY_* `sample_gates` 钩子 | **DONE** |
| P2 | 流式 / MCP / gather | **DEFERRED** |

---

## 出口命令

```text
python -B ops/run_agentic_offline_qualification.py --full-golden
python -B ops/run_agentic_offline_qualification.py --live-golden
python -B ops/run_skill_ablation.py
python -B ops/probe_cache_hit.py
python -B ops/set_agentic_qualification.py OFFLINE_QUALIFICATION --dry-run
```

`INTERNAL_DOGFOOD` 仅当报告 `live_model_v2_green=true`（需模型密钥的 `--live` / `--require-live`）。
