# 玄幻验收复跑标注（2026-09-21）

作品：`c09cdc7f-9cdd-4a74-9fbb-c10ccc8c7de5`  
意图：与 `3e411fe2` 同（矿奴陆沉 / 东方玄幻）  
结果：**第 1 章 CANONIZED**（2467 字），`repair_history=[]`，无 VALIDATE 硬修轮。

## 相对旧失败的变化

| 对比项 | 旧跑 `3e411fe2` | 本跑 `c09cdc7f` |
|---|---|---|
| 失败形态 | VALIDATE 四条重叠「未兑现终点」 | 首次 COMPILE 未分配硬停 → 补 `ensure_acceptance_assigned` 后一次过 |
| 失败可定位 | 自由文案，无 item_id | 义务清单带 `a1…a7` / `ml*` |
| 页面态 | TERMINAL 仍 writing | 本跑成功；此前未分配失败时已见 `chapter_failed` |
| 修订轨迹 | 不可见 | `repair_history=[]`（本轮无需修） |

## 义务真假标注（相对 working_state + 契约）

> 说明：本轮未抽全文逐句核对；以落库 `working_state` 与义务文案对齐为主。display 非 required，不记硬真假。

| item_id | kind | 义务摘要 | 裁定 | 依据 |
|---|---|---|---|---|
| a1 | state_change | 血激活储物戒，手记可翻不可带出 | **真兑现** | ws「戒指状态：已用血激活并藏入裤腰夹层」 |
| a2 | state_change | 右臂经脉损三成，七日再淬 | **真兑现** | ws「陆沉经脉：损三成，需七日恢复」；右臂肿胀渗黑血 |
| a3 | state_change | 戒缝进裤腰夹层，未被发现 | **真兑现** | ws 戒指藏匿 +「未被赵铁山发现」 |
| a4 | cognition | 无灵根也能修，每步用血肉换 | **真兑现** | ws「陆沉认知：…以身为炉…用血肉换」 |
| a5 | dilemma | 手记残缺 / 苦艾草汁无着落（不要求解决） | **真建立** | ws「苦艾草汁仍无着落，手记后半残缺」；未拍板解决 |
| a6 | dilemma | 矿脉异动与收工禁下七层未解 | **待全文抽检** | 义务已挂 s3；ws 未显式写异动句，需看正文 |
| a7+ml* | display | 痛感/悬念表现 | 软项 | 不作为生产硬门；本轮未拦 |

## 过程观察

1. **RC2 生效**：补丁前同意图跑（`4fa599f5`）在 COMPILE 以 `acceptance_unassigned:acc-*` 硬停，且 `public_stage=chapter_failed`。
2. **缺口修复**：LLM 分场冲掉骨架注入 → 增加 `ensure_acceptance_assigned`，未分配 required 钉入末场 must_show 后再开拍。
3. **RC3/RC4**：本轮 VALIDATE 未触发硬修（`repair_rounds=0`），故未验证 `[acc:item_id]` 修订轨迹；需在后续故意制造缺口的样本上补测。
4. **compile_soft_issues** 仍含 `turning_point_not_covered` / `end_change_not_covered` 字面软项——与「义务已分配、按 kind 核验」并存，符合「字面覆盖软、义务硬」设计。

## 三章产品入口（同意图续跑）

作品：`f36f4bc2-8db7-46f6-9a0e-9413e9b4522d`

| 章 | 结果 | 字数 |
|---|---|---|
| 1 | CANONIZED | 3594 |
| 2 | CANONIZED | 3878 |
| 3 | TERMINAL_FAILED @ REVIEW | — |

第 3 章 PRODUCE 已过，REVIEW 因「章节修复次数已达上限」停机。`repair_history` 已按 round 记录 fails（含 `[front:power_surface]` / `[front:redundant]`），`acceptance_items` 仍挂 `a1…a8`。这是责编/前置硬门修满上限，不是旧的「四条重叠未兑现终点」。

证据：`xh_three_ch3_slim.json`、`product_evidence_xuanhuan.json`。
