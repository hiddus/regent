# Regent P0 完成交付报告

日期：2026-07-16  
交付方式：P0 整体交付（未拆分 P0A/P0B）  
生产地址：http://118.31.171.159:8000/console/

## 完成结论

Regent P0 已满足《Regent PRD》第 9 节的五项整体完成条件。生产严格审计 13/13 通过，数据库迁移位于 `20260716_0009 (head)`，公网数据库就绪检查与控制台均正常。

## 第 9 节逐项证据

1. Core 在空 Apps 条件下通过 `CSV_SUMMARY_BASELINE`：生产库有 2 个 ACHIEVED 基线目标，输出内容与固定摘要一致，Artifact 内容哈希完整。
2. 普通 Goal 可形成可解释的最小组织、记录能力缺口并通过 `EVT_PARSER_GAP`：认证 EVT 工具 1 个，公开与隐藏测试均通过，能力认证及撤销链已验证。
3. 独立 Apps 交付：`apps/regent-console` 可独立运行，公网返回 HTTP 200，新 App 未改变 Core 领域模型。
4. 治理与恢复闭环：Goal/Work/Run 状态转换审计缺口为 0；副作用 UNKNOWN 已对账为 RECONCILED；Permit、人审、吊销/过期、密钥隔离、幂等、防重放、Artifact/Evidence 哈希和 Worker Lease 均已验证。
5. 冻结 A/B/C 首轮实验：冻结 Manifest `0f64f746-9ec3-4409-acd4-93f4aff9eae4` 已完成 270/270 次运行，唯一 DecisionRecord 为 `ec17a72f-54cb-4771-89b0-70a7bd9490ef`。

## 产品决策

决策：`STOP_GENERALIZATION`

动态组织模式 C 未通过预先冻结的净收益门槛。本轮不把“动态组织优于强单 Agent”作为产品卖点；后续 P1 默认采用受控单 Agent/固定组织模板，仅在新的冻结假设和任务集下重新验证动态泛化。该决策停止的是动态组织泛化，不是停止 Regent 产品或 P0 上线。

首轮成功率：

- A（强单 Agent）：50.00%
- B（固定组织模板）：36.67%
- C（动态组织）：45.56%

三组安全事件均为 0，恢复正确率均为 100%。

## 可复核产物

生产目录：`/opt/regent/artifacts/experiments/p0-v1`

- `raw-run-manifest.json` SHA-256：`6a9b89a50942af96c581010a8c749185835f3dd21b68c68645a8b8c8230e2fcf`
- `experiment-report.json` SHA-256：`8ba5caad35b06895c0cd7e72606ef05d77f149284bc78450f1c8a974c832815b`
- `README.md` SHA-256：`156ee08dff5bd28d4098913dacdc712d448fe3f69ea32c8dce599be8b4507591`

Manifest、原始运行清单和 DecisionRecord 均带签名或签名引用。签名密钥仅保存在生产服务器的受限 secrets 文件中。

## 验证摘要

- 本地：Ruff 通过、Mypy strict 通过、Pytest 52/52 通过。
- 生产审计：迁移、CSV、EVT、组织边界、能力缺口、UNKNOWN 对账、Artifact 哈希、状态审计、Worker Lease、签名 Observation、270 次实验、唯一决策、控制台产物全部通过。
- 公网：`/health/ready` 返回 `status=ok, database=ok`；`/console/` 返回 HTTP 200。
