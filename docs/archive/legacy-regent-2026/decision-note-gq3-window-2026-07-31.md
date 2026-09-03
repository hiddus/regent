# Decision Note — GQ-3 开窗合同（CD-8）

> 状态：**SIGNED（可开窗）** — 非 GQ-4  
> 日期：2026-07-31  
> 关联：CD-6 ✅ · CD-7 ✅ · [`gq34-promotion-control-flow-2026-07-31.md`](./gq34-promotion-control-flow-2026-07-31.md)

## 结论

在 **CD-6 S0 已验证** 且 **CD-7.1–7.5 代码侧收口** 后，允许开启 **小流量 GQ-3 canary**。  
本文件**不等于** GQ-4；开窗期默认臂曾为 `artifact-backed`。**现行代码默认**已是 `agentic`（见 `config.py`）；禁止仅凭 `.env=agentic` 宣称 GQ-4 晋级。

## 开窗参数（冻结；2026-07-31 修订）

| 旋钮 | 值 | 说明 |
|---|---|---|
| `REGENT_GENERATION_STRATEGY` | `artifact-backed` | **窗内历史配置**（开窗时对照臂）；现行代码默认 `agentic` |
| `REGENT_GENERATION_STRATEGY_CANARY_GATE` | `true` | GQ-2→GQ-3 顺序门 |
| `REGENT_GENERATION_STRATEGY_CANARY_PERCENT` | **`20`** | 由 5% **修订升档**（首份报告 n≪30；日均 Goal 过低，5% 无法在 21 天内凑样本） |
| `REGENT_GENERATION_STRATEGY_CANARY_VARIANT` | `agentic` | 命中臂 |
| `REGENT_GENERATION_STRATEGY_KILL_SWITCH` | `false` | 演练时可立刻 `true` |
| `REGENT_GENERATION_STRATEGY_FALLBACK` | `artifact-backed` | kill 后回落 |
| `REGENT_DEPENDENCY_EGRESS_PROXY` | `http://regent-egress:3128` | 必填 |

## 实验合同

| 项 | 合同 |
|---|---|
| 冻结任务集 | 生产既有 Goal 流；不另造合成满分任务 |
| 样本量起点 | ≥30 **独立 Goal**/臂（intent-to-treat：窗内该 Goal 首个 `generation_plan` 的 `generator_ref`） |
| 成功定义 | `goal.status == ACHIEVED` |
| 最长窗长 | **21 天**（自 `opened_at`）；到期仍不足样本 → `INSUFFICIENT_EVIDENCE` 关窗，不晋级 |
| 停止规则 | kill switch；或 agentic 失败率相对对照臂 **+15pp**（`ops/gq3_production_report` 强制 `KEEP_ARTIFACT_BACKED`） |
| 报告 | `ops/gq3_production_report.py` → `docs/gq3-experiment-report-*.json`；双臂成功率 / 成本 / P95 + Wilson **95% CI**；PRD §10.5 用户结果 |
| 晋级门槛（冻结，与代码 `PreregisteredThresholds` 一致） | lift ≥ **5%**；n ≥ **30**/臂；成本退化 ≤25%；P95 延迟退化 ≤30%；mean repair ≤3；交人率 ≤20% |
| 晋级 | 仅 `decision=PROMOTE_AGENTIC_CANDIDATE` → `apply_gq4_promotion` + DecisionRecord **ACCEPTED** → 再翻 `REGENT_GENERATION_STRATEGY=agentic` |
| egress | **必须** `REGENT_DEPENDENCY_EGRESS_PROXY=http://regent-egress:3128` 写入 `.deploy.env`（`ops/ensure_gq3_egress.py` / 开窗脚本 upsert） |

## Kill switch 演练

1. 设 `REGENT_GENERATION_STRATEGY_KILL_SWITCH=true` 并 recreate worker/api（之后须 `ops/deploy_console.py`，避免 `/console/` 回退镜像静态资源）。  
2. 新 Run 必须全部落在 `fallback=artifact-backed`。  
3. 演练通过后恢复 `false` 继续采数（或保持关闭若异常）。

## 禁止

1. 跳过 canary 直接翻 `REGENT_GENERATION_STRATEGY=agentic`。  
2. 用本窗宣称 GQ-4。  
3. 无 egress 时依赖 agent pip/curl 裸开网（须 `REGENT_DEPENDENCY_EGRESS_PROXY`）。  
4. recreate `regent-api` 后不重打控制台（公网 `/console/` 会回到镜像旧 UI）。

## 报告 / 晋级入口

```text
# 采数报告（可随时复跑）
python ops/gq3_production_report.py --since 2026-07-31T10:00:00+00:00 --max-days 21

# 升档流量（默认 20；会 recreate api/worker 并重打 console）
python ops/bump_gq3_canary_percent.py --percent 20

# GQ-4：先 dry-run；仅 PROMOTE 时加 --execute
python ops/apply_gq4_promotion.py
python ops/apply_gq4_promotion.py --execute --write-accepted-note
```

## 修订记录

| 时刻 | 变更 |
|---|---|
| 2026-07-31 开窗 | 5% canary SIGNED |
| 2026-07-31 晚 | 首份报告 `INSUFFICIENT_EVIDENCE`（artifact=4, agentic=1）→ **percent 修订为 20%** |

## 记录

| 字段 | 值 |
|---|---|
| Decision | SIGNED — GQ-3 at **20%**（修订自 5%） |
| Author | rechaos |
| Blocker cleared | CD-6 S0 + CD-7.1–7.5 |
| Report tooling | `ops/gq3_production_report.py` |
| Promotion tooling | `ops/apply_gq4_promotion.py`（门禁强制；未达标不可 execute） |
