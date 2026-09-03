# P1 立即合并程序（已废除 Day+7 等待）

> 替代原 `DAY7_MERGE_PROCEDURE.md`。Owner 校准：不断进化，不强制等日历。

## 合并顺序（现在可执行）

1. 确认 G6：≥2 独立路径含 ≥1 次拒绝→REVISE→新 Discovery（见 `g6_g7_prewindow_evidence.json`）  
2. 确认 G7：≥1 次 REVISE/CONTINUE/STOP + ≥3 合格 Observation  
3. `PRODUCT_EVIDENCE_GRADUATED` → `PASSED`  
4. SYSTEM 人工会签齐（`COUNTERSIGN_CHECKLIST.md`）→ `SYSTEM_GRADUATED=PASSED`  
5. 文档 CONDITIONAL → `CURRENT`  
6. 写 `P2StartDecisionRecord`  
7. **此后**才允许 `p2-scheduler-01`

## 禁止

- 用「还要再等 N 天」阻塞进化闭环毕业  
- 跳过 CURRENT / P2Start 直接 Scheduler  
