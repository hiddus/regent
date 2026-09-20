# 858a 失败修复计划（基于复核 RCA）

日期：2026-09-19  
依据：[858a-failure-rca-reviewed-2026-09-19.md](858a-failure-rca-reviewed-2026-09-19.md)  
衔接：[archive/continuous-creation-repair-plan-2026-09-17.md](archive/continuous-creation-repair-plan-2026-09-17.md)  
性质：可执行开发计划；采纳后按批次实施，不替代 Novel-Engine-Plan.md 作为长期产品路线。

## 0. 路线裁决（先读）

复核 RCA 的执行路线成立，本计划采纳：

1. **先修定位与证据闭环**（章内 VALIDATE/REVIEW/WRITE 映射）
2. **再修质量边界与修订验收**（不靠关键词软化真实矛盾）
3. **再修恢复与连续运行**（与内容修复分开统计、分开验收）
4. **不做**：扩大导演架构、无条件加重试、降低真实事实门槛、把 `find_similar` 升硬闸、用监督脚本成功冒充产品连续创作

**不把 a4/a5 的「修错场」写成已证明根因。** 当前代码已确认存在「整章失败 → 固定修末场」能力缺口；a4 轨迹与该缺口高度吻合，但仍缺 scene_id / 正文 hash / 模型请求串证。

---

## 1. 现状核对摘要（相对 RCA 的增量）

### 1.1 代码侧（证据级 A，本轮复读确认）

| 主张 | 现状 | 关键位置 |
|---|---|---|
| ScriptChapterValidation 无失败场定位 | 仅有 `hard_fails` / `soft_notes` / `facts`；独立审校 `ChapterValidation` 才有 `failed_scene_index` | `directing_contracts.py` |
| VALIDATE 硬失败固定末场 | `last_i = len(texts) - 1`，写 `scene_revision_instruction` 后回 `WRITE_SCENE` | `directing_script_loop.py` VALIDATE |
| ACCEPT render 失败也修末场 | 同样 `len(texts)-1` | 同上 ACCEPT_CHAPTER |
| WRITE 取旧稿用 `[-1]` | 依赖「回退已截断后续场」不变量；REVIEW 回退路径会截断 | WRITE_SCENE / `_rewind_script_after_review` |
| 关键词软化 | `_audit_cont_softable` 含「入场/连续/重复」等；纯 `continuity_block` 可 soft | `directing_script_loop.py` |
| `front:redundant` 不得 soft | VALIDATE / REVIEW 路径已拦；确定性门只返回摘录，**不返回场索引或双位置** | `prose_front_gates.py` |
| resume 不恢复失败章 | `resume_work` 只切 `work.state→RUNNING` | `works_run_control.py` |
| 成章不自动开下章 | 非末节点直接 `return` | `works_volumes._after_chapter_completed` |
| 结构化补丁已存在但未接 script 臂 | `prose_patch` / `editorial_repair` / `directing_scene_loop` 有 hash+段落补丁；**script_scene VALIDATE 仍用自然语言整场重写** | 多文件 |

### 1.2 运行证据（证据级 B，相对 RCA 的更新）

RCA 撰写时写「未定位 a4/a5 原始日志」。当前仓库已有证据包：

- `deploy/novel/artifacts/r3/858a_a4a5_2026-09-17/`（README + `supervise_529818.txt`）
- work_id：`858ad0b6-8fd4-447e-a560-553237862565`
- a4：`TERMINAL_FAILED` @ VALIDATE，`[front:redundant]`，tok≈506701，calls=20；轨迹含 VALIDATE→WRITE_SCENE→AUDIT→ASSEMBLE→VALIDATE 循环
- a5：`TERMINAL_FAILED` @ REVIEW，w=2251，worker 日志「修复没有改变正文」；轨迹含 REVIEW→WRITE_SCENE→AUDIT→VALIDATE→ACCEPT→TERMINAL
- 该轮为监督 regenerate，非纯产品入口

**仍缺（须 R0 补齐或明确标 C）**：各 scene_id、双重复位置、修订前后正文 hash、模型请求/响应、部署源码完整指纹（仅有单文件 SHA 前缀）。

### 1.3 与 9/17 连续创作计划的关系

| 连续创作项 | 本计划批次 | 说明 |
|---|---|---|
| C01 后继章 | W3 | 不解释 a4 VALIDATE 失败；独立验收 |
| C02 失败恢复 | W2（与章内恢复联动） | 与定位修复同属 P0，但分 PR |
| C03 卷末确认 | W3 | 非 a4/a5 直接原因 |
| C04/C05 调度与调用键 | W3/W4 | 不阻塞章内定位 |
| C06 状态投影 | W2 | 与恢复闭环一起做最小投影 |
| C07 跨章事实 | W4 | 定位与硬门稳定后再做 |

