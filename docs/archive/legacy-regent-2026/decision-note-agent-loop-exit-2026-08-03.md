# DecisionNote: Agent Loop 出口合同（A0）

**日期**：2026-08-03  
**状态**：ACCEPTED  
**相关**：[`execution-plan-agent-loop-exits-2026-08-03.md`](execution-plan-agent-loop-exits-2026-08-03.md)、[`absorption-plan-agent-matrix-2026-08-03.md`](absorption-plan-agent-matrix-2026-08-03.md) v3.4

---

## 0. 决策

每一轮 Agent lease **必须**落盘：

```text
exit_kind ∈ { COMPLETE, STOP, ASK_HUMAN }
```

禁止第四态 `RETRY_FOREVER`（含：验证失败后自动 `SESSION_RESUME`、换 lesson 空转、ATTRIBUTE_3 换标签当大脑）。

Session 只提供连续性；**不能**代替出口。

## 1. 不变量

| ID | 内容 |
|----|------|
| E-1 | 每轮 lease 结束必有且仅有一种 `exit_kind` |
| E-2 | COMPLETE 带 `result_bundle`；ASK 带 `ask_envelope`；STOP 带原因+草稿指针 |
| E-3 | 验证 FAIL / 预算硬顶 → ASK 或 STOP，**禁止**无问人自动再租 |
| E-4 | 人未答 ASK 前，Guidance CONTINUE 不得空白点火 |
| E-5 | COMPLETE ≠ 自动 Goal ACHIEVE（状态机分离） |
| E-6 | soft gates 不得把 FAIL 洗成 `stop_reason=verified_pass` |

## 2. 落点

- Schema：`regent.application.agent_loop_exit`
- 持久化：`goal.metadata_json["agent_loop_exit"]` + Session checkpoint 摘要
- 事件/文案：`AGENT_LOOP_COMPLETE` / `AGENT_LOOP_STOP` / `AGENT_LOOP_ASK`

## 3. 默认拍板（产品×技术）

1. 出 lease 的 FAIL → **ASK_HUMAN**（lease 内 repair 保留）  
2. soft 可暂留，但禁止假 COMPLETE  
3. ASK：metadata 问题单 + 对话；可选 HumanTask `AGENT_LOOP_ASK`  
4. COMPLETE 不自动 ACHIEVE  

## 4. 参考

OpenWork：artifacts、permission.reply、abort。  
Claude Code：AskUserQuestion、max_turns/预算、doom_loop→要人。
