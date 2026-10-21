# 长篇小说服务器失败复盘与修订方案

日期：2026-10-11。核查对象：本地 HEAD eba8990 及已有工作区修改；服务器导出 b0ce_runs.json、b0ce_texts.txt、b0ce_digest.txt。本次未连接服务器核验部署版本，未修改业务代码，未重新调用模型。服务器产物和本地当前代码分别作为运行证据与实现证据，不能假定二者完全一致。

## 判断

这次主要失败发生在章内：第一场写穿后续场，后续场按原计划重演，场记未阻止事实冲突进入后续场，最终审校才发现整章互相矛盾。增加成稿编辑不能解决主要问题。

建议保留现有角色，先落实场景边界、结构化道具/资源状态、正史提交硬门。但三项必须连同版本绑定、回退恢复、状态证据校验一起实现；否则会出现“表格结构正确、表中事实仍然错误”，或程序拦住问题却永远修不通。

不增加角色、不增加默认模型调用可作为实现约束；不能承诺修订期间总调用数一定不增加。错误被提前拦住可能触发重写，应以完整尝试的成本和成功率评估。

## 已核实的运行证据

- 第2章第一场实际写了读取齿轮、碎裂、发现棋子“纪”字、收纸条、出门；第二场再次读取并碎裂，第三场再次出门。不是单纯跨章衔接失败。
- 用 SequenceMatcher 对第1章三场拼接文本与第2章第一场进行字符比较，最长完全一致连续片段为245字，另外有58、55、44、29字片段。用户指出的尾段存在插入文字，因此不能把整个尾段都描述为完全逐字相同；长段照抄成立。
- 第2章 ctx.canon 共18条，按完整 JSON 内容去重为9条。能证明输入重复，尚不能仅据导出判定是重复提交、收集重复还是上下文装配重复，应追踪 commit_id/source_hash/accepted take。
- 第1章 gate_debts 中留下滴答声“停了”与“没停过”的矛盾；最后 review_history 记录仍 passed=false，但 run 已 CANONIZED。与当前代码的递延债务强制通过路径吻合；最终确认服务器所走分支仍需部署版本及运行日志。
- 该导出中第1章 call_count=63，第2章 digest 记70。不要把“每章65–70次”当作精确恒定值。
- 第2章 previous_handoff.tail_excerpt 是800字符；本地 TAIL_CHARS 也是800。交接入口还拼入最多800字符自由状态，且 writer context 携带 recent_chapters 等材料。“约1700字”不等同于全部是尾段正文；上下文确实存在多处长正文暴露。

## 当前代码里的关键路径

### 1. 真实连续性矛盾会因递延标签失去阻断作用

directing_script_loop._audit_cont_deferrable：没有缺失 must 节拍、没有模型 hard_fails，但 continuity_block=true，可以递延。AUDIT_SCENE 修满后调用 _append_gate_debt，再执行 next_state.update(audit.state_changes)，进入下一场。

prose_front_gates.review_issues_may_coerce 明确对 [gate_debt:cont] continue；chapter_review 的创作通过/修满分支调用 _coerce_review_soft。另有“至少修过一轮、纯递延债务、无 redundant”放行分支。

这不是只缺一次编辑，而是问题的严重性被标签路径改变。应将 defect_code/severity/status 分开：颜色冲突、消耗冲突、已碎物再用、事件重演为硬问题，递延仅改变处理时点，不改变严重性。必须显式复验关闭，不能靠修满关闭。

### 2. 状态只有自由键值，没有可验证变更协议

AUDIT_SCENE 给场记 working_state 和正文，但实际变化最终通过字典 update 合并。状态记录缺少统一物件身份、固定字段、变更前值、正文范围和资源消耗关联。

SceneDeltas.state_changes 虽描述为“实体.属性→新值”，仍是 dict[str,str]，没有构成程序可执行约束。实际审计记录已经显示场记把未来计划、未发生动作写进状态。

### 3. 自审与稿件版本未绑定

_compile_self_issues 看到 compile_self_review_done 就返回空问题；第一次自审失败也会设置此标志。writer_self_reviewed:scene_id 与 auditor_self_reviewed:scene_id 同样按场号存在性跳过，新稿/修订记录不等于已复验新版本。

不能简单让每次修订再增加一轮模型自审。应先用程序处理摘录、字段和规则；保留未解决问题，绑定 plan_hash/prose_hash/audit_hash，在既有下游场记或审校调用中复验语义问题。版本失效不代表必须新增一个模型角色。

