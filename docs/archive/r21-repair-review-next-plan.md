# R21 修复结果复核与下一批计划

日期：2026-09-11（v7.12 实施续）。v7.11 复核打开的 F1/F2/F3 已在本轮落地；F4 联合取证仍开放。

## 本轮实施（相对 v7.11 计划）

- **R21-F1**：`_validation_report_defects` 按 take 协议强制覆盖；双空报告缺陷；入口级 `test_empty_validation_report_cannot_reach_accepted`。
- **R21-F2**：`apply_patch` 拒绝非连续单 replacement；offset 合并保留分隔符；`allowed_paragraph_ids` 越界拒绝。
- **R21-F3**：`REQUIREMENTS_VERSION=r21_v2`；显式 `实体.属性` 拆分；同属性换值时保留可见中间 event；已冻结旧 take 不因版本升级漂移。
- 定向回归：`tests/unit/novel` + `test_provider.py` 通过；`mutate_check_r21.py` 6 变异全杀；离线探测确认空报告不放行、非连续拒绝且拆补丁保 KEEP。

## 仍开放

- ~~**R21-F4**~~：已闭合，见 [F4 摘要](../deploy/novel/artifacts/r21_f4_summary.md)（`c693853f` 同次补丁成章；run1/2/3b 三次成章样本）。
- 后续 **R3** 连续三章记忆写入/召回/兑现与纠错；M2 / R4 / M4 不变。

历史复核原文（缺陷描述与关闭条件）仍作对照；状态以总计划 v7.12 与上方实施段为准。
