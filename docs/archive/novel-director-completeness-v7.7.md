# 导演式生成复核 v7.7

结论：本轮修复有实质进展，但 C-01/C-03/C-05 尚不能整体关闭。原有导演创作控制权无需重做，需补齐当前主流程的调用连接与恢复约束。

## 验证范围

- 当前工作树定向回归：293 passed, 1 warning in 27.58s。范围为 tests/unit/novel、test_novel_generation、test_worker、unit/runtime、unit/model、tests/integration，以及 dependency_boundaries、cd6_sandbox_guards 两项架构测试。不是全仓/浏览器验收。
- novel-web TypeScript/Vite 构建通过。
- [隔离探测](novel-director-v7.7-probe.py) 已运行；内存 SQLite，不调用真实模型，不连接部署系统。
- 未重跑远程认证、迁移、人评。本轮仅更新审计材料和计划，保留业务代码。

## 已确认修复

C-02 的 80% 自动扩卷旁路已经移除。纠错恢复 API、前端恢复按钮、排队时保存纠错内容、具体承诺 resolves/歧义保护、非首批无交集记忆保持 unknown、终局请求附带实际产物与 CallBroker 留痕均已实现，相关定向测试通过。

## 仍存在的四个缺口

### D-01：纠错语义在正式装配时丢失（已复现，C-01）

works._queue_replay_run 已保存 correction/replay_reason，但 generation.assemble 重新赋值 generation_context，只通过 executor.carry_over 保留 executor 和延期切换字段，没有保留纠错信息。

隔离探测调用实际排队函数与 assemble：装配前有 ticket/statement/subject，装配后 correction 与 replay_reason 均不存在。当前测试停在排队上下文存在，不能证明导演下一次模型请求收到纠错。

验收：从 HTTP 报错/恢复，经后台 ASSEMBLE、DIRECT 到真实请求捕获，证明纠错内容仍在并影响后续核验；重复同一报错去重，不同报错不能因已有排队任务而静默丢失。

### D-02：排队顺序不等于按依赖完成（已复现，C-01/B-06）

advance_background_run 按 updated_at、chapter_no 排序，无父重演接受屏障。第一章推进一个检查点后更新时间变新，第二章仍为旧时间，下一次可能被先领取。

隔离探测：第一章重演 state=RUNNING、第二章 QUEUED，真实后台选择逻辑领取 chapter_no=2。仅排序排队章号不能证明拓扑执行；并发 Worker 还需同作品/依赖屏障。多个章节在旧父事实下启动，会产生旧上下文或提交冲突。

验收：第一章新版 Canon 接受前，依赖它的后章不得开始装配；验证暂停、失败、租约接管及双 Worker；无依赖的执行是否可并发须有明确证据。

### D-03：六类记忆投影接在旧流程，未贯通 director_v2（静态调用链，C-03）

generation._memory_view 的生产调用位于 perform/direct/weave。execute_step 对 director_v2 提前分派到 direction.plan_chapter/produce_tick/validate_chapter，不经过这些旧函数。direction 的 ACT/RENDER 仍使用自身的角色/Writer 上下文编译器，没有接新增记忆投影。

因此：章节规划能看到总记忆，不等于人物误信、读者认知和导演笔记已按目标协议贯穿场景。不能因为旧流程新增参数就勾选当前导演流程完成。

验收：通过 director_v2 的 ACT/WATCH/RENDER 实际模型请求验证逐角色记忆、误信来源与叙述可见性；不得将总记忆无差别塞给角色/Writer。保留当前已有信息隔离。

### D-04：终局调用有账本，但未启用预算上限（静态调用链，C-05）

generate_ending_verdict 使用 CallBroker(lease_owner=...)，未设置 budget_limit_minor。该字段默认 None，ledger.reserve 仅在 funding_limit_minor 非 None 时执行上限检查。场景调用传入剩余预算，终局调用没有同等约束。

验收：章/作品预算耗尽时在终局 provider 调用前拒绝；费用纳入同一额度体系，重复恢复复用结果。接入 broker 不能等同于预算硬门已启用。

## 原始目标与剩余验收

导演已经拥有场景构思、人物调度、重演与正文取舍权。当前不足集中在“改错真正到达导演”“依赖事实按序更新”“长期记忆进入当前场景流程”和“预算约束一致”。修复上述后仍需 B-04/B-06、M2 浏览器旅程、R4 真实同预算盲评、M4 长篇认证；不以定向测试数量代替质量结论。
