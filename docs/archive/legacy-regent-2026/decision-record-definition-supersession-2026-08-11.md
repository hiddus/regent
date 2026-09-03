# DecisionRecord 定义源 supersession（2026-08-11）

历史 `DecisionRecord` / 签署门禁（如 `P2StartDecisionRecord`、AAR1*）若 JSON 内仍写 `definition_id=REGENT-DEFINITION-1.0`（或 2.0），**不改写历史签署字段**（须 Owner 签核）。  
已在下列 JSON 增加只读 supersession 字段（不改变 `status` / `authorized` 历史语义）：

- `superseded_by_definition`: `REGENT-DEFINITION-3.0`
- `definition_note`: 标明 1.0 为历史门禁；现行编码/产品规范源为 3.0

涉及文件：`P2StartDecisionRecord.json`、`AAR1MilestoneDecisionRecord.json`、`AAR1CodingReadinessDecisionRecord.json`。  
**当前编码与产品规范源**为 [`definitions/REGENT-DEFINITION-3.0.txt`](definitions/REGENT-DEFINITION-3.0.txt)；上述记录仅作历史准入证据，不得再当作现行定义正文。