---

## 2. 目标与非目标

### 2.1 目标

- 整章硬失败能定位到 **scene_id（可多场）+ 证据片段 + 正文版本**，无法定位时显式「不可局部修复」停机，**禁止默认修末场**。
- 修订后按问题 ID 验收：原问题消失、未引入新硬错、无进展有界停止；「正文未变」区分「改错对象 / 未应用 / 模型未改」。
- 真实矛盾不可凭关键词软化；修满只改恢复策略，不改问题严重性。
- 作品 resume 按失败原因产生可领取任务或明确 blocker；不跳过失败章、不重复计费。
- 连续三章用**产品入口**验收，监督脚本只作诊断。

### 2.2 非目标（本计划明确不做）

- 不重构整套导演协议 / 不新增第二套执行器。
- 不把相似度检索直接当硬闸。
- 不降低 `front:redundant` / 真实连续性硬门。
- 不靠「CANONIZED 即质量合格」结案。
- 不在未完成定位修复前扩大 VALIDATE 重试次数。

---

## 3. 实施批次

### W0 — 证据基线（0.5–1 天，无业务行为变更）

**目的**：任何后续结论可追溯；避免用新样本替换失败样本「凑成功」。

| 任务 | 动作 | 出口 |
|---|---|---|
| W0.1 冻结证据包 | 确认 `858a_a4a5_2026-09-17` 为权威 a4/a5 包；补齐 README 中缺失项清单 | README 列出已有/缺失字段 |
| W0.2 源码指纹 | 对当前拟修复文件算 SHA256 全文；记录 HEAD + dirty 文件清单 | `fingerprints.json` 或 README 表 |
| W0.3 失败样本夹具 | 从 a4 日志与 `front:redundant` 行为抽出**合成**回放：多场正文中前场重复、中场重复、末场重复、跨场重复各至少 1 例（可用确定性门，不需模型） | `core/tests/.../fixtures/redundant_scenes/` |
| W0.4 干预标记 | 证据包标明 supervised / regenerate / DB 修补 | 验收报告不得把该轮当产品连续创作 |

**不做**：付费模型重跑、改线上状态、扩大监督脚本能力。

---

### W1 — P0 修复定位（核心，约 2–3 天）

**问题**：VALIDATE / ACCEPT render 硬失败无场映射 → 固定 `len(texts)-1`；`front:redundant` 只给摘录不给位置；WRITE 依赖 `[-1]` 隐含不变量。

#### W1.1 问题结构与定位契约

**文件**：`directing_contracts.py`、`prose_front_gates.py`、可选 `directing_types.py`

新增小型结构（名称可微调，语义固定）：

```text
LocatedIssue:
  issue_id: str          # 稳定 ID，如 front:redundant#<hash12>
  code: str              # front:redundant / continuity / ...
  severity: hard | soft
  scene_ids: list[str]   # 可多场；空表示无法定位
  evidence_quotes: list[str]
  content_hash: str      # 检出时整章或相关场 hash
  expected_action: remove_dup | rewrite_scene | escalate_nolocal
  verify_rule: str       # 机器可执行的复检提示，如「摘录不得再出现两次」
```

扩展：

- `ScriptChapterValidation`：增加 `located_issues: list[LocatedIssue]`（或并行字段，保持旧 `hard_fails` 兼容一段时间）
- `front_gate_hard_fails`：对 redundant **返回双段落位置**（paragraph index 或 byte offset）；上层映射到 `scene_ids`
- 确定性：给定 `scene_texts[]` + 拼接稿，用摘录/`snip` 在各场中搜索归属；双命中 → 多 `scene_ids`

#### W1.2 VALIDATE / ACCEPT 修订入口

**文件**：`directing_script_loop.py`

替换「固定末场」逻辑：

1. 合并 `chapter_fails` / `front_fails` 时先 `locate_issues(scene_texts, fails) → LocatedIssue[]`
2. 若全部 hard issues 的 `scene_ids` 为空 → `ProductionStopped("无法局部定位，拒绝默认修末场：…")`
3. 若单场 → 设 `scene_index` 为该场、截断该场之后的 `scene_texts` / `scene_state_trail`（与 REVIEW rewind 同规则）
4. 若多场 → 回退到**最早**受影响场（`min` index），`scene_revision_instruction` 标明全部相关 `scene_ids` 与证据；下游场失效
5. ACCEPT render 分支共用同一 `select_repair_target(...)`，禁止第二套末场魔法

