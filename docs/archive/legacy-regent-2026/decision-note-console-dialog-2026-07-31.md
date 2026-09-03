# DecisionNote: 控制台对话框（规则透明 + 超时默认）

**日期**：2026-07-31  
**状态**：ACCEPTED  
**依据**：[console-dialog-prd-2026-07-31.md](./console-dialog-prd-2026-07-31.md)、[console-dialog-plan-2026-07-31.md](./console-dialog-plan-2026-07-31.md)

## 决策

1. **Web 优先**：确认 UX 落在 Regent Console + Core `HumanTask` / 会话消息 metadata，不假设 CLI rich。
2. **合同先行**：`ConfirmationRequest` + `resolve_default` + `decision_policy.evaluate`；画像默认 `balanced`。
3. **死等定义**：无超时默认的 HumanTask / 前端 waiting；超时须应用 `default_on_timeout`（安全项 `timeout=0`）。
4. **安全不变量最高**：四处 fail-closed 门包装为 `safety_invariant=True` 确认信封，仍抛 `DomainError`；画像不可放行。
5. **不重复死重**：文档与实现不声称 `validate_goal_alignment_semantic` 仍默认调用（见 [decision-note-dead-weight-trim-2026-07-31.md](./decision-note-dead-weight-trim-2026-07-31.md)）。

## 不做

不引入 LangGraph；不扩 Hive；不擅自改生产 `REGENT_GENERATION_STRATEGY`；不做本专题 DB 迁移。
