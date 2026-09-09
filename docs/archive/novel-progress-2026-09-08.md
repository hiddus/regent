# Novel Engine 编码进度复核 — 2026-09-08

## 范围与证据等级

对当前未提交工作树做只读代码核验、Python 3.12.14 定向回归、前端构建、迁移图检查和隔离 SQLite/纯函数探测。没有调用真实生成模型，没有连接或修改部署服务器，没有执行生产数据库迁移。

HEAD 为 `b3d5818`。本次结论依赖未提交文件，不能仅由 HEAD 重现。受审源码指纹见 [manifest](novel-progress-2026-09-08-manifest.json)；补充探测见 [probe](novel-progress-2026-09-08-probe.py)。

- **测试证据**：190 passed in 19.84s；测试范围如下，不能表述为全仓测试通过。
- **构建证据**：novel-web 的 TypeScript/Vite build 通过。
- **迁移图证据**：单一 head `20260908_0054`；本次未执行 upgrade/downgrade。
- **历史记录**：前序计划记载独立 PostgreSQL 临时库 11/11 认证及迁移往返。本次仅确认存在 `deploy/novel/pg_verify.py`，未重新运行，未找到绑定当前源码指纹的原始认证结果文件；不推翻历史执行记录，也不将它升级为当前工作树全量认证。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/novel tests/unit/test_novel_generation.py tests/unit/test_worker.py tests/unit/runtime tests/unit/model tests/architecture/test_dependency_boundaries.py tests/architecture/test_cd6_sandbox_guards.py -o addopts= --basetemp=C:/regent/.pytest_tmp/progress-audit-0908 -q
# 在 apps/novel-web 中执行 npm.cmd run build
.venv/Scripts/python.exe -m alembic heads
.venv/Scripts/python.exe docs/archive/novel-progress-2026-09-08-probe.py
```

旧定义冻结测试未纳入通过集合；真实模型质量、移动端/网络/无障碍 E2E、供应商对账及生产并发不由上述测试证明。

## 已确认的新增实现

1. `production.py`/`ledger.py`：UNKNOWN 恢复、独立事务结算、对账计数、按尝试的常规结算、预留上限与作品锁；Worker 已接周期恢复。
2. `runtime.py`/`commands.py`/`context.py`：导演命令、版本、产物及场景绑定；完整上下文 manifest。`hive.py` 与 `run_batch()` 已接角色批量执行。
3. `works.py`：调用窗口后的租约/版本回查、指导与路径改动递增 input_version；裁决创建、条件更新与默认期限扫描。
4. 场景创作、导演重演/改写、独立核验、多场景组章、后缀修复、章节与 Canon 同事务接受继续存在且有回归覆盖。
5. 长期记忆与评估新增领域函数、应用服务及 0053/0054 表；这些不再是“未找到实现”，但尚不构成对应里程碑完成。

## 复核发现

### A-01 补账路径仍缺 attempt 幂等维度（已复现）

位置：`application/production.py::_top_up`、`application/ledger.py::consume`。

预留键含 attempt，但 consume 的 logical_call_id 仍为 `logical_call_id:topup`。两个 attempt 分别补记 3 和 4 个测试整数单位时：应消费 7，实际仅 3；第二笔 amount=4、settled=0、status=RESERVED。常规 `_finalize()` 的 attempt 修复不能覆盖这个分支。

因此 P0-2 重新打开。须验证正常成功、供应商查回和 UNKNOWN 重试各路径连续两次超预留，费用与每笔预留均守恒。

### A-02 裁决留痕存在，选择尚未传入恢复后的导演观看（静态调用链证据）

位置：`works.py::_apply_decision`、`direction.py::produce_tick`、`context.py::compile_director_performance_context`。

选择只追加到 `generation_context.decision_resolutions`，记录 option_id 而没有所选选项的完整创作语义；WATCH_TAKE/WATCH_PROSE 的上下文不读取该字段。`plan_chapter()` 在已有 plan 时直接返回，因此不能依靠重新规划自动消费选择。`production.pending_decision` 也未找到消费/清除路径。另有 `advance_step()` 对 incomplete 统一写 RUNNING，覆盖 create_decision 写入的章节 PENDING_DECISION；作品仍等待，但章/作品投影不一致。

因此 P1-2 为“请求/结算服务已实现，导演生成闭环未验收”。须以导演实际发起两种互斥选择为入口，证明恢复后的模型请求含所选语义、不会重问、不会先执行未选分支。

### A-03 末节点完成/跨卷逻辑没有闭合到 v2 成章分支（静态调用链证据）

位置：`works.py::advance_step/start_run/_maybe_expand_volume`、`generation.py::assemble`。

`_maybe_expand_volume()` 的调用位于 pending=None 分支；director_v2 在 CANON 步骤提前完成并返回，后续对 CANONIZED 的推进也会提前返回，因此未走该扩卷调用。`story_complete` 由下一章 ASSEMBLE 发现前章末节点完成后才设置，`start_run` 却只检查上一章既存的 story_complete，可能多创建一章没有目标节点的任务。现有测试直接调用 helper，或手工设置 story_complete，未覆盖该实际时序。

此外，卷定位仍依赖预计 chapter range，在动态节点章数下需要验证边界漂移。P1-3 重新打开，要求从真实 v2 最后一个节点生成至 CANON，再从 start_run 进入下一步的集成测试。

### A-04 新记忆服务与正式事实格式不兼容（已复现）

位置：`direction.py::VerifiedFact`、`domain/memory.py::classify/_content_of`、`generation.py::canon`。

正常 VerifiedFact 只有 statement/quote/known_by/entities，没有 memory_kind 等分类标记；分类器不会识别。即使探测补入 memory_kind=rule 与 subject，正文读取器也不读取 statement，结果仍为 0 条记忆。现有记忆测试使用人工构造的另一套字段，不能证明正常章节会写入记忆。

R3 应标为“存储/规则和召回接线已实现，真实事实到记忆未闭合”。须用正式验证输出生成至少一项规则/弧线/承诺/关系，下一章实际召回并验证来源。

### A-05 依赖图重演与灰度函数尚未成为运行流程（静态搜索证据）

`link_memory()`/`plan_replay()` 未找到生产调用方，不能宣称真实依赖图已经建立或用于重演。`replay_subgraph()` 只发现指向未知条目的边，不能检测漏记的边；空图也可能返回 complete=True。`invalidate_memory()` 按类别整批失效，不是按改动路径的依赖范围失效。

`assign_executor()`/`switch_executor_allowed()` 未找到 start_run 等生产调用方；start_run 仍直接选择 director_v2。灰度是函数骨架而非可运行灰度。完整六类记忆中读者认知、误信、导演记忆的长期投影也不能以四类 MemoryItem 替代。

### A-06 评估可错误晋级（已复现）

位置：`application/evaluation.py::record_scores/build_report/decide`、`domain/evaluation.py::summarise/verdict`。

探测注册样本数=0，仅一个 sample_id、每个 arm 10 名评者：汇总 n=10，min_samples=10 即被满足，结果为 PROMOTE。没有提供成本数据时，报告成本默认为 0；样本构成、模型/预算带和时延没有据实核验。冻结后直接改变 config.thresholds，再运行 decide，仍得到 PROMOTE，因为没有按当前配置重算指纹，只比较原先保存的两个字段。

盲评样本落库只保留 A/B 载荷，不保存 arm 映射及正文引用，评分服务却接受任意 arm/rater/sample_id；完整盲评收分链尚待接入。R4 必须保持 HOLD，不得将“只差人工评分”作为当前状态。

## 计划处理

保留已实现和通过回归的工作；重新打开 P0-2、P1-2、P1-3，R3/R4 记作部分实现。P0-5 区分历史认证与本轮证据。下一批首先修复 A-01，再闭合 A-02/A-03，然后接通 A-04/A-05，并在采集真实人评前修复 A-06。新验收应从生产入口贯穿数据库/模型请求/状态投影，不能只重复 helper 的单元断言。

此次复核仅更新计划与审计材料，没有修复上述业务代码，也没有修改已部署系统。

---

## 2026-09-09 修复批次（A-01～A-06 业务代码落地）

上文的业务代码问题已逐个修复并通过反证。以下按缺陷编号记录**改了什么、为什么这样改、凭什么认为修好了**。

### A-01 补账幂等（已完成）

`production._top_up` 的 `logical_call_id` 原来不含 attempt，跨 attempt 补账会被幂等键挡掉并留下预留；`ledger.consume` 增加同键异额保护，避免「同一笔调用被两种金额结算」。用例 `test_topup_attempts.py`。

### A-02 裁决结果回到导演（已完成）

`works._apply_decision` 除 `option_id` 外写入 `label / near_term_consequence / reversibility / trigger_summary`，并在 `advance_step` 里不再用 RUNNING 覆盖 PENDING_DECISION 状态；导演发起的裁决按 `impact_level` 分级给 deadline（低 6h／中 24h／高 72h），否则到期默认扫描永远扫不到；`compile_director_performance_context` 新增 `user_decision`，WATCH_TAKE / WATCH_PROSE 提示按所选后果推进。用例 `test_decision_reaches_director.py`（3 例）。

### A-03 末节点结束与跨卷（已完成）

`_after_chapter_completed()` 在 v2 的 CANON 成章分支与旧分支都要调用：末节点完成且扩不出新卷即标 `story_complete` 并把作品置 DONE；`expand_next_volume` 在旧卷 COMPLETED 时收口 `end_chapter_no`，否则新卷与旧卷区间重叠、装配会定位到旧卷；`start_run` 增加存量兜底。用例 `test_last_node_and_volume.py`（2 例）。

### A-04 正式事实生成记忆（已完成）

`_content_of` 读 `statement`；`classify()` 在缺少标记时按**人物共现**判定（两人 relation／一人 character_arc／无人不记，宁漏记不误记）；知情范围校验跳过「所有人知道」的记号 `ALL`。`cast_of()` 从在册人物取名做结构信号。用例 `test_memory_from_real_facts.py`（2 例）。

### A-05 依赖建图、按范围失效与可运行灰度（已完成）

- `record_chapter_memory` 落库即登记依赖边（共享实体/主体）；`plan_replay` 在**一条边都没有**时返回 `complete=False`——「没有下游」和「下游未知」无法区分，必须按未知处理。
- `update_critical_path` 改用 `invalidate_changed()`：图完整只失效「被改主题 + 下游子图」，不完整时保守失效同类整批，并在事件写 `invalidated_scope`（`dependency_subgraph` / `conservative_batch`）。
- `_node_changes()` 统一改动判定（标题/顺序/节点类型/**承诺**），受影响章节与记忆失效共用同一口径；此前只比标题与顺序，只改承诺文本会绕过固化冲突检查。
- 灰度：`start_run` 按作品分桶钉住 `executor`，`assemble` 重建上下文时用 `carry_over()` 保住执行器身份。
- **语义修正**：`switch_executor_allowed=False` 的处置是**沿用原执行器 + 记一次 `executor.switch_deferred`**，不是拒绝推进。原实现会 raise，操作员把坡度调到 100% 时落入新桶的作品的在途章节将永久卡死——把运维旋钮做成死锁，且前后两半来自两个执行器时盲评无法归因。
- 用例 `test_path_change_scoped_memory.py`（3 例）+ `test_executor_canary.py`（3 例）。

### A-06 盲评数据与晋级硬门（已完成）

- **注册校验**：`record_scores`/`submit_ratings` 只接受已注册样本与预注册评者（`EvalConfig.raters` 参与冻结指纹），arm 必须存在于该样本的映射里；凭空造分数是最省力的晋级方式，入口挡住比事后发现便宜。
- **私有映射持久化**：`BlindSample.as_record()` 落 `arms_in_order` 与 `content_refs`，`as_payload()` 仍只有 A/B 位置；新增 `arm_at_position()` 与 `submit_ratings()`——评者按位置打分，还原只发生在服务端。
- **按独立样本计数**：`summarise` 的 `n` 改为不同 sample_id 数，另存 `ratings` 条数；多评者评同一份样本不再放大样本量（先样本内均分再对样本求均）。
- **证据硬门**：新增 `Evidence` 与 `missing_evidence()`——模型是否与预算带一致、成本/延迟是否提供、注册样本数、舒缓/单人占比、正文引用、预注册评者，缺一项即 HOLD。
- **指纹重算**：`fingerprint_of(config)` 从存储重算，不信任库里那份 `config_fingerprint`（用它跟自己比等于没校验）；`report_fingerprint` 覆盖报告内容，改门槛或改报告都会被 `decide` 检出。
- 用例 `tests/unit/novel/test_eval_and_canary.py` 扩到 18 例，覆盖零注册样本、多评者单样本、缺成本、阈值篡改、报告篡改五个反例。

### 验证与反证

- 回归：`tests/unit/novel` + `test_novel_generation.py` + `test_worker.py` + `tests/integration` **221 passed**；改动源码 ruff（E,F,I,UP,B,SIM）除既有 E501 外无新增问题。
- 反证脚本：`deploy/novel/mutate_check_a05.py`（6 例）与 `deploy/novel/mutate_check_a06.py`（7 例）——把修复点逐个改回旧写法，确认新测试全部失败，13 个反例全部咬住。
- **仍未执行**：M2 浏览器旅程矩阵、R4 真实 pilot 与人评冻结。本轮不得据新判定宣布新导演胜出或扩大灰度。

### P0-5 绑定当前源码的 PostgreSQL 认证（2026-09-09，16/16）

在服务器 118.31.171.159 的独立临时库 `novel_pgverify` 上执行，不触碰运行中的部署。

**指纹（先记指纹，再谈通过）**

| 项 | 值 |
|---|---|
| git HEAD | `b3d5818071c95b656109cb2b0209184e0d9314ff` |
| 工作区脏文件 | 94（差异 sha `399ec42cf73d9a85`） |
| 迁移 head | `20260908_0054` |
| 数据库 | PostgreSQL 16.14（x86_64-pc-linux-musl，Alpine） |
| 运行时 | Python 3.12.14；api 镜像 `sha256:09a9ee55…` |
| 脚本 | `deploy/novel/pg_verify.py`；原始结果 `deploy/novel/pgverify-20260909.log` |

**结果**：全新库 `upgrade head` → 回退两级（0054→0053→0052）→ 再 `upgrade head` 全绿，`current` 回到 `20260908_0054 (head)`；并发与崩溃注入 **16/16 通过**。

场景覆盖：租约抢占唯一赢家、fencing token 单调递增、租约归属唯一、并发预留不破上限、未结清总额不超上限、崩溃后回收并对账结清、恢复后调用不再挂起、无悬空预留、旧 worker 租约失效后写回被拒、同 key 并发预留只剩一行，以及本轮新增的**并发章提交**（两章都落库且作品版本不丢更新）与**用户/默认裁决竞争**（状态唯一、只一个赢家、归因可区分 `user` 与 `timer`）。

失败记录：本轮唯一失败是断言写窄——默认裁决归因值记作 `timer` 而非 `default`，属测试断言问题，已修正后复跑全绿。归因值必须能区分「人做的」与「到期自动落的」，这一点被保留下来。
