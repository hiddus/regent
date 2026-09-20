# Regent 三项优化实施提案

日期：2026-09-16。状态：待实施设计提案；不是已完成记录，也不替代 Novel-Engine-Plan.md 的开发顺序。采纳后将任务和出口回填唯一计划，稳定契约写入技术规格，本文保留决策背景。

## 1. 总体目标与边界

三项工作共同达到：小说服务不再启动旧应用交付业务；导演与作品服务具有清晰职责边界；责编发现可修问题后，能低成本修改正文并验证收益。

本轮只提供方案，不修改业务代码、不删模块、不迁生产数据。依据当前工作树检查，未用远端生产状态或新模型实验作为证据。

当前实现需要准确区分：

- API 不仅挂旧 app_*、product_creation、experiments 等路由，还在 main.py 内直接定义静态预览和运行时反向代理。
- Worker 不仅构造 ExecutionOrchestrator，还在循环中推进旧 runs、执行交付 watchdog、业务对账和调度；启动时初始化旧交付能力。
- Outbox 的 claim_statement 当前按状态和到期时间领取，没有产品事件范围过滤。直接拆两个 Worker 会产生错领风险。
- direction.py 负责规划、协议执行、调用预算、证据、人工裁决和章审校；works.py 负责创建到导出的大量用例，并存在双向局部导入。
- editor_audit.py 当前默认 sample：首章和每五章检查；所有已知规则仍强制 soft。抽样降低检查成本，没有使结果进入修稿。
- prose_patch.py 已有内容 hash、段落定位、连续区间、修改范围、重叠检查，应复用。其当前接口不允许空文本替换，不能直接当作任意删除操作。
- validate_chapter 尾部会设置 verified_facts、actual_state、validated_content_hash，并调用 _commit_run_story_ledger。责编若放在它返回之后再改正文，会留下旧证据和新正文不一致的问题。
- 最近工作树已增加 review_issues_may_coerce 等收紧策略。实施须从当前实现出发，不照旧审计恢复已经修掉的行为。

设计原则：保留模块化单体；先建立边界，再迁移，最后删除。模块拆分阶段不改提示词、策略默认、持久化键和收费行为；责编闭环作为独立行为变更上线。

## 2. 旧业务从 API / Worker 解耦并退役

### 2.1 先建立明确的能力清单

| 归属 | 内容 | 处理 |
|---|---|---|
| 小说产品 | novel 路由、身份与作品授权、SSE、章推进、裁决计时、小说调用恢复、私有分享/导出 | 保留并单独装配 |
| 共用基础设施 | 数据库连接、模型供应商、Worker 租约、日志、必要 Outbox/Artifact、主机资源观测 | 保留；去掉对旧业务实体的反向依赖 |
| 旧产品专属 | Generated App 创建/指导、预览、部署、交付评审、经营实验、自我改进经营链 | 停新增、排空、退役 |
| 待核实的共用能力 | uploads、通知、human_tasks、scheduler、environment_heal、通用对账 | 按真实调用、表和副作用划分，不凭文件名整包删除 |

特别区分旧经营 experiments/eval 与 novel/experiments、小说盲评。退役前者不等于删除小说质量实验。

建立一份版本化退役清单，逐项记录入口、调用方、事件类型/版本、数据库表、外部资源、活跃任务查询、替代路径、删除批次。Python 导入图只是其中一项；字符串注册的事件、动态导入、部署命令、控制台 HTTP 调用和数据库持久化标识也必须查。

### 2.2 API：由单一大入口改为显式装配

建议新增薄装配层（具体命名可调整）：

```text
regent/bootstrap/
  shared.py          # sessions / provider / 基础日志和资源
  novel_api.py       # 当前产品 API
  novel_worker.py    # 当前产品 Worker
  legacy_api.py      # 临时保留，仅用于迁移
  legacy_worker.py   # 临时保留，仅用于排空
```