### 4. 当前已有 COMPILE 回退，但并不代表修复闭环成立

相较10月1日报告，本地 chapter_review 已有 _rewind_script_to_compile，并在 _rewind_script_after_review 中调用 review_repair_layer 分流。因此不能继续断言“完全没有计划层分流”。

仍有两点风险：

- classify_review_fault 依赖自然语言关键词；任一问题包含前章标记时，review_repair_layer 优先判 prior_chapter。必须区分“当前正文违背前章事实”和“前章本身自相矛盾”，不能见到“前章事实冲突”就认定前章应重做。
- _rewind_script_to_compile 清场文、场审、状态轨迹等，但该函数中没有把 sp.working_state/production.working_state 显式恢复到本章入口。后续 COMPILE 初始化又优先取 production.working_state，存在把本章失败出口作为新入口的风险。应补一个不可变 chapter_entry_state，并在所有回退路径统一恢复、清理后续衍生记录。

## 三项改动的可执行规格

### A. 场景边界：约束正文实际发生了什么

分场卡必须明确：本场允许兑现的事件、仅可引用的事件、后场才能发生的事件、入口状态、预期出口、禁止的状态变化。

现有 script_events/event_refs 约束的是计划。正文仍可在第一场提前兑现所有事件。让现有场记在原调用里额外返回 actual_events（含正文证据和“发生/回忆/计划/否定”等模态），程序校验实际发生事件是否属于本场。第一场发生 read_gear/destroy_gear/notice_chess_mark/leave_shop 就当场拦，不能等到后场再修。

注意：程序无法只凭编号识别自然语言中所有同义动作。模型提取可能漏事件；补充可确定的物件状态变更、资源扣除、人物位置变化作为交叉校验。事件清单应允许新增背景细节，但不能偷偷新增重大状态变化。

增加场后正文去重：与前章、已接受前场比较长连续片段及近似重复段，输出新旧位置和 scene_id；短对白、固定铭文、正式引用豁免。阈值通过本次245字复用和正常样本校准，不凭空设成文学标准。合法重读笔记不等于再次发现笔记；文字复述不等于再次发生读取。

writer 不传整个 ordered_ctx：建立白名单，只保留本场卡、相关台账、必要人物动机/记忆和前章最后少量句子。同时移除 recent_chapters.content、previous_handoff 大尾段、旧稿等隐藏重复入口；审校和程序比较仍可读取全文。

### B. 场记状态：固定身份台账与带证据的增量

台账是现有 working_state 的类型化投影，不另建一套互不一致的事实真相。不是只有道具：应包含人物位置、时间、资源和能力规则。

建议字段：

| 对象 | 固定信息/状态 |
|---|---|
| prop_id | 规范名、别名、部件/容器关系、固定属性、文字内容 |
| 位置/持有 | location_id、holder_id、container_id、摆放方向（已明确时） |
| 生命周期 | intact/broken/destroyed、read_status、read_count、是否可重复读 |
| 一次性事件 | event_key、主体、对象、是否发生、证据、认知者 |
| 资源 | resource_id、已消耗条目、cause_event_id、规则及适用条件 |
| 人物/场景 | actor_id、位置、在场者、时间标记、已知事实 |

“笔记7本”应区分集合与具体笔记/页面；“齿轮”和“齿轮后盖”必须明确是否同一物、部件关系，不可靠名字相近合并。固定文字也应定位到具体页面/补注区域；另一页新发现文字是新增条目，不覆盖旧文。

场记输出增量：entity_id、operation、field、before、after、event_id、evidence_quote、source_scene_id、source_prose_hash。程序确认摘录存在并定位到正文；证据存在只能证明接地，不能自动证明字段理解正确，仍由现有场记承担提取判断。

检查规则：

- 不允许未知 ID、擅改固定属性、无 before 的覆盖、无证据的状态确认。
- 已销毁物不能再次使用；损坏但可用/经维修恢复需要明确规则与修复事件。
- 可重读纸面文字，但一次性触忆不能重读；read_text/read_memory 必须不同动作。
- 每次有成本的能力事件与资源扣除关联；重复扣同一记忆需拦。是否同义不能靠“修表第一课”字符串，应共享 resource_id。
- “不消耗亲忆”与“扣除亲忆”不能出现在同一能力事件上；例外必须有已批准规则。
- 只在场次所有硬门通过后应用增量；不通过时状态保持不变。正文修订使旧增量立即失效。
- unknown 表示未确认，不得靠猜测补颜色、持有人、时刻。模糊化不得把“已碎”重置成“未知”。

