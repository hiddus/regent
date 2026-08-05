# Regent 落地开发计划：先跑起来（2026-08-04）

> **原则**：不做门禁 / 开窗 / GQ / MA-5 作为上线前提。验收 = 控制台能点开可用 Preview。跑 Goal → 看失败 → 改代码 → 再跑。
> **状态**：S1–S6 代码已落地（2026-08-04）；S7 靠生产手测金色路径验证。

## 已落地代码

| ID | 改动 |
|----|------|
| S1 | `generation_strategy` 默认 `agentic`；`.env.example` 写死 agentic |
| S2 | system prompt + runtime-contract 禁止发明 `/health`；fastapi profile 去掉隐式 `/health` |
| S3 | Preview 缺省 `runtime` 真进程；`failure_envelopes` 注入 ContextAssembler |
| S4 | `DELIVERY_GAP_AUTO_CONTINUE_MAX=0`（禁无 lessons 空转） |
| S5 | 停滞 nudge≤2 后 sticky soft-pause，停假 ACTIVE 空转 |
| S6 | `submit`/COMPLETE 拦截未完成清单项 |

## 放弃

- GQ / canary 百分比放量
- MA-5 盲评、DecisionRecord 才开 Hive
- 自适应拓扑 / 自改晋级门
- 新治理指标与实验框架代偿

## 唯一成功标准（金色路径）

1. 输入：「做一个显示当前时间的 Flask 页面」
2. 5–15 分钟内出现**真进程** Preview
3. 打开能看到时间；刷新仍可用；不靠人工救火脚本

## 每日作业（取代开窗）

- 每天手跑 3–5 个固定小 Goal（时间页 / Todo / 一句话）
- 失败当天改代码、部署、重跑同题
- 禁止：新实验框、新组织模板、新指标仪表盘