对外保留现有 regent-api / regent-worker 命令。迁移阶段新增部署配置 REGENT_SERVICE_MODE=novel|legacy|combined（新配置提案，不是现有配置）。combined 仅用于兼容过渡；迁移完成默认 novel，并删除 combined/legacy 装配，不形成永久双产品框架。

API 改造项：

1. create_app 先解析运行模式，再导入对应路由；novel 模式不能在文件顶层 import 旧 app_* 模块。
2. 将 main.py 内 /preview/* 静态文件和 runtime 反向代理整体迁入 legacy preview router；不只是删 app_previews_router 注册。
3. 小说路由路径、鉴权和响应格式保持不变，减少前端迁移成本。
4. lifespan 的交付评审、产品表面能力、旧 runtime profile 初始化仅在 legacy 执行；真正共用的资源初始化单列。
5. /health/live 只反映进程存活；/health/ready 检查本模式必需组件。novel readiness 不应依赖旧 goals/runs/delivery 表。
6. 运维健康详情以小说章任务、调用账本、恢复积压为主；旧交付统计放在 legacy 详情中。主机观测保留，但预览进程回收和旧目录清理必须有独立归属。
7. root 返回小说前端。前端缺失时给明确不可用信息，不自动退回旧经营控制台。
8. SPA fallback 排除退役 API/preview 前缀；不存在的旧接口不得被 index.html 的 200 响应伪装成可用。

旧接口退役策略：停止创建入口可临时返回 410 与迁移说明；内部未公开接口可在切换后直接 404。只读历史访问如有需求，可独立保留限期入口；不能为了看历史预览继续启用任意代码执行和新部署能力。

### 2.3 Worker：拆装配与任务归属，不新造调度框架

保留简单 Worker 生命周期：启动、租约、心跳、轮询、停止。装配阶段明确其可执行任务，而非每轮读取几十个动态开关。

novel Worker 保留：

- 小说章任务领取与同作品前章依赖屏障。
- 小说模型 RESERVED/UNKNOWN 对账。
- 小说裁决到期处理、预算暂停/恢复。
- 确认仍被小说使用的事件派发和基础观测。

legacy Worker 承载：旧 ExecutionOrchestrator、generator selector、deployment provider、预览沙箱物化、旧 runs 回收、交付 watchdog、经营调度与经营事件。

不能仅把这些对象设为 None：当前循环里有依赖 sessions 就执行的旧任务，需要全部移入 legacy 工作单元。小说 Worker 不应构造 workspace writer、静态/运行时 preview provider，再等待它们“没被用到”。

### 2.4 队列隔离必须在领取前完成

对 OutboxDispatcher 增加显式订阅范围，过滤进入 SQL claim 条件，并保持 SKIP LOCKED 与租约语义：

- novel 只领小说/明确共用事件；legacy 只领旧业务事件。
- 已知但属于其他模式的事件留在所属队列，不领取后报“无处理器”。
- 未注册事件走独立隔离/告警流程，不能被新 Worker 自动标成功或耗尽重试。
- TimerFired 等通用名字可能跨业务复用，不能只看名称。按 payload 中持久化的业务归属/任务实体分类；若不足以无歧义判断，增加 producer scope 字段，先回填，再切换领取。
- 新生产者在同一事务内写正确 scope。模糊历史项进入待归类清单，不推测处理。
- EventEngine 与 OutboxDispatcher 的双入口也按清单核对，保证同一事件只有既定处理责任，不在拆分时新增重复执行者。

若小说当前并不使用某一通用 Outbox 消费链，不强行为它创建新的消费责任；保持其既有 novel events/SSE 存储机制。产品事件展示与后台任务队列是不同用途。

### 2.5 上线、排空与删除顺序

| 阶段 | 动作 | 出口 |
|---|---|---|
| A | 记录活跃任务、事件、计时器、UNKNOWN 外部操作、预览进程及资源清单 | 每项有处理归属和处置办法 |
| B | 停止旧业务新建入口和自动生产者 | 新任务数量不再增长，重复请求也不会绕开 |
| C | 新 novel API/Worker 灰度；legacy 仅排空 | 无跨域错领，小说旅程可用 |
| D | 完成/取消旧任务并对账；停止旧预览进程与计时器 | 无未处理外部副作用、活动租约或待恢复旧任务 |
| E | 移除旧路由、装配、控制台页面、部署配置和专属依赖 | 新镜像运行不导入旧业务模块 |
| F | 删除无调用方的旧业务代码和专属测试 | 干净检出构建、运行、迁移认证通过 |
| G | 独立数据保留与删表批次 | 备份与恢复演练、保留期限和审计要求已明确 |

排空观察窗应覆盖实际最大租约、重试退避、计时器周期和 UNKNOWN 对账周期；不能固定等一天就认定安全。正在进行的外部调用不能靠把数据库状态改 DONE 来“排空”。

保留历史 Alembic 迁移，检查其是否导入准备删除的运行时模块；必要时把迁移依赖固定到自包含逻辑。旧表可先只读保留。源码退役与数据库销毁分开，保证回滚窗口内能切回旧镜像。

### 2.6 验收与回滚

必测：旧模块被禁止导入时 novel API/Worker 仍能启动；OpenAPI 与实际路由均没有旧入口；健康检查无需旧业务表；同一队列混合事件下各 Worker 只领取本范围；小说创建→澄清→确认→生成→暂停恢复→裁决→导出完整通过；UNKNOWN 对账及双 Worker 竞争不退化。

回滚通过部署模式/旧镜像进行，期间不删除旧表、不回写污染小说数据。取消退役不应重新投递已经结算或明确取消的旧事件。

## 3. direction.py / works.py 的职责拆分

### 3.1 拆分顺序与不可变条件

先提取纯类型/策略和依赖边界，再迁移有副作用函数。每个提交只迁移一组职责，避免同时改提示词和行为。文件行数只作异味指标，不作拆分验收目标。

不变量：现有 executor 身份、production phase、JSON 字段、logical_call_id 算法、模型请求内容、预算结算、事件顺序、run/input_version、租约竞争检查保持原语义。新责编策略改变行为时再显式升级 pipeline/policy 版本。

### 3.2 direction.py 的目标结构

```text
novel/application/
  direction.py              # 临时兼容入口，显式转发，不再承载策略
  directing/
    contracts.py            # 内部请求/返回类型，版本兼容加载
    planning.py             # plan_chapter、人物声明、设定准备
    calls.py                # _call/_call_batch、调用标识、配额预留
    evidence.py             # 正文定位、判定证据、核验报告有效性
    scene_loop.py           # director_v2 / beat 流程
    script_loop.py          # 候选、选择、装配；复用分场步骤
    scene_steps.py          # WRITE_SCENE / AUDIT_SCENE / 装配
    chapter_review.py       # 章审校编排与有界回退
    checkpoints.py          # production 保存、恢复兼容、take 生命周期
  editorial_repair.py       # 第三项新增的责编局部修订用例
```

这是职责上限，不要求第一批一次建完。evidence 中纯算法适合再归入 domain；有调用/持久化的规则留 application。不要新建巨型 utils.py、万能 Context 或逐函数一个类。

具体迁移：

| 原职责 | 新归属 | 约束 |
|---|---|---|
| ARCHITECTURE/协议常量、EndingVerdict 等 | domain 或无副作用 contracts | executor.py/generation.py 不再为读常量导入整个导演 |
| 预算/调用计数、_call/_call_batch | directing/calls.py | 继续通过 production.CallBroker，禁止第二套计费器 |
| _is_grounded/_quote_check/报告检查 | directing/evidence.py，纯函数再下沉 | 单一证据语义，生产与实验复用 |
| _produce_script_tick 的大分支 | script_loop.py + scene_steps.py | 保留持久化 phase，不用模块名产生调用键 |
| _new_take/_retake/_save | checkpoints.py | 不修改旧 take 索引与 SUPERSEDED 语义 |
| validate_chapter | chapter_review.py | 分清检查结果、回退决定、最终接受副作用 |

对反向调用 works.create_decision，先提取真正的 decisions 服务，使 directing 直接依赖该服务或小型 DecisionPort；服务不得再回头导入 direction 门面。

### 3.3 works.py 的目标结构

```text
novel/application/
  works.py                  # 迁移期兼容门面
  work_access.py            # 所有权、版本校验，复用已有 principal
  work_queries.py           # 作品/章列表与详情、版本、角色查询
  onboarding.py             # 创建、澄清、方向选择
  world_bible_service.py    # 读取/修改/锁定/历史兼容设定
  path_service.py           # 路径编辑、影响预览
  volume_service.py         # 扩卷、终局判断和章后推进
  chapter_runs.py           # 开章、暂停、恢复、预算授权、用户引导
  run_advancement.py        # advance_step、后台领取、租约和失败映射
  decisions.py              # 创建/查询/解决/超时裁决
  corrections.py            # 事实报错、排队重演、重复纠错合并
  sharing_exports.py        # 分享、撤销、导出提示与导出
  moderation_service.py     # 内容扫描、申诉、处置
```

已有 memory、production、ledger、events、principal 模块继续复用；不复制一份对应工具。查询较少的模块可先合并，拆分是否成立看依赖和职责。

run_advancement 是事务与恢复编排入口，调用 chapter_runs / generation / volume_service；叶子模块不反向调用它。后台领取与前台单步推进必须共用同一推进语义，不能各实现一套。

### 3.4 依赖和事务规则

允许的方向：API/Worker → 用例服务 → 导演流程 → 纯领域策略 + CallBroker/仓储。查询服务不触发模型；纯领域层不 import API/SQLAlchemy；叶子模块不 import direction.py/works.py 兼容门面。

不要为了消循环导入而将全部 import 移入函数内部。发现循环时拆出共同类型/策略，或让上层编排两边；只对真实可替换的有副作用边界使用小型端口。

事务规则：

1. 普通子服务接收现有 session，只 flush，不自行提交业务事务。
2. 不在搬代码时挪动现有显式 commit；先记录其租约/模型调用前置作用，再单独整理事务边界。
3. 模型请求继续在长事务之外，调用预留与结算沿用 CallBroker 的独立短事务。
4. 模型返回后，重检租约、input_version、当前稿 hash。过期结果可保存调用证据，不得更新当前作品。
5. 将来统一的最终接受入口只接收通过验证的候选版本；不能把所有服务改为拥有任意 commit 权限。

### 3.5 兼容与迁移批次

第一批：常量/类型、证据纯函数、查询/导出等低依赖职责。第二批：decisions、调用适配、规划，解除双向依赖。第三批：两类协议循环和章审校。第四批：作品推进、恢复和最终接受。

direction.py / works.py 暂保留显式兼容函数，方便 API 和在途版本迁移。内部新代码禁止导入门面；迁移完引用后删除无用转发。不要依赖把旧模块里的对象 monkeypatch 后自动影响新模块的全局变量；测试改为注入 provider、session、策略配置等真实边界。

### 3.6 验收不是“文件变短”

以脱敏 checkpoint 夹具覆盖四种已有执行器、用户裁决、预算暂停、UNKNOWN、纠错回退。用确定性假模型回放搬迁前后，比较请求 schema/prompt、调用键、事件顺序、预算账、最终正文和状态；外部模型不要求字节可复现。

必须验证：各关键 phase 重启续作、旧 JSON 缺字段兼容、前章屏障、双 Worker、用户改意后旧结果丢弃、事实和记忆不重复写入。API 契约与浏览器旅程补一轮，PostgreSQL 验证锁与事务，不能只用 SQLite 代替。

重构使用旧镜像可以读新写出的 checkpoint 才可直接回滚；若将来新增责编阶段，则走下一节独立的版本化回滚办法。

## 4. 责编 soft → 自动局部修稿闭环

### 4.1 两个决策分离

“这一章是否做责编检查”与“已发现的问题是否值得自动修”是两个策略。保留抽样降本，但被抽到且有可修问题时，应该实际生成候选补丁、验证，再决定采用。

soft 的含义是“不因这一问题无限阻断交付”，不是“不允许改进”；事实矛盾也不能因为来自责编就永久被归成 soft。

建议配置分开：

- 现有 NOVEL_EDITOR_AUDIT=off|sample|always 继续控制检查。
- 新增 EDITOR_REPAIR_MODE=off|shadow|auto 控制修稿。shadow 只生成并验证候选，不替换正式正文。
- 把采样决定、修稿策略版本、额度上限在本轮编辑开始前持久化；重启和中途改环境变量不重新抽样或换策略。

默认先 shadow，积累固定样本再按作品稳定分桶灰度 auto。前置硬门失败时任何模式均先处理硬门，不把 always 理解为必须多花一次责编调用。

### 4.2 插入位置：最终接受之前

目标顺序：

```text
场景生成与场景核验
  → 组章
  → 基础整章核验（只产出候选核验报告，不固化跨章账本）
  → 责编抽样 / 风险触发
  → 问题定位与分类
  → 一次局部补丁候选
  → 确定性校验 + 语义复核
  → 选择原稿或候选稿
  → 最终接受（正文/证据/hash/事实/记忆/事件保持同版本）
```

将现有 validate_chapter 拆为检查、修订决策、最终接受三个内部步骤；将 _commit_run_story_ledger 等最终副作用移到选稿后，不在责编改稿之后继续复用旧 validated_content_hash。

每次编辑固定 base_revision_id、base_content_hash、input_version 和策略版本。原稿保留为不可变基线，候选单独存储；不能先覆盖 run.content 再尝试验证，失败后才拼命恢复。

### 4.3 问题分类与可改边界

| 类别 | 处理 | 示例 |
|---|---|---|
| 已有信息的表达问题 | 可自动局部修订 | 重复解释、指代不清、术语首次说明过弱、隐喻过密 |
| 需核验的事实问题 | 转既有硬核验，确认后走受控纠错 | 同章身份/物品归属冲突、代价前后矛盾 |
| 需要新增剧情/设定 | 不让责编自行补，交导演重规划或留待改进 | 新增金手指限制、新角色、改人物选择与因果终点 |
| 纯偏好/弱证据 | 记录，不自动修改 | “更燃”“更高级”“最好再反转一次” |

第一批建议只自动处理 redundant_beats、metaphor_overuse、closed_set_without_scope，以及能从已批准资料引用定义的 jargon_first_gloss。power_readable/info_debt/causal_unlink 只有在无需新增事实且证据充分时后续开放。contract_invisible 不自动补剧情。

责编不能更改：人物核心选择、身份、关系状态、世界规则、能力上限、交易结果、时间顺序、必须兑现节拍、读者获知时点。对白可以精简，但不得改变说话人的承诺与意图。

### 4.4 用结构化 Issue 代替修稿字符串

建议新 EditorialIssue（现有字符串字段暂保留投影以兼容 UI）：

```text
issue_id                确定性生成，与规则/基线/位置关联
rule_id                 白名单规则
base_revision_id/hash   被审版本
paragraph_ids + quote   在原稿中的精确位置
reason                  可检验的问题描述
repair_goal             希望消除的具体问题
kind                    expression / factual / structural / preference
allowed_fact_refs       允许用于澄清的已批准事实来源
status                  detected / eligible / fixed / retained / escalated / invalid
```

程序核对 quote 真在正文指定段落，不因模型提供 quote 字段就信任。出现相同文本多处而未指明位置，标为歧义，不随意改第一处。没有锚点的问题可以留评语，不能直接变补丁授权。

严重度和可改范围由规则与来源约束共同决定，不只相信模型自报 confidence。issue_id 对同一基线稳定，跨不同基线不要复用旧定位。

### 4.5 补丁协议与范围限制

复用 prose_patch.apply_patch，生成基于可信 base_text 切出的 Paragraph；携带 base_hash 与 allowed_paragraph_ids。模型只返回 replacements 和解决了哪些 issue_id，程序负责合并。

首轮建议默认（是待验证起点，不是已证最优参数）：每章一次编辑轮次，每轮最多三个表达问题；最多三个非重叠修改区间；原稿被触及字符数与候选新增量都设上限，例如各不超过章字数 15%，另设绝对上限。超过局部范围直接拒绝，不自动改为整章重写。

邻接一段可作为理解上下文，但只有显式允许的段落可编辑。若问题散布全章，选择最有价值的少数问题或交导演处理，不通过扩大窗口变相整章重写。

删除重复段落：当前补丁器拒绝空 replacement。第一期合并“重复段+紧邻保留段”为非空替换范围，且该邻段也必须显式授权；若无法安全定位，放弃该删除。后续确需 delete 操作时单独加协议版本和边界测试，不能悄悄让空响应等同删除。

### 4.6 验证与选稿

第一层，程序检查：基线 hash、版本、租约、修改范围、段落连续性、无重叠、未修改区域字节保留、变更量、前置规则和已知重复规则。

第二层，独立的候选复核调用：查看 issue、原稿/候选改动、邻接上下文、场景退出条件、已批准事实和必要整章上下文。返回每个 issue 是否解决、证据位置、有无新事实/因果/人称问题。不能由修稿模型一句“已修好”直接通过。

基于本轮最终候选重新建立正文证据引用。旧事实引用若对应未变文本可校验后复用；被修改/删除的引用、节点兑现依据和场景边界必须重新映射并核验。模型核验不能提供数学保证，所以灰度期间另做人评回看。

接受规则：至少一个目标问题解决，未产生新的硬问题，所有既有事实和必须节拍仍有有效证据，改动在授权范围内。多个补丁作为一个候选原子接受或原子拒绝；第一期不混合挑选互相依赖的半份补丁。

候选失败/无变化：原稿本来已通过基础硬核验，则保留原稿和未解决 soft，不追加重试。若责编发现并经核验确认原稿本身存在事实硬错误，则不能把原稿当安全回退，必须进入已有硬修复/暂停路径。

### 4.7 可恢复状态，不增加顶层业务状态爆炸

在现有 REVIEW 内加版本化 editorial 子状态：

```text
PENDING → AUDITED → PATCH_PENDING → PATCHED → VERIFY_PENDING
        → VERIFIED → APPLIED
        → SKIPPED / RETAINED / ESCALATED
```

子状态建议保存在 versioned generation_context.editorial 中，包含 audit/patch/verification 调用键、候选 hash、问题清单、原稿与候选引用、累计调用/金额、结束原因。大正文放既有版本/Artifact 存储，用引用链接，避免无限增大每次 checkpoint。

每个有模型调用的子步骤独立可续作，下一 tick 从检查点恢复，不能在一次 validate_chapter 内循环调用直到满意。

CallBroker 的 logical_call_id 包含 run_id、input_version、base_hash、editorial_policy_version、stage、round；核验键另含 candidate_hash。补丁内容改变不能复用旧核验成功；同键同参恢复复用已成功结果；UNKNOWN 先对账，不发第二次相同调用。

用户改意、其他 Worker 接管或基线变化时，旧候选标过期；调用费用照实记账，但不得写回新正文。服务关闭时不撤销账单事实。

### 4.8 预算与停止条件

所有调用沿用章级 calls/cost 账本，编辑子预算只是现有预算的子限额，不新增账外费用，也不增加章级总上限。

常见路径：被抽样章节已有一次审核，再新增一次补丁、一次语义复核。必要的正文证据重验和最终收尾也要计入预留；不能承诺所有路径最多增加两次调用。

开始 PATCH 前必须有能力完成 PATCH + VERIFY + 最终核验。预留不足且原稿合格，直接跳过本次可选编辑；发生 UNKNOWN 时仍按账本规则对账，不能因为编辑可选就丢掉费用。遇硬错误时沿用预算暂停，不伪装成正常合格。

一轮无进展立即结束；拒绝补丁、不确定语义或纯文风偏好均不自动再来一轮。下一章不会因为同一句旧评语无限追加编辑预算。

### 4.9 最终提交与前端

一个受版本/租约保护的最终接受步骤，发布最终正文 revision、validated_content_hash、核验报告、事实与记忆引用、故事账本及相应事件。数据库内的正式状态和 outbox 事件原子提交；若 Artifact 在文件/对象存储中，先写不可变对象并验证可读，再提交引用，崩溃孤儿对象由后续回收，不假称跨存储原子事务。

前端继续显示“校稿中”等用户语言，不暴露 issue schema。已有流式正文如允许展示须维持草稿属性；最终接受后切换版本。进度投影读取 editorial 子状态，不能显示章节已完成而后台继续换稿。

保留内部差异、采用/保留原因与调用账。用户无需逐条审批 soft 修订；超出原意的剧情选择才进入现有导演/用户裁决机制。

### 4.10 上线验证与回滚

先 shadow 比较原稿/候选稿，再稳定作品分桶灰度 auto。评估集至少覆盖不同题材的重复铺陈、术语说明、误报、跨段证据、相同摘录多处、用户改意和低预算；固定样本和阈值后再运行，保留所有失败候选。

指标分开看：目标问题解决率、盲评偏好、硬问题新增数、事实保持率、补丁拒绝率、原稿保留率、每千合格字增量成本、P50/P95 延迟、UNKNOWN 与恢复成功率。代码验收要求没有已知越界/重复计费/事实污染反例；有限样本“零事故”不等于绝对正确。

建议晋级条件：确定性范围/幂等/恢复测试全部通过；抽样人工检查未发现硬语义回归；有可解释的净质量收益且成本在预先冻结的产品预算内。质量收益的最小幅度由 pilot 冻结，避免先拍脑袋写一个胜率再迁就数据。

回滚关闭新编辑轮次，但已 PATCHED/VERIFY_PENDING 的轮次仍由兼容版本收尾或按已保存原稿安全放弃；不让旧镜像读取它不认识的阶段。保留可读旧/新检查点的发布版本作为回滚基线，直到在途轮次排空。

## 5. 建议拆成九个可评审变更

| 批次 | 内容 | 可交付物 / 验收出口 |
|---|---|---|
| 1 | 运行边界与回放基线 | API/任务/表/资源清单；当前 checkpoint、请求和账本回放集 |
| 2 | API 模式装配与旧预览迁移 | novel 启动不导入旧路由；健康与 SPA 契约通过 |
| 3 | Worker 模式与领取隔离 | 混合事件、计时器与租约故障测试通过；旧业务开始排空 |
| 4 | 纯类型/证据/查询/裁决模块提取 | 打破主要循环依赖；搬迁前后确定性回放一致 |
| 5 | 导演协议与作品推进拆分 | 明确事务/恢复入口；旧执行器 checkpoint 全可续作 |
| 6 | 审校/最终接受分离 | 未选定最终正文前不固化新账本；hash/证据/记忆一致性验证 |
| 7 | 责编结构化问题与 shadow 补丁 | 一次局部修稿→独立核验→保留候选，不改正式输出 |
| 8 | 责编 auto 灰度与恢复认证 | 达到冻结质量/成本门槛；故障注入、用户改意与预算路径通过 |
| 9 | 旧业务物理删除与文档收口 | 排空证据齐全、干净构建/新库迁移通过；移除临时兼容模式 |

依赖关系：1→2→3；1→4→5→6→7→8；9 依赖旧业务排空与小说运行验收，不必等待文学质量评估全部结束。代码模块拆分和行为升级分开发布，便于归因和回滚。

本方案不建议先做完全体插件框架、微服务拆分、统一万能状态机或全量数据库重建。完成标准是：删掉旧业务后小说仍可恢复运行；拆分后开发者能在单一职责处定位问题；责编闭环能以有界成本证明正文确实改善。
