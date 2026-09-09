# Novel Director 完备性复核 v7.8 —— D-01～D-04 闭合报告

> 日期：2026-09-09
> 前置：[v7.7 复核](novel-director-completeness-v7.7.md)（用户复核，提出 4 个闭环缺口）
> 结论：**D-01～D-04 全部修复并通过反证。全量单测通过。**

## 总览

| 缺口 | 缺陷 | 修复位置 | 测试 | 反证结果 |
|---|---|---|---|---|
| D-01 | 纠错内容装配后丢失 | `generation.assemble` + `works._queue_replay_run` | 3 例 | 装配丢弃 → 红；合并退回静默丢弃 → 红 |
| D-02 | 重演依赖顺序未保证 | `works.advance_background_run` | 5 例 | 屏障移除 → 4 例红 |
| D-03 | 记忆投影接错流程 | `domain/memory.py` + `domain/context.py` + `direction.py` | 6 例 | 接线/契约退回 → 4 例红 |
| D-04 | 终局预算未启用 | `generation.generate_ending_verdict` | 2 例 | 耗尽门移除 → 红；不传预算 → 红 |

新增 `tests/unit/novel/test_d_batch.py` **15 例**，全部通过；反证脚本 `deploy/novel/mutate_check_d.py` 两场景合计 **11 例次变红，8 个变异全部被咬住**。全量单测通过（含 infrastructure，2 个环境性跳过与产品无关）。

## D-01 纠错内容穿过 ASSEMBLE 重建

**根因**：`assemble()` 重建 `run.generation_context` 时只保留执行器钉住键（`PINNED_CONTEXT_KEYS`），排队时写入的 `correction` / `replay_reason` 在重建那一刻被清掉——重演拿到的是一次「不知道要改什么」的普通重跑。

**修复**：

1. `assemble()` 重建字典时显式携带旧上下文中的 `correction` / `replay_reason` / `corrections` 三个键。`plan_chapter` 本就把全部上下文键（除 production）发给导演，因此纠错语义自然到达导演请求。
2. `_queue_replay_run()` 对「已有排队中的重演」从**一律静默拒绝**改为**按 statement 去重/合并**：同一报错（statement 相同）仍去重，不叠任务；**不同报错合并进排队运行**——`corrections` 历史追加、`correction` 指向最新一次、`replay_reason` 保留，并落 `run.correction_merged` 事件留痕。普通重排（无纠错）被纠错请求命中时同样合并。

**测试**：装配后 correction 仍在；不同报错合并为单任务且历史含两条、当前指向最新；（既有）重复报错不叠任务保持绿。

## D-02 后台领取的依赖屏障

**根因**：`advance_background_run` 按 `(updated_at, chapter_no)` 排序领取，没有依赖屏障——第一章尚未完成（甚至刚排队）时，updated_at 更旧的第二章可能被先领取，剧情依赖断裂。

**修复**：领取改为逐候选检查：

- 候选为 **QUEUED（新章起跑）**时，若同作品同分支存在**更早章节**的运行处于在途状态（QUEUED / RUNNING / PENDING_DECISION / AWAITING_INPUT / RETRYABLE_FAILED），跳过该候选。
- 屏障**只拦起跑**：RUNNING / RETRYABLE_FAILED 的续跑不受影响；不阻塞其他作品（逐候选跳过而非整体停摆）。
- 领取前**锁内复核**（`SELECT ... FOR UPDATE SKIP LOCKED` 复核状态与租约），扫描与加锁之间被其他 worker 改写的候选自动失效——覆盖双 Worker 与租约接管。

**测试**（5 例）：第二章更旧也被跳过、worker 转而续跑第一章；第一章租约在期（另一 worker 在跑）时本 worker 什么都不领；两章都 QUEUED 先领第一章；第一章 CANONIZED 后屏障放行第二章；第一章 PENDING_DECISION（人在回路）挡住第二章。

## D-03 六视角记忆投影接入 director_v2

