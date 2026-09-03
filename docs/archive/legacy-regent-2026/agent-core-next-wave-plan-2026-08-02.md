# Agent 内核下一波计划（W4 · 2026-08-02）

> 状态：**DONE（已提交 + S0 部署验证 + QUAL 升至 INTERNAL_DOGFOOD）**  
> Commit：`ffad949` + 后续 recreate/报告提交  
> 默认 **D1=A**；P2 后置未做  
> 生产流量：`canary_percent=0`、`canary_gate=false`（**未开 canary**）

---

## 实现账本

| ID | 动作 | 状态 |
|---|---|---|
| P0-1 | CJK token 计权 + `prompt_tokens` EMA 校正 | **DONE** |
| P0-2 | `ops/probe_cache_hit.py` | **DONE** |
| P0-3 | 门禁噪声 + subagent submit | **DONE** |
| P0-4 | 审计勘误 | **DONE** |
| P1-1 | live golden 车道 | **DONE**；S0 `live_model_v2_green=true` |
| P1-2/3 | 中文 Skills + 消融报告 | **DONE**（20/20 非空） |
| P1-4 | `agent_context_window_tokens` | **DONE**（S0=128000） |
| Q3 | CANARY `sample_gates` | **DONE** |
| P2 | 流式/MCP/gather | **DEFERRED** |

## 部署验证（S0 · 终态）

```text
QUAL=INTERNAL_DOGFOOD
STRATEGY=artifact-backed（默认仍 fallback）
CANARY_PERCENT=0
CANARY_GATE=false
health ok；Skills=7；CJK estimate / Preview rewrite OK
live golden 报告：docs/agentic-live-golden-report-2026-08-02.json
```

注意：`set_agentic_qualification --remote` 会写 host env 并 `recreate_from_deploy_env`；之后必须再跑 `sync_local_to_server.py`（镜像层会盖掉 docker-cp 代码）。

开 canary（另决策，需 sample_gates≥20）：

```text
python -B ops/set_agentic_qualification.py CANARY_5 --remote --also-canary-percent 5 --also-gate true --report <含 sample_gates 的报告>
python -B ops/sync_local_to_server.py
```