COMPILE 开拍前按场序模拟计划增量，在这里就拦“已读再读”“读完不扣/又扣两次”“未返回就重新在室内”。场后再检查实际增量，防止计划正确、正文越界。

### C. 正史提交：硬问题零未结、幂等且去重

将最终 gate 放在共享最终接纳入口，并在 CANON 提交前再次检查。不能只相信 run.review.passed。

必要条件：

1. 所有硬问题已用最终版本证据复验关闭；repair_count、creative_accept 不改变事实严重性。
2. 最终章节正文、场次清单、场记证据、台账快照和审核结果版本一致。
3. accepted 下标唯一、有效，take 非 SUPERSEDED；collect_accepted_facts 不重复收同一 take。
4. 事实精确重复确定性去重；不能只按 subject 去重，因为同一主体有不同时间状态。
5. Canon 提交幂等键/约束阻止重复运行重复写入；上下文也按有效提交去重，避免重复消耗有限召回额度。
6. 正史、台账出口、交接和长期记忆使用一致来源版本，在事务边界内保持一致。

本次旧第1章不能直接当干净基线。应保留失败证据，隔离带矛盾分支，修复/重生第1章再从新分支测试。旧章修订后派生的后章与记忆不能继续当有效记录；不要直接删除审计证据。

## 修稿策略必须同时落地

每个问题保存 code、severity、origin_scene_id、affected_scene_ids、evidence_spans、expected_constraint、status、version。

- 第一场越界：重写第一场并使后场、状态增量、摘要、take、审核标记失效；从第一场入口快照重放。
- 计划自己矛盾：回 COMPILE，恢复 chapter_entry_state，不能携带失败出口。
- 无法定位：返回计划层重新验证/组织；不能默认重写最后一场。若证据说明前章本身矛盾，则先隔离前章。
- 摘录格式问题：程序规范化/定位处理；不得占用唯一一次文学修稿额度。规范化要保留原文位置，不抹平改变事实的否定或数字差异。
- 已发现问题不因第二稿跳过自审而自动关闭；用现有场记/审校复验改后的正文及状态。

## 阶段与验收

第一批：三项硬门 + 白名单上下文 + 入口快照/版本失效 + 最早故障回退 + 自审问题保留。人名规范化属于台账ID落地的必要部分：在开篇锁定人物/店铺ID，将大纲、正文名、长期承诺映射到同一ID。不能让不同名靠审校临时降为 soft 继续运行。

第二批：全书长期事实召回、跨章事件身份、证据驱动故事段完成、统一钩子关闭、滚动故事段规划。

第三批：章级文学编辑与节奏控制，在事实稳定基础上验证读者体验。

先用 b0ce 作为离线回放集，验证无需新增模型调用即可重现并拦截：第一场写穿、245字复用、齿轮重复读取/碎裂、文字变更、消耗冲突、声响矛盾、18→9精确去重、第二稿仍有未解决问题、回退清空污染状态。正常对照必须允许重读笔记、引用旧铭文、修复后再使用道具、自然时间跳跃。

随后服务器在干净分支重跑三章，记录部署 commit、提示/协议版本、配置、run_id、完整中间产物；不能通过手改库或关闭硬门取得通过。

验收同时看：完成率、硬矛盾零未结、重复发生/长段照抄被拦、合法重读不误杀、状态一致、最终版本证据一致、重试成本。只停机更早不叫稳定输出。先测多个种子/题材的三章，再测一个完整故事段（例如8–12章），再进行超过近期记忆窗口的召回及完整卷验证。

当前不能给出“改完即可稳定长篇”的保证。距离长文稳定，首先差一个可靠的章内状态与事件执行闭环；随后才是长期记忆、故事推进和文学质量。三章是这一轮的冒烟验证，不是长篇能力认证。

## 证据来源

- deploy/novel/artifacts/r3/b0ce_runs.json
- deploy/novel/artifacts/r3/b0ce_texts.txt
- deploy/novel/artifacts/r3/b0ce_digest.txt
- core/src/regent/novel/application/directing_script_loop.py
- core/src/regent/novel/application/chapter_review.py
- core/src/regent/novel/application/chapter_accept.py
- core/src/regent/novel/application/generation.py
- core/src/regent/novel/domain/chapter_handoff.py
- core/src/regent/novel/domain/prose_front_gates.py
- core/src/regent/novel/domain/scene_card.py
- core/src/regent/novel/domain/script_events.py