**根因**：v7.6 的 `_memory_view` 投影只接在旧流程（perform/direct/weave）；`director_v2` 场景链（plan_chapter / produce_tick）没有经过这些函数——导演计划请求全量倾倒召回 memory，ACT 角色拿不到自己的记忆，writer 拿不到叙述者视图。

**修复**：

1. 投影实现下沉到 **`domain/memory.project_payloads()`**（纯函数、无检索）：旧流程 `generation._memory_view` 变为委托，`direction` 直接从 domain 导入——**新旧流程共用同一裁剪实现，不得漂移**。（注意：不能从 direction 模块级导入 generation——`executor` 顶层依赖 `direction`，会成环；domain 是正确归属。）
2. `domain/context.py` 三个装配器新增 `memory` 参数并留痕 manifest：
   - `compile_actor_context` → payload `character_memory`（调用方传已按角色投影的一份）；
   - `compile_writer_context` → payload `narrator_memory`；
   - `compile_director_performance_context` → payload `director_memory`。
3. `direction.py` 接线四处：**plan**（`context` 排除 raw `memory`，另发 `director_memory`）、**ACT**（逐角色 `_memory_view(memory, "character", persona)`——每个 compiled payload 只含本人视角，Hive 隔离保持）、**WATCH_TAKE / WATCH_PROSE**（导演视图）、**RENDER**（叙述者视图）。

**测试**：domain 投影视图边界（导演笔记不进角色/叙述者）；三个装配器契约 + manifest 留痕；**produce 级真实请求证明**——plan/ACT/WATCH_TAKE/RENDER/WATCH_PROSE 五种请求逐一断言各自的投影（ACT 只含 rule + 与主角相关的 promise，无读者认知/导演笔记/他人误信）。

## D-04 终局裁决接入预算上限

**根因**：`generate_ending_verdict` 已走 CallBroker（幂等、恢复、计费），但构造时未传 `budget_limit_minor`——终局调用成为绕过章级货币上限的旁路。

**修复**：复用导演 `_call_batch` 的同一口径——`remaining = MAX_COST_MINOR − run.production.committed_minor`；`remaining <= 0` 时在调用 provider 前 `raise ProductionStopped`；否则 `CallBroker(..., budget_limit_minor=remaining)`，预留由账本原子校验。

**语义边界**：调用方 `_director_ending_verdict` 把异常折算为「没有判断」（None），**不是**「判断为没讲完」——预算故障不会触发自动扩卷（B-05 纪律不变）。

**测试**：耗尽时不发模型调用（provider 请求数为 0）且抛 `ProductionStopped`；spy 证明 `budget_limit_minor` 按「上限 − 已结算」传递（退回不传 → 红）。

## 反证方法与一次自纠

`mutate_check_d.py` 首跑只咬住 7/9 个变异：**同一文件出现在多个注入条目时，各条目独立从原文出发，后写覆盖先写**——generation.py 的装配丢弃与耗尽门两个变异被 D04_WIRE 覆盖掉了，反证看起来成立实则漏测。已修为按文件聚合后依次应用，重跑后 8 个变异全部被咬住。这个坑与 v7.6 的「C-04 两方向互斥需分场景」同属一类：**反证脚本的注入机制本身也要被校验**。

## 覆盖统计

- 变红 11 例次 / 15 例；未变红的 4 例为正向控制（视图边界常量、RENDER 契约、WATCH 契约、屏障放行正向用例）——它们描述投影内容本身，不针对单一变异，其咬合由其他用例承担。
- 全量单测：通过（`tests/unit` 全目录）。

## 仍开放（与 v7.7 一致）

- B-04 真实灰度、B-06 运行时认证
- M2 浏览器旅程矩阵
- R4 真实 pilot 采样与人评冻结
- M4 长篇认证（20/50/100/150）
- PRD §10：D-04～D-06 待拍板（首批题材 / 裁决超时 / MVP 收费）
