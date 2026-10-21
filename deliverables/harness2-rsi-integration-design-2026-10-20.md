# 吸收 Harness² 递归自改进：长篇写作配置的自我演化方案

日期：2026-10-20（同日修订）。依据：本地 HEAD ac5a282 及工作区已有修改；Harness² 开源仓库 [google-research/harness2](https://github.com/google-research/harness2) 与论文 [arXiv:2610.14224](https://arxiv.org/html/2610.14224)；同组 [google-research/rrsi](https://github.com/google-research/rrsi)。本次只做设计，未修改业务代码，未调用付费模型。

状态：方案，未实现。不代表长篇已具备稳定输出能力。

修订说明：初版低估了四件事，分别是回放基础设施、噪声基线、在不稳定流水线上的成本，以及模块体量与团队节奏不匹配。本版把“自动闭环”降为有门槛的后续立项。当前只做一件小而可证伪的事：对写作阶段做离线回放探针，由人工组合候选。

## 判断

Harness² 可以吸收，但不装它的运行时，也不在现阶段建自动闭环。

现阶段吸收的是方法：对同一输入做对照探针，由证据驱动提示词修改，并记录经验。组合与采纳由人完成，采纳的改动作为普通代码提交，并升级执行器版本号（现有 `EXECUTOR_VERSIONS` 机制）。自动改进器、改进库、本书覆盖层、在线循环，都要等第 9 节的门槛达标后再立项。

可以演化的只有提示词、写作载荷装配旋钮和写作技巧。正史提交、硬门、预算上限、命令白名单冻结。

前提已满足：服务器三章连写已在默认执行器 `director_chapter` 上通过验收（2026-10-20，作品 `064adf0a`，第 1–3 章均 CANONIZED，见 [mainline-expand-plan-2026-10-12.md](./mainline-expand-plan-2026-10-12.md) 状态更新）。本方案第一阶段仍不触碰生产路径。

这套机制只能改善由提示词、上下文装配和写作技巧引起的质量问题。台账、状态交接等结构缺口仍靠工程修复。

## 一、为什么不直接接入 Harness² 运行时

1. **执行器不匹配。** Harness² 只驱动 OpenCode 或 Codex 编码代理，靠运行时挂载的文件树起作用。生产默认臂 `director_chapter` 是“章纲 → 手段判定 → 写作 → 确定性硬门 → 最多改一次”的命令式流程，提示词是 Python 常量，每次调用都经过 `CallBroker` 预留与结算。
2. **可编辑面越界。** 护栏、脚本、插件、子代理会改变控制流，在本项目中对应正史、台账与硬门。
3. **安全与成本。** 上游执行器网络不受限，Codex 关闭沙箱；一次整章级 `k=1` 步骤需要多次完整回放。

## 二、四个难点的核实结论

| 难点 | 核实结果 | 本版处理 |
|---|---|---|
| 检查点回放 | 成立。`ModelCallModel` 只存 `prompt_hash`、`context_hash` 和输出，不存入参；`generation_context` 在 PRODUCE 中被改写（如 `target_node` 被章纲替换），没有整章入口快照 | 不做整章回放。只回放单个阶段，上游输出取参照运行已存的结果；先用已存哈希验证能否精确复原入参 |
| 噪声下限 | 成立，且更严重。模型接口没有 `seed` 参数；`ChapterProse` 写作调用温度是 0.8，同输入方差不小 | 噪声测量作为第一阶段的首个交付；改用同检查点配对比较，不依赖绝对阈值 |
| 成本 | 成立。整章级步骤约 60–80 次调用，在成功率未知的流水线上不可接受 | 写作阶段探针每次 1–2 次调用；只离线、人工触发；在线循环设成功率门槛 |
| 体量与节奏 | 成立。11 个模块加 3 张表约需 2–3 个月 | 第一阶段只有一个离线脚本、一个测试文件，不建表、不建新包 |

## 三、阶段级回放：为什么可行、怎么验证

整章回放难，是因为要重建 ASSEMBLE 后的全部上下文，并让多阶段链路在隔离环境中重跑。本版只回放被修改组件所在的那一个阶段。最常见的是写作阶段（WRITE），它的输入是：

- `build_write_payload(ctx, cast)`：读取 `generation_context` 中的世界书块、文风、读者契约、`target_node`（此时已被章纲替换，正是执笔看到的内容）、最近章节、前文结果、正史与伏笔；
- `_writer_system(ctx, cast)`：系统提示加前置门提示；
- `production["cast"]`、`production["chapter_plan"]`：都已持久化在运行记录里。

**可验证性：** `directing_calls._call` 把 `json.dumps(payload, ensure_ascii=False)` 的 sha256 同时写进 `production.calls[].input_hash` 和 `ModelCallModel.context_hash`。用已入正史运行的最终 `generation_context` 重建载荷并比对哈希：对上，说明入参被精确复原；对不上，就定位是哪个字段在 WRITE 之后被改写。

据此分两步：

1. **零调用的可行性检查（第一步交付）：** 对已有 `director_chapter` 的 CANONIZED 运行跑哈希比对，报告命中率。首稿调用（无 `avoid` 字段）与重写调用分别统计。
2. **命中率不足时才加快照：** 在 ASSEMBLE 结束时写入不可变的 `assemble_snapshot`（或其哈希加对象存储引用）。这是 `generation.py` 中的一处小改动，同时满足 10-11 复盘中“从入口快照重放”的诊断需要。

**隔离方式：** 探针不经过 `CallBroker`、不写数据库，直接调用模型接口，用 `quality_ab.MeteredProvider` 计量和限额。这样逻辑调用号的隔离问题不存在。

**防止镜像漂移：** 探针调用生产中的同一批纯函数（`build_write_payload`、`_writer_system`、`settle_chapter_text`、`check_chapter`、`name_warnings`），不重写编排。测试断言：对同一快照，探针生成的入参哈希与生产记录一致。

**局限：** 阶段级回放看不到改动对下游阶段和后续章节的连锁影响。章纲类改动需要同时回放 PLAN 与 WRITE，放到第二阶段。

## 四、噪声与比较方法

- 探针参数与生产一致：写作温度 0.8，同一模型。没有种子，“重复”就是独立采样。
- **先测噪声：** 选 8 个检查点，参照提示词各写 4 次，共 32 次写作调用，约合 2.5 章的费用。对 `check_chapter` 的通过与否、失败码，以及 `quality.py` 的相邻重复、套话密度、推进量，报告同输入下的分布。对固定文本，确定性指标没有方差，方差全部来自采样。
- **配对比较：** 每个候选在同一批检查点上与参照各采样 N 次（初值 3），逐检查点比较硬门通过数和指标；以胜、平、负计数做符号检验，不依赖绝对噪声阈值。噪声数据用于确定 N 是否够用。
- **读者偏好：** 只对硬门都通过的成对稿件做盲评。裁判模型与执笔模型不同，并交换前后位置。第一阶段裁判可以由人担任。

## 五、组件对应与可编辑面

| Harness² 组件 | 小说引擎对应物 | 第一阶段 | 后续 |
|---|---|---|---|
| 系统提示 | `CHAPTER_WRITE/REPAIR_SYSTEM` | 探针可替换，采纳后走代码提交 | 加入 PLAN、METHOD、ARC_REVIEW |
| 工具说明 | 写作载荷旋钮：前章尾段长度、前文结果条数等 | 探针可调，采纳后走代码提交 | 数据化，登记取值范围 |
| 技能 | 按章节职责加载的写作技巧 | 不做 | 门槛达标后 |
| 记忆 | 类型级写作经验 | 人工维护的经验文档 | 改进库 |
| 子代理 | 追读自评频率、手段判定轮数 | 不做 | 预算内调参 |
| 护栏、脚本、插件 | 硬门、确定性后处理、无 | 冻结 | 冻结；新增硬门只走人工 PR |

故事正文、世界书与模型输出进入改进上下文时一律视为数据，不执行其中的指令。

## 六、第一阶段交付

| 交付 | 内容 |
|---|---|
| `core/src/regent/novel/experiments/write_probe.py` | 离线命令：`check-replay` 做零调用哈希比对；`noise` 测噪声；`compare` 做候选对参照的配对探针。结果写入 `deploy/novel/artifacts/probe/` |
| `tests/unit/novel/test_write_probe.py` | 入参哈希与生产一致；用 `SimProvider` 跑通 `compare`；探针不写数据库、不动生产预算 |
| 经验文档 | 每次采纳或否决都写明：针对哪个失败、改了什么、探针证据、适用边界 |
| `generation.py` 快照（条件性） | 仅当哈希命中率不足时加入 |

候选提示词可以由人或编码代理起草。采纳的改动作为普通提交合入，并把 `director_chapter@1` 升为 `@2`，保证盲评与复盘可归因。

估算 3–5 人日。除条件性快照外，不改生产路径，不建表。

## 七、第一阶段的费用

一次配对比较：参照 3 次加 2 个候选各 3 次，共 9 次写作调用；修稿阶段有失败时最多再加同等数量；盲评若用模型约 6 次。合计约 10–20 次调用，相当于 1–1.5 章，只在人工触发时发生。

噪声测量一次性约 32 次写作调用。上述为估算，第一次运行后以实测替换。

## 八、第一阶段的退出判据

- 哈希比对给出命中率，必要时快照已合入，入参可精确复原。
- 噪声报告给出每个指标的同输入分布，并确定配对采样次数 N。
- 至少完成一轮针对真实失败的候选比较。重点是写作阶段可修的失败，如 R1 型“重演前章结尾”、长度越界、长段复用。结论（采纳或否决）有探针证据。
- 若一轮下来没有任何候选在配对比较中稳定胜出，就如实记录，不进入第二阶段。

## 九、后续立项门槛

满足全部条件才为第二阶段（自动改进器、多阶段回放、改进库、配置数据化）单独立项：

1. 主线计划工作包 4 通过：服务器三章均 CANONIZED（2026-10-20 已满足）。
2. 流水线稳定：至少 3 部作品、累计至少 30 章，章节入正史率不低于 80%，且没有人工改库。
3. 第一阶段至少有 2 个经探针采纳的改动，在未参与比较的检查点上复现收益。

满足第二阶段出口后，才评估在线循环（本书覆盖层、按作品灰度）。届时再定改进库表结构、晋级流程与预算账户，沿用初版第 4–7 节的设计思路，但重新估算成本。

## 十、不在本方案内

- 不引入 OpenCode、Codex 或 Harness² 运行时依赖。
- 不允许改进器修改 Python 代码、硬门、预算常量、命令白名单、正史与台账。
- 不复用 `application/harness_evolution.py`：它以缺口关键词给经验打分，易被刷分，且只挂在旧产品路由上。
- 不以自我改进替代 10-11、10-12 计划中的结构修复。
- b0ce 等历史失败出自分场协议，不是 `director_chapter`。它们只作定性案例，不进入写作阶段的回放集。

## 证据来源

- core/src/regent/novel/application/directing_chapter.py：提示词常量、`build_write_payload`、`_writer_system`、`check_chapter`、`produce_chapter_tick`
- core/src/regent/novel/application/directing_calls.py：`_call` 入参序列化、`input_hash` 记录、`ChapterProse` 温度 0.8
- core/src/regent/novel/application/production.py：`CallBroker`、`prompt_hash`/`context_hash` 校验、`output_json` 复用
- core/src/regent/novel/infrastructure/models.py：`ChapterRunModel`、`ModelCallModel` 字段
- core/src/regent/novel/application/generation.py：ASSEMBLE 重建 `generation_context`
- core/src/regent/model/provider.py：只有 `temperature`，没有 `seed`
- core/src/regent/novel/application/executor.py：`EXECUTOR_VERSIONS`、灰度分桶
- core/src/regent/novel/domain/quality.py、experiments/quality_ab.py：质量指标、`MeteredProvider`、`SimProvider`
- deliverables/long-novel-architecture-review-2026-10-01.md、long-novel-server-failure-review-2026-10-11.md、mainline-expand-plan-2026-10-12.md
