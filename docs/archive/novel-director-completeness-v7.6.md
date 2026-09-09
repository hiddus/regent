# 导演式小说生成完备性复核（v7.6）

结论：**C-01～C-05 全部闭合**（代码 + 反证），导演闭环的五个生产缺口均已有实现、回归与反例。判定语义成立；真实模型下的质量提升仍无证据（见「仍开放的验收」）。此结论针对当前未提交工作树，以反证脚本与入口级测试为准，不以文档勾选代替行为。

## 本轮验证

- 定向回归：`tests/unit/novel` + `test_novel_generation.py` 共 **212 项全部通过**（含本轮新增 `test_c_batch.py` 13 例与 `test_c01_resume_api.py` 3 例）。
- B 批反例：`deploy/novel/mutate_check_b.py` **22/22 成立**。
- C 批反例：`deploy/novel/mutate_check_c01.py` **两个场景、合计 23 例次变红**——13/13 个行为断言被咬住（唯一不变的 `test_last_node_still_expands_when_user_set_no_cap` 是「扩卷路径没被整体删掉」的对照组，按设计应保持绿）。
- ruff（E,F,I,UP,B,SIM，忽略既有 E501）：本轮触碰文件全部通过。
- 迁移图单一 head：20260909_0055；未运行数据库升级或远程认证。
- 全程无真实模型调用：C-05 终局判断用桩模型断言请求载荷，不证明判断质量。

## 各缺陷的闭合方式与证据

### C-01：纠错执行（已闭合）

- 纠错内容（ticket/statement/subject/章号 + `replay_reason=fact_reported`）写入重演 attempt 的 `generation_context`，不再只躺在事件里（`works.py::_queue_replay_run`）。
- 重复报错不叠任务：同章已有 `attempt>1 且 QUEUED` 的重演即跳过。
- 调度规则如实：DONE/PAUSED_QUOTA/PAUSED_COST 返回 `resume_then_replay` 并说明「恢复后才执行」，不自动改状态，保留用户意图。
- **恢复衔接补齐**：新增 `resume_after_correction()` + `POST /works/{id}/resume-correction`。两个闸门——状态允许恢复、确有排队重演（无任务拒绝，不空放回 RUNNING）。
- 依赖顺序：`plan_local_replay` 返回的章号升序排队；依赖边只从早章指向晚章，升序即拓扑序。
- 证据：应用层 6 例 + **HTTP 入口层 3 例**（真实 FastAPI 路由 + 服务端 token 解析 + 409/401 envelope），反证覆盖全部语义断言。

### C-02：扩卷旁路（已闭合）

`_maybe_expand_volume` 删除 80% 完成度触发（它会经 `expand_next_volume` 提前结束活动卷、跳过剩余节点）；扩卷只在末节点完成时发生，且先过 `decide_ending` 的用户卷数约束。证据：3 例（80% 不关卷、用户限 1 卷不扩、对照组末节点仍扩）。

### C-03：承诺兑现与六类投影（已闭合）

- `VerifiedFact.resolves`：正文兑现关联到**具体承诺**；`resolve_items` 按 key/内容精确命中，按 subject 仅在唯一候选时关闭（同人两条只兑现一条 → 另一条保留；只给人物名的歧义兑现不整批关闭）。
- `_memory_view()` 接入生产：`perform` 拿角色视图、`weave` 拿叙述者视图、`direct` 拿导演视图（导演笔记不进正文、读者认知不给人物）。
- 证据：2 例兑现精度 + 反证退回按 subject 整批关闭后变红。

### C-04：依赖独立性证据（已闭合）

实体无交集不再自动认证 independent（unknown → 保守重做）；只有创作输入显式声明（`independent: true`）才登记。注意两个方向**互斥**：放宽会掩盖正向测试、收紧会掩盖反向测试，故反证分两个场景。首批仍标独立——那是「此前无任何记忆」的结构性事实，不是启发式。

### C-05：终局判断证据与统一调用（已闭合）

`generate_ending_verdict` 请求携带 `accepted_text[-4000:]` / `verified_facts` / `open_promises`，节点只取当前路径版本；调用走 `CallBroker`（幂等键 `ending:<卷>:<章>`，重试不重复计费，UNKNOWN 挂账不盲重试）。

## 本轮追加验证（前端接线 + 0055 真机认证）

- 前端报错面板接线 `resume-correction`：`facts/report` 返回 `resume_then_replay` 时显示「恢复创作并执行重演」按钮；TypeScript/Vite 构建通过。浏览器旅程验证属 M2。
- **0055 真机认证（服务器独立临时库，PostgreSQL 16.14）**：全新库 `upgrade head` → `20260909_0055` 一次通过；`downgrade -1`×2（0055→0054→0053）与再升级全部成功；并发与崩溃注入 **16/16 PASS**。指纹：git HEAD b3d58180，working diff sha bb032d68ca8a91f5。
- **真机抓出全新库无法安装的缺陷**：0048 的 `_LATER_COLUMNS` 归一化把 `novel_memory_items.resolved_chapter_no/basis`、`novel_memory_edges.edge_kind`（0055 新增列）也列了进去，但这两张表属 `_LATER_TABLES`（0053 才建），0048 对不存在的表发 `DROP COLUMN` → 全新库 `upgrade head` 直接失败。修复：清单只保留 0048 自己建的表，并加注释说明约束。此缺陷本机单元测试（`create_all` 不走 alembic）永远发现不了——只有全新库真机升级能暴露。
- 结论更新：**「迁移 0055 未执行验证」已闭合**；B-06 剩余部分为生产部署入口的运行时认证（api/worker 容器装配、真实流量），非 schema 范畴。

## 本轮额外发现并修复的生产缺陷（HTTP 入口测试带出）

1. **鉴权路径 500 而非 401**：`DateTime(timezone=True)` 在 SQLite 取回 naive，与 aware `now()` 比较抛 `TypeError`。修复 `principal.as_utc()`（fail-closed 语义，naive 按 UTC 补齐），同类隐患 `get_public_share` 的有效期比较一并修复。
2. **事件序列分配绑不上非 Postgres 驱动**：裸 SQL `text()` 直接把 `uuid.UUID` 交给驱动。改为 Core insert（按方言 `on_conflict_do_update`），绑定交给列类型，行级原子自增语义不变。
3. **`novel_events.id` 在 SQLite 不自增**：BIGINT PRIMARY KEY 不是 rowid 别名。`BigInteger().with_variant(Integer, "sqlite")`——Postgres 上仍是 BIGSERIAL，仅测试库可真写事件。
4. **反证脚本自身缺陷**：旧实现落盘 `.bak` 备份且文件名按随机 hash 生成，曾把 `domain/memory.py` 留在变异态。重写为内存保存原文 + 复原后逐字节自检 + 残留 `.bak` 检查。

## 仍开放的验收

- [ ] B-04/B-06：真实两策略灰度；B-06 剩生产部署入口的运行时认证（schema 迁移认证已完成，见上）。
- [ ] M2/R4/M4：浏览器旅程矩阵、真实盲评采样与人评冻结、长篇认证。
- [ ] C-05 的模型侧：`VerifiedFact.resolves` 由模型填写，无强制；真实生成里兑现率未测。
- [ ] `resume-correction` 前端按钮已接线并构建通过，但浏览器端到端旅程未验证（M2）。
