# 对话式交付 · 下一步计划（CD-6…CD-12）

> 状态：**ACTIVE**（2026-07-31 重订）  
> 前序：[`conversational-delivery-plan-2026-07-31.md`](./conversational-delivery-plan-2026-07-31.md)（CD-0…CD-5 ✅）  
> CD-6 执行级展开：[`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md)（经[技术专家核实](12a89078-b415-488b-9e68-eef2f7dbaba8)修订）  
> 输入：产品/架构专家 · 审计 §8 · N-3c/N-3d 复核  
> 互指：`Regent-Plan.md` §14.4

## 0. 阶段目标

CD-0…CD-5 与审计 F-1…F-9 已闭环。下一阶段**不再扩功能面**，按证明链推进：

| 序 | 目标 | 若不做的后果 |
|---|---|---|
| 1 | **CD-6** 沙箱真执行（N-3 族 + 守卫） | GQ-3 对照数据无效或假绿 |
| 2 | **CD-7** 交付可靠性硬债（技 P1-1…4 + N-4/N-6） | 误路由 / 跨 goal 预算污染实验 |
| 3 | **CD-8→9** GQ-3 窗 → 条件 GQ-4 | 不得用 `.env` 宣称晋级 |
| 4 | **CD-10…12** 能力执行 → 推流 → 结构后置 | 体验增强，不阻塞晋级门禁 |

**禁止**：把 agentic 未默认当缺陷；删护城河；CD-6/7 未绿开生产 canary；仅用 `echo ok` 验收沙箱。

---

## 1. 批次总表

| 批次 | 名称 | 依赖 | 状态 |
|---|---|---|---|
| **CD-6** | 沙箱真执行 + 回归守卫 | CD-0.1 | ✅ **S0 已验证**（2026-07-31）：镜像构建 + worker 内三联 e2e；`--group-add docker` |
| **CD-7** | 交付可靠性硬债 | CD-6 全绿 | 🟢 **7.1–7.5 已落地**（待部署复核） |
| **CD-8** | GQ-3 真实 canary 实验窗 | CD-6+7 | 🟡 **历史曾开窗后已 clamp**（见 `m6-canary-window-2026-08-01.json`）；报告/晋级脚本就绪；样本不足 → GQ-4 PENDING；**不得写现行 20%/5% ACTIVE** |
| **CD-9** | GQ-4 条件晋级 | CD-8 报告达标 | 🟡 PENDING |
| **CD-10** | capability 执行适配器 | CD-8 后（可并行设计） | ⚪ |
| **CD-11** | token 流 / SSE LISTEN | 不阻塞 GQ | ⚪ |
| **CD-12** | Coordinator + F-10 | CD-7 稳定后 | ⚪ **现在不抽** |

---

## 2. CD-6 · 沙箱真执行 + 回归守卫

> **权威工作包**：[`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md)。本节只保留批次合同；细节以执行级为准。

### 2.1 缺陷族（同源：构建沙箱被复用为命令沙箱）

| ID | 问题 | 证据（摘要） |
|---|---|---|
| **N-3** | 无 `--entrypoint`；构建镜像 ENTRYPOINT 吞掉 `sh -lc` | `sandbox.py` argv；`bootstrap/sandbox/Dockerfile` |
| **N-3c** | worker uid 65534 vs 沙箱 65532 → **写盘** EACCES；无 `HOME` → pip 失败；`echo` 可假绿 | `core/Dockerfile`；`--user 65532` |
| **N-3d** | 容器路径直接作 `--mount src` → 常静默挂空目录 | compose workspace 绑定；`sandbox.py` mount |
| **N-3b** | compose 无 docker.sock → 容器化 worker 无法 `docker run` | `compose.yaml` |
| **N-2** | 运维未声明 `REGENT_SANDBOX_MODE` / 支持矩阵 | deployment / `.env.example` |
| **N-1** | canary 校验死代码（保护已由 production 校验提供） | `config.py` |

### 2.2 工作包顺序

```text
6.1 专用 agent-exec 镜像 + entrypoint（禁止以仅 hotfix 作验收）
6.2 执行身份与写权限一致（N-3c；Owner 选 B1/B2/B3）
6.3 host_path_map + 容器内无 map 则 fail-closed（N-3d）
6.4 DinD 打通 或 支持矩阵定界（N-3b；sock 须安全附录）
6.5 回归守卫 T1–T6 + verdict 行为断言整改
6.6 运维配套（N-2）；6.7 N-1 清理可并行
```

**CD-6 期间网络主路径**：临时移除 `_NETWORK_PREFIXES`（全程 `--network none`），避免真执行刚点亮即裸开网。完整 N-4 治理默认留 **CD-7.5**（Owner 可选加速，须改本表）。

### 2.3 出口判据（缺一不可进 CD-7）

1. docker 下 **`echo ok` + 写文件可见 + pytest** 三联真实执行（禁止单靠 echo）。  
2. 容器内缺 `host_path_map` → fail-closed（含配置项名）。  
3. T1–T6 进 CI；T6（argv 契约）**无条件运行**。  
4. `deployment.md` 支持矩阵 + `.env.example` 可起 Path B。  
5. 沙箱 uid ≠ 0。

---

## 3. CD-7 · 交付可靠性硬债

严格顺序：**7.1 → 7.2 → 7.3 → 7.4 → 7.5**；**7.4 必须在 CD-8 前合入**。

