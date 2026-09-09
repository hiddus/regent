# 2026-09-10 编码复核与初始目标验收（v7.3）

结论：B-01～B-05 已完成并验收；B-04（真实双臂灰度）、B-06（真实成章入口并发认证）与 M2/R4/M3/M4 仍开放。导演已参与场景规划、人物调度、重演、正文取舍，并在本轮获得「长期创作约束（六类视图）、依赖真实重演、同预算质量验证、自然完结」四项生产级闭环。不能将测试通过解释为真实模型质量或生产并发安全已获证——后者仍属 B-06。

## 本轮证据

- 当前未提交工作树；本轮修改业务代码（B-01/B-02/B-03/B-05 落地），并新增迁移 `20260909_0055`。
- Python 定向回归：**196 passed in 26.01s**（`tests/unit/novel` + `tests/unit/test_novel_generation.py`，Python 3.13.12）。不是全仓测试，不含浏览器 E2E。
- 离线反例：`deploy/novel/mutate_check_b.py` 执行 **22/22 成立**，不调用模型、不连接服务器；每条反例构造旧行为并断言现实现给出不同结果，用于证明修复不是空壳。
- 迁移图单一 head：`20260909_0055`（链 0048→0049→0050→0051→0052→0053→0054→0055 连续，无多 head）。
- 新增迁移 `20260909_0055_ending_and_memory_views.py`：`novel_works.ending_target_volume`/`ending_statement`、`novel_memory_edges.edge_kind`、`novel_memory_items.resolved_chapter_no`/`basis`、`ck_novel_memory_kind` 扩展为 7 类（rule/character_arc/promise/relation/belief/reader_knowledge/director_note）。
- 本地 `novel_output` 的 23 份章节文件证明有产物，不构成预注册同预算人评、长篇分级认证或当前架构的完整可追踪实验。

回归命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/novel tests/unit/test_novel_generation.py -p no:cacheprovider -q
.venv/Scripts/python.exe deploy/novel/mutate_check_b.py
```

## 本轮修复（B-01～B-05）

### B-01 正式长期记忆：六类视图 + 内容指纹键 + 来源可查

位置：`domain/memory.py`（MEMORY_KINDS、item_key、classify_with_basis、extract_items、resolve_items、VIEW_OF_KIND/VIEWS/VISIBLE_TO/project_for/open_promises、CONFIDENCE_BY_BASIS）；`application/memory.py::record_chapter_memory/_keep_resolution`；`domain/models.py`。

- 7 类记忆齐备：世界规则(world_fact)、人物弧线/关系(character_state)、人物误信(character_belief)、读者认知(reader_cognition)、承诺与伏笔(promise_and_foreshadow)、导演记忆(director_memory)。
- 承诺/误信/导演记忆为 append-only，键带内容指纹（`item_key(kind, subject, content)` → `#digest(content)[:8]`）：同人两条承诺各成一条，不互相覆盖（反例 `B-01 同人的两条承诺各成一条` / `B-01 承诺键带内容指纹`）。
- 分类依据可查：显式 kind > marker > 内容信号 > 结构。`_RELATION_MARKERS`（反目/决裂/结盟…）命中为 `relation_signal`(high)，纯共现为 `co_occurrence`(medium)——两者置信度不同（反例 `B-01 共现推断与显式关系变化置信度不同`）。
- 六视图可见性边界：director_note 对 character/narrator/reader 均不可见；reader_knowledge 不给人物（反例 `B-01 导演记忆对 … 不可见` / `B-01 读者认知不给人物`）。
- 兑现章号独立存储（`resolve_items` 置 `resolved_chapter_no`），已兑现承诺不被后续重述误 reopen（`_keep_resolution`）。

验收：正式生成输出能表示规则/承诺/兑现与来源，跨章保留未兑现事项；六类视图投影与权限边界单测通过。覆盖 `test_long_term_memory.py`、`test_memory_from_real_facts.py`、`mutate_check_b.py`。