#### W1.3 WRITE_SCENE 身份一致性

**文件**：`directing_script_loop.py`

- 修订时：`prior_draft = scene_texts[idx]`，断言 `idx == scene_index` 且 `cards[idx].scene_id` 与票据一致
- 替换：`scene_texts[idx] = new`，禁止无断言的 `[-1]`
- 可选断言：`len(scene_texts) == scene_index + 1`（回退后不变量）；失败则停机并记诊断，不静默修错场

#### W1.4 REVIEW 回退对齐

**文件**：`chapter_review.py`

- `_rewind_script_after_review` 已有 index 截断；改为优先消费 `located_issues` / `scene_id`，index 仅作兼容回退
- Top-3 截断保留，但 **issue_id 全量写入 production**（`pending_repair_ticket`），模型 payload 可截断，验收不可丢 ID

#### W1.5 测试（不付费）

| 用例 | 期望 |
|---|---|
| 前场完全重复段落 | 修订目标 = 前场 scene_id，末场正文不变 |
| 中场重复 | 目标 = 中场；后续场截断 |
| 末场重复 | 目标 = 末场（唯一允许修末场的情况） |
| 跨场同一摘录双命中 | 回退最早场；ticket 含两个 scene_ids |
| 无法归属的硬失败 | 停机文案含「无法局部定位」，**不**进入 WRITE_SCENE |
| WRITE 时 index 与 texts 长度不一致 | 显式 ProductionStopped |

**出口**：四类反例单测全绿；无关已接受前场 hash 不变。

---

### W2 — P0 质量边界 + 修订验收 + 恢复闭环（约 2–3 天）

#### W2.1 删除关键词误放真实矛盾

**文件**：`directing_script_loop.py`（`_audit_cont_softable`）、必要时 `chapter_review.py` / `prose_front_gates.review_issues_may_coerce`

规则：

- Soft 白名单仅允许**结构化标签**（如已有 `[commons:]`、`[front:power_surface]`、显式 `soft-cont` 前缀），禁止用「入场」「连续」「重复」等子串判断
- 纯 `continuity_block` 且无 hard_fails：**默认不可 soft**（推翻当前 `return True`）；若需例外，必须带显式 `soft-cont` 与理由码
- `_coerce_review_soft`：写入 `review.verdict_original` + `review.accept_decision` + `review.coerce_reason`；**禁止只把 `passed` 改 True 覆盖来源**

#### W2.2 修订验收票据

**文件**：新建小模块或挂在 `directing_issue_ledger.py`；script 臂 VALIDATE 回写路径接入

票据字段最少：

- `issue_ids[]`、`scene_ids[]`、`base_content_hash`、`must_remove_quotes[]`、`required_state_refs[]`（仅已核事实）、`attempt`、`verify_rule`

验收：

1. 复跑同一确定性门 / 针对性检查：原 `issue_id` 应消失
2. 新硬错 → 记入 ledger，不静默忽略
3. 同 `issue_id` + 同 hash 无进展 → 停机（保留现「修复没有改变正文」语义，但附带「目标场 / ticket」诊断）
4. **优先**：script_scene 修订路径逐步改用已有 `prose_patch` 段落补丁（与 scene_loop 对齐），自然语言整场重写降为 fallback

#### W2.3 作品/章恢复闭环（连续创作 C02 的最小切片）

**文件**：`works_run_control.py`、`works_advance.py`、投影 `works_projection.py`

| 失败类 | resume 行为 |
|---|---|
| 预算暂停 | 保持现有 `authorize_budget` |
| `RETRYABLE_FAILED` / 暂时故障 | 清租约 → 可领取 |
| `TERMINAL_FAILED` + 可定位内容硬错 | **不**自动重开 attempt；返回 `blocker_code=content_hard_fail` + `recommended_action=repair_from_checkpoint \| regenerate` |
| `TERMINAL_FAILED` + 用户显式 regenerate | 新 attempt；旧版保留；费用累计不清零 |
| `RUNNING` + 无租约任务 | 补偿投影，禁止只显示「正在写」 |

最小 API/投影字段：`blocker_code`、`failed_phase`、`recoverability`、`recommended_actions[]`。

**测试**：FAILED 作品 resume 后存在可领取任务或明确 blocker；`TERMINAL_FAILED` 存在时 `start_run` 仍 Conflict；不跳章。

