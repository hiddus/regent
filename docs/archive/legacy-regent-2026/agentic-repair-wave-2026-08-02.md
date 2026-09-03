# Agentic 修复波次计划（2026-08-02）

> 状态：**SUPERSEDED by** [`agent-core-next-wave-plan-2026-08-02.md`](./agent-core-next-wave-plan-2026-08-02.md)（W4）  
> R0–R3 代码仍有效；生产 canary 仍禁止自动开启。  
> 融合：Cursor canvas `deadloop-agentloop-review` · [`agentic-qualification-executable-plan-2026-08-01.md`](./agentic-qualification-executable-plan-2026-08-01.md) · 10 项交付物账本  
> 裁决：自锁协议已废；**不得**宣称死循环全解 / 超级强悍 AgentLoop；本波次只做解锁 Qual 泳道的缺口

## 0. 账本（第一批 10 项）

| # | 交付物 | 状态 | 备注 |
|---|---|---|---|
| 1 | INVALID_BASELINE + DecisionRecord | **DONE** | |
| 2 | FALLBACK_ONLY + qualification 七态 | **DONE** | 枚举+分流；**无自动晋级状态机**（Q3） |
| 3 | Provider fail-closed 锁测 | **DONE** | |
| 4 | Artifact manifest | **DONE** | |
| 5 | Runtime Profile v1 | **DONE*** | *Preview 真启后端见 #7 |
| 6 | Runner 预算/transcript/去递归 | **DONE** | fingerprint / max_extra_turns / nested=4；candidate branch 已接线 |
| 7 | 真实 Preview 最小链 | **R1 DONE** | `PreviewProcessSupervisor` + start_command + readiness → SUCCEEDED |
| 8 | accepted_workspace_snapshot | **R1 DONE** | 成功 accepted；失败 `last_recoverable_workspace_uri` |
| 9 | Offline Qual 执行入口 | **R2 DONE*** | fixture → 仅 `OFFLINE_QUALIFICATION`；DOGFOOD 须 live+V2 报告；`set_*` 相邻+读报告 |
| 10 | Canary 开关 + 回滚 | **R0 DONE**（ops） | `set_agentic_qualification.py` + clamp；开档仍须 gate+percent |

## 1. 额外偏差（并入本波）

| ID | 项 | 动作 |
|---|---|---|
| D1 | `canary_gate` 第二道门 | **保留为运维闸**（非漏斗自锁）；文档写清：开 CANARY 须同时 `QUAL≥CANARY_5` + `percent>0` + `gate=true` |
| D2 | 晋级门槛未自动化 | **部分收紧**：`set_agentic_qualification` 相邻跃迁+读报告；≥20/≥40 样本门槛仍无执行点（Q3） |
| D3 | `_resolve_test_command` 过期单测 | **R0 修** |
| D4 | STATIC 挡 smoke（B5） | **R0：始终尝试 smoke**，静态失败仍记 gap |
| D5 | planned_path 后缀窄于 manifest | **R0：对齐 ts/tsx/jsx/vue/svg/sql** |

## 2. 波次

### R0 — 解锁 Qual 通道 — **DONE**

契约门禁 + ops 资格工具 + smoke/planned_path/stale 测修复。

### R1 — 真 Preview + 失败可恢复 — **DONE**

1. `PreviewProcessSupervisor` 执行 Profile `start_command` + HTTP readiness  
2. 失败路径写 `last_recoverable_workspace_uri`；REVISE：accepted → recoverable → draft  
3. Preview evidence 含 `profile_hash` / pid / port  
4. Soft-pause「给新方向」→ `resume_after_human`  
5. Smoke probe 使用 Profile `entry_object`

### R2 — Offline Qual 全链 golden — **DONE***（fixture 天花板已纠正）

1. `--full-golden`：契约 + 本地 fixture Preview+readiness（无 LLM）  
2. 出口绿 → 仅 `allows_state=OFFLINE_QUALIFICATION`（**不再**虚标 DOGFOOD）  
3. `INTERNAL_DOGFOOD` 须报告含 `live_model_v2_green` / live REVISE+V2 check  
4. `set_agentic_qualification.py`：相邻升级 + 新鲜报告；`--force` 破窗

### R3 — 收敛与 Skills — **DONE**

1. Skills 3→7：`persistence` / `http-api` / `evidence` / `ui`  
2. `system_prompt` 钉 `{entry_module}:{entry_object}`  
3. Soft-pause continue（与 R1 重叠）  
4. `allow_candidate_branch` 接到 repair user turn

## 3. 明确不做（本波）

- GQ-4 / 扩 canary / 自动晋级状态机  
- 活模型 Offline Qual 全自动（需密钥与沙箱配额时人工）  
- Skills 全量、多 Agent、跨 Goal cache 大优化  

## 4. 命令速查

```text
# 契约门禁 + 报告
python -B ops/run_agentic_offline_qualification.py --contracts-only

# fixture Preview golden（仅 OFFLINE_QUALIFICATION；DOGFOOD 须 live+V2）
python -B ops/run_agentic_offline_qualification.py --full-golden

# 资格态（本地 env 示例；S0 加 --remote）
python -B ops/set_agentic_qualification.py OFFLINE_QUALIFICATION

# 回滚流量
python -B ops/clamp_generation_strategy_freeze.py
```