### B-02 依赖完整性：部分图不得宣称完整，未知时保守重做

位置：`domain/memory.py::coverage_of/replay_subgraph`；`application/memory.py::_link_dependencies/link_memory/plan_replay/plan_local_replay`、`works.py::report_fact`。

- `coverage_of` 区分 `depends` 与 `independent` 边：一条边都没有或没有显式 independent 标记的条目一律 `unknown`，不误报完整。
- `replay_subgraph` 在无覆盖记录（a/b/c 仅 a→b，c 与 a 未登记）时返回 `complete=False` 并列出 `unknown`（反例 `B-02 只有部分边时不得宣称完整` / `B-02 显式登记独立后才是完整图` / `B-02 无任何覆盖记录时全部算未知`）。
- `_link_dependencies` 对无上游的条目显式登记 `independent` 边（含首批），`link_memory(..., edge_kind=)` 落 `depends`/`independent`；`report_fact` 调用 `plan_local_replay` + `invalidate_changed` + 排重演运行（`_queue_replay_run`），从纠错入口实际调度最小场景重演，不只给记忆打失效标记。

验收：记录可验证的依赖覆盖与场景/产物关联；部分图不能宣称完整；从用户纠错入口实际重演正确范围。覆盖 `test_path_change_scoped_memory.py`、`mutate_check_b.py`。

### B-03 可信盲评：同预算带硬门 + 证据缺失 HOLD + 指纹重算

位置：`domain/evaluation.py::budget_violations/verdict/missing_evidence/Evidence`；`application/evaluation.py::build_report/_collect_evidence/_invalidate_if_reported`。

- `budget_violations` 三维度核对：单章成本、单场景成本、单场景时延 P95，上限为 0 表示未冻结。挑战方花 100、预算带上限 10 → HOLD，门槛 1000 也放不过（反例 `B-03 预算带上限 10、实际 100 不得晋级`）。预算带内但超单章成本门槛 → REJECT（反例 `B-03 预算带内且超门槛才 REJECT`）。
- `verdict` 优先级固定：① 配置漂移 → HOLD；② 证据缺失 → HOLD；③ 同预算带 → HOLD；④ 样本不足 → HOLD；⑤ 分项门槛/成本门槛 → REJECT；⑥ 是否超基线。
- `missing_evidence` 缺模型证据（不回填冻结配置中的模型名）、缺运行绑定（`runs_bound`）一律 HOLD（反例 `B-03 缺模型证据时不得放行` / `B-03 缺运行绑定时不得放行`）。
- `_invalidate_if_reported`：报告形成后样本/评分变化即作废报告指纹，保证来源一致（反例 `B-03 单场景时延超限也不晋级` 之外的漂移检测）。

验收：逐臂绑定真实运行/模型/正文/成本/同口径时延；执行冻结预算带；证据缺失 HOLD，超限不晋级。覆盖 `test_eval_and_canary.py`、`mutate_check_b.py`。

### B-05 自然完结：三态判定 + 扩卷失败不套模板

位置：`domain/ending.py`；`works.py::_after_chapter_completed/_decide_ending/_mark_ending_decision/set_ending_intent/resolve_ending/expand_next_volume/start_run`；`direction.py::EndingVerdict`；`generation.py::generate_ending_verdict`；`api/novel.py`（POST ending-intent、POST ending/resolve）。

- `decide_ending` 三态：用户设定卷数写完即 COMPLETE；导演判达成即 COMPLETE；导演未判定（None）为 UNDECIDED，**不是默认继续扩卷**（反例 `B-05 导演没有判定时是 undecided`）。判定依据可查（`BASIS_USER_VOLUME`/`BASIS_DIRECTOR`/`BASIS_NONE`）。
- `_after_chapter_completed`：末节点 → COMPLETE 写 `story.completed` 事件；EXPAND 调 `expand_next_volume`，模型/大纲失败不再套「变强/更强大的对手」静态模板，返回 None 并发 `volume.expansion_failed`；UNDECIDED 发 `ending.undecided`。
- `start_run` 在上一运行决策为 UNDECIDED 时抛 `Conflict`，禁止空章。
- 新增 `set_ending_intent` / `resolve_ending` 入口与 `EndingVerdict` 模型，导演判定经 `generate_ending_verdict` 进入决策。