**出口**：注入真实矛盾必拦；合理回忆（显式 soft 标签）不误拦；修满不改变 severity；resume 不再出现「work=RUNNING 且 run=TERMINAL_FAILED 且 tick=None」无解释态。

---

### W3 — P1 连续运行与卷末（约 2–3 天）

直接采纳 9/17 计划 C01/C03/C04/C06 的设计要点，本处只定接口与验收，避免两份计划打架。

#### W3.1 作品级 continuation policy

- 字段：`enabled`、`target_chapter_no` 或 stop 条件、卷范围、预算授权引用、version
- **勿**复用章内 `auto_advance` 语义
- `ensure_next_run(work, completed_run)`：权威接受版本校验 → 暂停/预算/裁决/范围检查 → 锁内幂等创建 QUEUED → 完成事务内只写待办，不发模型

#### W3.2 成章衔接

- `_after_chapter_completed` 非末节点：写 continuation 请求或直接 `ensure_next_run`
- Worker 补偿扫描「已授权连续且缺后继」

#### W3.3 卷末

- `expansion_pending` → 持久决策或专属待办；前端可调用 expand / set_ending
- expand 幂等（目标 `volume_no`）；成功后清 pending、恢复 RUNNING、按 policy 开新卷首章

#### W3.4 调度公平（C04 最小）

- 屏障尽量下推 SQL；跳过候选用游标，不每次死盯同一 16 条
- 推进按 `run_id`，不只 `chapter_no`

**出口**（产品入口，非监督脚本）：

1. 一次启动授权三章，关页，Worker 完成三章
2. 双 Worker / 前台误点续写仍单一次后继
3. 失败章未修不得开后章
4. 卷末确认两次只扩一卷

---

### W4 — P1 跨章事实与工程治理（约 2 天 + 持续）

| 项 | 内容 |
|---|---|
| 跨章 | 正文证据绑定权威版本；旧 attempt 不贡献失效事实；承诺 OPEN/CLOSE 可追踪（9/17 C07） |
| 重复检索 | `find_similar` 仅候选；结合状态变化人工/规则裁决 |
| 调用键 | `call_key_version`；旧 checkpoint 兼容（C05） |
| 脚本治理 | `_tmp_supervise_*` 标注 supervised；产品验收入口分离 |
| 计划同步 | 本文件批次回填 Novel-Engine-Plan / PRD 验收条目（仅勾选已完成出口） |

---

## 4. PR 拆分建议

| PR | 范围 | 依赖 | 可单独合并 |
|---|---|---|---|
| PR-A | W0 证据 + 合成夹具 + 指纹 | 无 | 是 |
| PR-B | W1 定位契约 + VALIDATE/WRITE/REVIEW | PR-A 夹具 | 是（纯章内） |
| PR-C | W2.1–W2.2 质量边界 + 修订票据 | PR-B | 是 |
| PR-D | W2.3 恢复投影 | 可与 PR-C 并行 | 是 |
| PR-E | W3 连续运行 | PR-D 建议先合 | 是 |
| PR-F | W4 跨章 / 调用键 / 治理 | PR-B 稳定后 | 分批 |

每个 PR 必须带：**不付费单测** + 变更文件指纹说明；禁止「顺手扩大重试」。

---

## 5. 验收矩阵

### 5.1 工程（每 PR）

| 指标 | 门槛 |
|---|---|
| 前/中/末/跨场定位单测 | 100% 目标场正确 |
| 无法定位 | 100% 停机且无 WRITE_SCENE |
| 关键词 soft 回归 | 注入真实矛盾 0 误放 |
| resume 夹具 | 无「假 RUNNING」 |
| 连续三章（假模型） | 一次启动完成；无重复开章 |

### 5.2 真实模型（有预算上限，W1–W2 后）

| 项 | 要求 |
|---|---|
| 样本 | 优先回放 858a ch3 类题材；固定输入与协议版本 |
| 成功定义 | 硬事实过门 + 人工短评可读；**不是**仅 CANONIZED |
| 失败保留 | 全部 attempt、token、费用、ticket、hash 入证据包 |
| 对照 | 同输入下「旧末场回退」vs「定位回退」各 ≥N 次（N 在开跑前冻结，建议 ≥3） |

### 5.3 产品连续（W3 后）

- 禁止监督脚本开章；只用 Console/API 一次启动
- 记录人工干预次数（目标：功能验收轮 = 0）

### 5.4 发布门禁

同时满足才可声称「858a 类失败已修复」：

