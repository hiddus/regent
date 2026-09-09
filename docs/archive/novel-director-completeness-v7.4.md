# 导演式小说生成完备性复核（v7.4）

结论：尚未完备。场景导演的创作控制权已经建立，新增修复有实质进展；但纠错执行、终局约束和长期记忆仍存在生产流程缺口。真实阅读质量提升也尚无完整人评证据。此结论针对当前未提交工作树，不以文档勾选或 helper 测试代替行为。

## 本轮验证

- Python 定向回归：277 passed, 1 warning in 39.96s。范围：tests/unit/novel、test_novel_generation、test_worker、unit/runtime、unit/model、tests/integration，以及 dependency_boundaries、cd6_sandbox_guards 两项架构测试。不是全仓或浏览器测试。
- novel-web TypeScript/Vite 构建通过。
- migration graph 单一 head：20260909_0055；未运行数据库升级或远程认证。
- [离线探测](novel-director-completeness-probe.py) 使用内存 SQLite、正式模型字段和应用入口，已运行；没有真实模型调用或外部写入。
- 本轮仅更新审计材料和计划，不修改业务实现。

## 已确认的改善

终局 complete/expand/undecided 判定、用户终局 API、失败事件、记忆内容指纹键、分类依据、兑现章号、依赖覆盖记录、纠错任务排队和评估预算约束均已有新增实现，回归通过。上轮具体反例的修复不应被抹去，但不代表对应完整旅程已验收。

## C-01：纠错任务未携带纠错内容，已完结作品无人领取（已复现）

位置：works.py::report_fact、_queue_replay_run、advance_background_run。

report_fact 将 statement 写入事件，但新 attempt 的 generation_context 只有 architecture_version，没有纠错内容、ticket 或待核对的事实引用；未找到生成流程读取 fact.reported 的路径。后台只领取作品 state=RUNNING 的任务，而排队不改变作品状态。

探测结果：DONE 作品报错返回 accepted=true；新 attempt 为 QUEUED，作品仍 DONE，后台推进返回 None；新任务上下文不含纠错句。暂停/等待作品同样需要明确恢复策略，不能承诺已自动纠正。

验收：通过正式报错→后台领取→模型请求→核验→接受新版全程，证明纠错语义到达导演；对 RUNNING/DONE/PAUSED 等状态定义清晰的调度规则，保持用户暂停意图；重复报错不无限创建任务。依赖章节应按父事实版本顺序重演，而不是仅批量排队。

## C-02：提前扩卷入口绕过终局意图（已复现）

位置：works.py::_after_chapter_completed、_maybe_expand_volume。

末节点入口已有新判定，但非末节点仍调用按预计章数达到 80% 的旧入口，该入口直接 expand_next_volume，不检查 ending_target_volume 或终局判定。

隔离探测：用户限定 1 卷，当前第 1 卷预计 10 章、已完成 8 章，仍调用扩卷一次。这是对入口调用的复现，没有调用实际模型。提前扩卷和实际卷完成必须分开，不能提前改变活动卷导致剩余节点被跳过。

验收：80% 预规划、末节点完成、手动恢复等所有入口共享用户终局约束；用户限定一卷不得新建第二卷。动态章数超出/不足预计值仍完成当前卷全部节点。

## C-03：六类视图尚未贯通角色上下文，正式兑现链仍不完整

位置：domain/memory.py::project_for、resolve_items；direction.py::VerifiedFact；generation.py::canon。

project_for 只见定义/测试，没有生产调用；按 character 受众过滤也不带具体角色身份，单凭该函数不能实现“人物只知自己知道的事”。当前召回接到章节总上下文，但新增视图不能据此视为角色/叙述投影已接通。导演笔记仍依赖事实文字分类，未从导演创作决策、失败原因直接建立长期来源。

正式 VerifiedFact 新增了 memory_kind/subject，但没有 resolves/payoff/closes。探测“甲承诺归还钥匙”后输入正式事实“甲把钥匙还给乙，兑现承诺”，原承诺仍 OPEN。路径节点完成可以通过专用 resolves 关闭路径承诺，不能覆盖正文中任意承诺。按 subject 兑现还可能同时关闭同人多条承诺，须按具体承诺标识核验。

验收：正式生成结果能关联具体承诺并记录兑现；两条承诺只兑现一条时另一条保留。跨章实际人物/Writer 请求分别验证误信、已知信息、读者揭示边界；导演记忆来自真实创作过程，不能要求正文出现“导演失败原因”才能抽取。

## C-04：依赖覆盖标记仍由启发式自动推断（静态证据）

位置：application/memory.py::_link_dependencies。

首批所有条目自动标为 independent；后续不共享实体/主体也自动标 independent。这样可以区分“没有落覆盖记录”，却不能区分“语义依赖未被关键词识别”。同章因果关系或跨实体因果关系仍可能被自动认证为独立。现有漏记录测试只验证删除标记，未验证错误独立标记。

验收：以冻结的实际创作输入/产物依赖建立覆盖依据；启发式无命中应保持 unknown，除非有独立性证据。覆盖同章依赖、跨实体因果和遗漏一条真实依赖的反例；未知时不能宣布最小重演范围可信。

## C-05：导演终局判断缺实际结果证据及统一调用保障（静态证据）

位置：works.py::_director_ending_verdict；generation.py::generate_ending_verdict。

传入 completed_nodes 的内容来自作品路径节点 title/promise，查询未限制最新路径；请求没有最终正文、已核验事实、未兑现承诺。提示要求引用“已经写出的具体内容”，模型实际只能看到计划性信息。函数还直接调用 provider.generate_structured，未通过场景流程的 CallBroker，不能沿用其预算、幂等复用和崩溃恢复承诺。

验收：使用当前分支/路径的已接受章节、核验事实、未收承诺及用户终局意图，区分计划与实际完成；对终局模型调用接入同等预算/持久化/幂等恢复约束，故障重试不重复计费或修改创作方向。

## 仍开放的验收

B-04 实际双策略灰度、B-06 真实生产入口 PostgreSQL 认证，计划本身仍未关闭。0055 未获本轮迁移执行验证。评估 build_report 仍属库级能力；真实 pilot、同预算三章人评、浏览器旅程和长篇分级认证尚不能判通过。

当前可以表述为“导演式核心流程已建立，正在补齐持续创作与恢复闭环”；不能表述为“导演式小说生成已完备”或“质量提升已证明”。下一轮应优先完成 C-01/C-02，再贯通 C-03/C-04/C-05，最后以真实请求与产物验收，而非继续以新增字段/函数或排队成功关闭任务。