验收：全书完成/继续条件明确，导演依据用户认可的终局选择结束或扩卷；正常生产入口自然 DONE 且不新增空章；扩卷失败保留失败状态与可恢复上下文。覆盖 `test_last_node_and_volume.py`、`mutate_check_b.py`。

## 仍开放的任务

### B-04 真实双臂灰度（未做）

`application/executor.py` 的 `KNOWN_EXECUTORS`/`STABLE_EXECUTOR` 仍仅 `director_v2`，生产 stable/canary 实际相同。选择与运行身份钉住机制已具备，但无两个不同策略、shadow 隔离与回退证据。验收须至少两个版本固定的策略，验证新作品分桶、shadow 产物隔离、运行中不混用与新运行回退。

### B-06 PostgreSQL 生产恢复证据（未做）

历史 16/16 日志应保留认可；但本轮未重跑绑定当前源码指纹的远程并发/崩溃测试，未对实际生成提交入口做故障注入。验收须逐文件记录全部源码与迁移 hash（含未跟踪文件），用真实入口做并发章提交、用户/默认裁决竞争、调用后崩溃、补账重试、租约接管。

### M2/R4/M3/M4 验收

浏览器旅程矩阵、真实 pilot 与三章同预算人评、M3 封测、M4 分档长篇认证均无完整通过材料；现有 23 份章节产物不能替代这些证据。

## 本轮验收的边界（诚实标注）

复核中发现两处「代码已实现但尚未在生产生效」的边界，不掩盖、也不夸大为缺陷：

1. **B-03 硬门无生产/pilot 入口。** `application/evaluation.py::build_report` 目前只被单元测试与历史审计脚本调用，`api/novel.py` 未暴露评估端点，Worker 也未调用。因此 BudgetBand/证据硬门是**库级就绪**：判定逻辑与反例齐备，但在真实评估流程中生效仍要等 R4 pilot 与三章同预算盲评（开放任务）接入。好消息是它按 fail-closed 设计——调用方漏传模型/成本/运行绑定只会得到 HOLD，不会因为没接线而错误放行。
2. **B-05 的「正常生产入口」是入口级单测，非真实模型运行。** `test_last_node_and_volume.py` 已不再把 `expand_next_volume` 替换为恒返回 None（v7.2 点名的原缺陷已消除），完结由 `ending_target_volume` 与 `EndingVerdict` 从 `advance_step→start_run` 正常判出；但模型输出仍由 stub 提供（单测必需）。真实模型下的终局行为仍属 B-06 范围。

这两点不推翻 B-01～B-05 的验收结论，但说明「验收通过」的语义边界：证明的是**机制与判定成立**，不是**真实环境与真实模型下的质量成立**。

## 初始目标与产品任务

初始目标：「AI 像导演拍电视剧一样持续组织创作并把控最终小说质量」。场景层控制权已建立；本轮补上长期意图（六类视图）、承诺兑现、依赖重演、同预算质量门禁与自然完结，整体目标中「持续把控质量」的机制已基本具备，但真实阅读质量与并发安全的证据仍缺，整体目标尚未获生产环境认证。

接下来完成 B-04/B-06 与 M2 浏览器旅程，随后执行 pilot、冻结配置、真实三章同预算盲评与 M3 封测。长篇分档验收仍待执行。

M-1 基线/用户研究、M2 浏览器矩阵、R4 人评、M3 封测、M4 长篇认证尚无完整通过材料。M-T 公共阅读与 M5 商业化是后续条件阶段，保持未启动，不混作本次导演架构必须立即实现的缺陷。