1. PR-B/C 单测绿，且定位对照显示末场默认路径不再被使用  
2. 至少一轮真实模型：原 `[front:redundant]` 类在定位修复后可消除或有界停在正确诊断  
3. resume 夹具绿  
4. （若宣称连续创作）W3 产品入口三章通过  

未满足则只可声称「定位缺口已修，历史 a4 因果仍为 B/C」。

---

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 定位误判导致错场仍在 | 无法高置信时升级停机，优于瞎修 |
| 多场回退成本上升 | 只截断最早场之后；保留已接受前场 |
| Soft 收紧导致通过率下降 | 预期行为；用结构化 soft 标签显式放行，不回退关键词 |
| 连续开章误授权 | policy 默认关闭；旧作品不自动获得无限章 |
| 调用键漂移重复计费 | W4 前置处理进行中的部署；新逻辑不复用异参旧输出 |

回滚：各 PR 功能开关可选 `repair_locate_v1`（默认 on）；关闭时恢复旧末场行为**仅用于紧急对照**，不得作为长期默认。

---

## 7. 建议立即执行顺序（落地 checklist）

> **2026-09-19 修订（实施前对齐代码现状）**  
> 1. **W1 不是新增契约**：`domain/repair_locate.py`、`LocatedIssueSpec`、`ScriptChapterValidation.located_issues`、`pending_repair_ticket`、`verify_repair_progress`、`_begin_located_scene_repair` 均已存在；WRITE_SCENE 已改为显式 `scene_texts[idx]` + 票据 `scene_id`/`scene_index` 校验。W1 真活 = **VALIDATE/ACCEPT 接线定位助手，删除默认 `last_i=len(texts)-1` 路径**。  
> 2. **WRITE 策略**：以现状为准采用 **显式 idx + 票据校验**（策略 B 已落地大半）；截断后续场由 `apply_repair_target_to_script_state` 保证。禁止再引入无断言的 `[-1]` 修订读写。  
> 3. **W0 必须纳入测试基线**：novel 定向套件已知红（调用键 `vrep`、恢复账本 FAILED/UNKNOWN）记入基线，不与 W1 回归混淆。  
> 4. **每个行为 PR**：单测 + `mutate_check`（退回默认末场 / 恢复关键词 soft → 目标测必须变红）。  
> 5. **§5.4 门禁不变**：W3 连续三章通过不能替代 W1/W2 的定位与质量边界验收。

1. [x] 用户确认本计划批次与 PR 拆分（含本修订）  
2. [x] W0：证据包字段清单 + 源码指纹 + novel 测试基线 → `deploy/novel/artifacts/r3/858a_fix_w0_baseline.md`  
3. [x] PR-B：VALIDATE/ACCEPT 接线 `_begin_located_scene_repair`；删除默认末场；AUDIT 显式 idx  
4. [x] PR-C：soft 边界收紧（结构化标签 only）+ `verify_repair_progress` 无进展停机 + coerce 保留 original  
5. [x] PR-D：`resume_work` 按失败路由返回 `WorkResumeOut`（blocker_code/推荐动作）；租约过期→UNKNOWN→对账耗尽 FAILED  
6. [ ] 小预算真实模型对照（定位 on/off）— 需线上/付费，离线未做  
7. [x] PR-E：`works_continuation.ensure_next_run` + 成章收尾挂钩 + worker 补偿；policy 默认关闭  
8. [x] W4（代码侧）：`call_key_version`（旧=1 无 vrep0，新默认=2）；计划回填本表  

**离线开发完成边界**：novel 定向套件全绿；产品入口三章与真实模型对照仍待部署验收（§5.4 门禁未满足前不得宣称「858a 类失败已修复」）。

---

## 8. 关键路径速查

- 契约：`core/src/regent/novel/application/directing_contracts.py`
- 分场环：`core/src/regent/novel/application/directing_script_loop.py`
- 审校：`core/src/regent/novel/application/chapter_review.py`
- 前置门：`core/src/regent/novel/domain/prose_front_gates.py`
- 段落补丁：`core/src/regent/novel/domain/prose_patch.py`
- 恢复：`core/src/regent/novel/application/works_run_control.py`
- 成章：`core/src/regent/novel/application/works_volumes.py`
- 领取：`core/src/regent/novel/application/works_runtime.py`
- a4/a5 包：`deploy/novel/artifacts/r3/858a_a4a5_2026-09-17/`
- 连续创作前案：`docs/archive/continuous-creation-repair-plan-2026-09-17.md`
