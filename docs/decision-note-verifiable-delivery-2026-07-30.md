# DecisionNote: 可验证交付路线与 Spec §25 纠偏

**日期**：2026-07-30  
**状态**：ACCEPTED  
**依据**：`docs/regent-verification-2026-07-30.md`

## 决策

1. **Spec §25 纠偏**：删除「Evidence Connector 为空」「Deployment Provider 为内存」等已证伪表述；Hive 改为 opt-in 固定模板 + 默认强单 Agent；SelfImprovement / Tauri 标明候选或探索性，禁止写成已验收。
2. **完成定义对齐**：P0「可验证交付」以治理管道行为 pytest、非桩 Eval、北极星/护栏与 DecisionRecord 为准，不以模块存在或结构级断言为准。
3. **执行顺序**：Phase 0 文档/CI markers → Phase 1 治理行为测 → Phase 2 Eval/度量 → Phase 3 隐私/证据五分类/溢出门禁。

## 不做

本注记不开启 P2-3 Impact Graph、P2-5 HMAC、G0 ExternalOperation 完整闭环；待 Eval 正收益门后再排期。
