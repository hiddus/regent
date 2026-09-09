# 2026-09-09 编码复核与初始目标验收

结论：待办没有全部完成。导演已经参与场景规划、人物调度、重演、正文取舍；但持续创作的长期约束、自然完结、可信质量对照与用户旅程仍未完成验收。不能将测试通过解释为小说质量已提升。

## 本轮证据

- 当前未提交工作树；保留既有业务代码。本轮仅新增审计材料并更新计划。
- Python 定向回归：258 passed, 1 warning in 23.17s。范围为 tests/unit/novel、test_novel_generation、test_worker、unit/runtime、unit/model、tests/integration 及 dependency_boundaries、cd6_sandbox_guards 两项架构测试；不是全仓测试或浏览器 E2E。
- novel-web TypeScript/Vite 构建通过。
- 读取 pgverify-20260909.log，确认存在迁移往返与 16/16 记录；未重跑远程脚本或迁移。
- [离线反例](novel-progress-2026-09-09-probe.py) 已执行，不调用模型、不连接服务器。
- 本地 novel_output 存在 23 份章节文件；这证明有产物，不构成预注册同预算人评、长篇分级认证或当前架构的完整可追踪实验。

## 上轮问题复核

| 问题 | 本轮判断 |
|---|---|
| A-01 补账 attempt | 原消费键已修复，新增回归通过，可关闭该具体缺陷 |
| A-02 裁决传回导演 | 选择语义、等待状态、消费与 pending 清理已接入；用户/默认恢复入口测试通过，可关闭原缺陷。长期决定的持续约束仍需质量验收 |
| A-03 跨卷/终止 | v2 已调用成章收尾且动态卷边界测试通过；自然完结仍缺正常生产行为，不能整体关闭 |
| A-04 正式事实记忆 | statement 字段已兼容，但分类与兑现契约不完整，部分修复 |
| A-05 依赖与灰度 | 建图/失效/执行器钉住已接入，完整重演与真实双臂灰度未完成 |
| A-06 评估硬门 | 注册、计数、配置/报告指纹修复已存在；预算带执行与证据来源仍不足，不能整体关闭 |

## 仍需完成的代码任务

### B-01 长期记忆尚不能可靠表达承诺与规则

位置：domain/memory.py::classify、_subject_of、resolve_items；direction.py::VerifiedFact。

正式事实没有 memory_kind/promise/resolves 等字段。新增规则按在册人物数量分类：一人为弧线、两人为关系。离线探测“甲承诺明日归还钥匙”被记为 character_arc；正式输出仍无法提供显式规则/承诺/兑现标记。同一人物的弧线键也会覆盖先前同类内容。人物共现不等于关系变化，知道某件事也不等于参与该关系。

验收：正式生成输出能表示规则、承诺、兑现和来源，跨章保留未兑现事项；补齐技术方案 §3 的六类视图，尤其人物误信、读者认知与导演记忆。不能只增加人工标签测试。

### B-02 有边不代表依赖完整，失效不等于执行重演

位置：application/memory.py::_link_dependencies、plan_replay、invalidate_changed；domain/memory.py::replay_subgraph；works.py::update_critical_path。

空图已保守回退，但非空图仍只有“边端点存在”检查，没有漏边检测或完整性来源。探测三项 a/b/c、仅 a→b 时返回 complete=True；系统无法区分 c 独立与漏记 b→c。共享实体自动建边也不能证明因果依赖完整。路径入口使用结果给记忆打失效标记，未据 ReplayPlan.chapters/keys 调度最小场景重演。

验收：记录可验证的依赖覆盖与场景/产物关联；部分图不能宣称完整；从用户纠错入口实际重演正确范围，未覆盖时保守重做，保护已接受事实。

### B-03 盲评预算带仍可绕过

位置：domain/evaluation.py::verdict、missing_evidence；application/evaluation.py::_collect_evidence。

离线探测 BudgetBand 单章上限 10、挑战方成本 100、独立晋级上限 1000，得到 PROMOTE。判定只检查 PromotionThresholds 的成本上限，未落实 BudgetBand 的成本/时延上限。模型证据未传时还会回填冻结配置中的模型名称；样本正文引用只检查至少一项，不能证明每臂正文与实际模型一致。

验收：逐臂绑定真实运行、模型、正文、成本及同口径时延；执行冻结预算带约束；证据缺失 HOLD，超限不晋级。报告形成后样本/评分变化应作废或重新生成报告，保证来源一致。

### B-04 灰度接线只有一个可选实现

位置：application/executor.py::KNOWN_EXECUTORS、STABLE_EXECUTOR。

两者均只包含 director_v2，因此生产允许的 stable/canary 实际相同。已具备选择和运行身份钉住机制，但不能证明两个不同策略、shadow 隔离或回退。无需恢复旧审核式导演才能解决：可以比较保留导演控制权的两个版本/调度策略。

验收：至少两个真实可执行且版本固定的策略，验证新作品分桶、shadow 产物隔离、运行中不混用与新运行回退。

### B-05 自然完结仍依赖测试替身

位置：works.py::_after_chapter_completed、expand_next_volume；tests/unit/novel/test_last_node_and_volume.py。

末节点完成后先调用 expand_next_volume；有正常路径时可继续造节点，模型异常则使用“变强／更强大的对手”静态模板。没有先依据用户终局意图决定结束的分支。结束测试将 expand_next_volume 替换为恒返回 None，因此只证明“无扩展时能置 DONE”，没有证明正常故事会结束。静态模板还可能偏离题材与用户方向。

验收：明确全书完成/继续条件，导演依据用户认可的终局选择结束或扩卷；正常生产入口自然 DONE 且不新增空章。扩卷失败须保留失败状态及可恢复上下文，不静默改写创作方向。

### B-06 PostgreSQL 证据覆盖不足

16/16 日志存在，应保留认可。但 pg_verify.py 的源码摘要使用 git diff HEAD，不包含大量未跟踪的新业务模块；仅记录 dirty 文件数量不能固定其内容。新增 commit_chapter 测试自行加锁并直接写 CanonCommitModel，未调用 generation.canon/advance_step，不能证明生产父版本冲突、章与事实原子提交均通过。

验收：对实际发送/运行的全部源码和迁移逐文件记录 hash（包括未跟踪文件）；使用真实生成提交入口做故障和并发测试。历史迁移往返只覆盖降至 0052，保持该范围表述。

## 初始目标与产品任务

初始目标是“AI 像导演拍电视剧一样持续组织创作并把控最终小说质量”。场景层控制权已经建立；长期意图、承诺兑现、终局与真实阅读质量证据仍缺，整体目标未完成。

接下来先完成 B-05/B-01/B-02/B-03，再完善 B-04/B-06 和 M2 浏览器旅程，随后执行 pilot、冻结配置、真实三章同预算盲评与 M3 封测。长篇分档验收仍待执行。

M-1 的基线/用户研究、M2 浏览器矩阵、R4 人评、M3 封测、M4 长篇认证尚无完整通过材料。M-T 公共阅读与 M5 商业化是后续条件阶段，保持未启动，不把它们混作本次导演架构必须立即实现的缺陷。