| 切片 | 问题 | 方案要点 |
|---|---|---|
| **7.1** 技P1-1 | `_EVIDENCE_MARKERS` 裸 `"http"`/`"observed"` 误路由 | ✅ 删过宽 token；整码/前缀匹配；负例 CI |
| **7.2** 技P1-2 | `recover()` 事务内 httpx + 嵌套 session | ✅ 写库与 ACQUIRE 分离；Permit/EO + egress |
| **7.3** 技P1-3 | `delivery_profile` vs `decision_preference`；`_GATE_MAX` 硬编码 | ✅ D5；Gate 吃 multiplier |
| **7.4** 技P1-4 | `_ensure_agentic` 冻预算跨 goal | ✅ per-budget 隔离 |
| **7.5** N-4 / N-6 | pip/curl 裸开网；transcript 无稳定 error_code | ✅ egress 强制；`TRANSCRIPT_PERSIST_FAILED` |

**验收**：URL 不误入 evidence；ACQUIRE 无外层事务；两 goal 预算不污染；Gate 次数可复算。  
**门禁**：CD-7 代码侧已绿；开 CD-8 见 [`decision-note-gq3-window-2026-07-31.md`](./decision-note-gq3-window-2026-07-31.md)。

---

## 4. CD-8 · GQ-3 实验窗

| 维度 | 内容 |
|---|---|
| **叙事** | 小流量对照两种生成方式；命中走增强路径，否则稳定默认 |
| **范围** | 冻结任务集 + 样本量/停止规则；`canary_gate` + 小 percent；双臂 + 95% CI；PRD §10.5 用户结果指标 |
| **验收** | 可复算报告；kill switch 演练；未达标保持 artifact-backed |
| **禁止** | `.env=agentic`=晋级；跳过 canary 翻默认 |

---

## 5. CD-9 · GQ-4 条件晋级

仅 CD-8 达标 → `apply_gq4_promotion` + DecisionRecord **ACCEPTED** → 再改运行时默认。  
未达标：DecisionNote 维持 PENDING。

---

## 6. CD-10…CD-12

| 批次 | 要点 | 时机 |
|---|---|---|
| **CD-10** | ToolSpec **执行**适配器（≥ `product-surface-v1`）；不可逆仍 Permit | CD-8 后；**优先于**推流 |
| **CD-11** | `chat_stream`；LISTEN/NOTIFY；可回退轮询 | 不阻塞晋级 |
| **CD-12** | `DeliveryRecoveryCoordinator` 零行为抽离；F-10 | CD-7 稳定后；**勿插入 CD-8 合并窗** |

---

## 7. Owner 必拍板

| # | 决策 | 默认建议 |
|---|---|---|
| D1 | **N-3c uid 方案** B1/B2/B3 | B1（运行时 uid + uid≠0） |
| D2 | **CD-6.4** DinD vs 支持矩阵定界 | 定界（Path A 宿主 worker）除非接受 sock 风险附录 |
| D3 | **N-4** 是否加速进 CD-6 | 否；CD-6 禁 `_NETWORK_PREFIXES`，N-4 留 7.5 |
| D5 | **画像权威源**（profile vs preference） | ✅ [`decision-note-d5-persona-authority-2026-07-31.md`](./decision-note-d5-persona-authority-2026-07-31.md) |
| D4 | **GQ-3 开窗合同**（percent/样本/门槛） | ✅ [`decision-note-gq3-window-2026-07-31.md`](./decision-note-gq3-window-2026-07-31.md)（5%） |
| D6 | **CD-10 vs CD-11** | 能力执行 > 推流 |

---

## 8. 时间盒（建议 6 周）

| 周 | 焦点 | 产出 |
|---|---|---|
| **W1** | CD-6.1–6.3 | agent-exec 镜像；uid；path map + fail-closed |
| **W2** | CD-6.4–6.6 + T1–T6 | 支持矩阵或 DinD；守卫进 CI；env/文档 |
| **W3** | CD-7.1–7.4 | marker / 事务 / 画像 / per-goal 预算 |
| **W4** | CD-7.5 + CD-8 开窗 | N-4/N-6；小流量 canary + kill switch |
| **W5** | CD-8 收口 + CD-9 决策 | 95% CI；晋级或 PENDING；CD-10 设计 |
| **W6** | CD-10 主切片 | ≥1 能力端到端；11/12 择一薄切 |

---

## 9. 严格落地顺序

```text
① CD-6（见执行级 6.1→6.6；临时禁裸开网）
② CD-7.1 marker
③ CD-7.2 ACQUIRE 出事务
④ CD-7.3 画像 / Gate
⑤ CD-7.4 per-goal 预算隔离
⑥ CD-7.5 N-4/N-6
⑦ 部署复核 + kill switch
⑧ CD-8 GQ-3 → 报告
⑨ CD-10 执行适配器
⑩ CD-11 流式 / LISTEN
⑪ CD-12 Coordinator / F-10
⑫ CD-9 仅报告达标后 GQ-4 DecisionRecord
```

---

## 10. 文档职责

| 文档 | 职责 |
|---|---|
| **本文件** | 批次级 ACTIVE 下一步权威 |
| `cd6-execution-plan-2026-07-31.md` | CD-6 工作包 / 验收 / 风险（从属本文件 §2） |
| `Regent-Plan.md` §14.4 | 编码清单互指 |
| `decision-note-gq4-pending-*.md` | 晋级前置 = CD-6 全族 + CD-7 |
| `gq34-promotion-control-flow-*.md` | 开窗控制流 |
| `Regent-Technical-Spec.md` §13.8/§25 | 沙箱真执行与门禁状态 |
| `Regent-PRD.md` | 产品叙事交叉引用（不重复切片表） |
