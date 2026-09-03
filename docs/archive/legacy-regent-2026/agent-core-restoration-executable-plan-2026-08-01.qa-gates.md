# Agent 内核可执行修复计划：QA 与统计门禁

> 本文件是主计划的强制验收附件；与主计划冲突时，以本文件的可复现性、样本量和回滚要求为准。

## 当前基线

- 隔离临时目录实跑当前测试：712 collected，7 failed，约 702 passed，3 skipped；因此不得把当前分支称为绿基线。
- 默认共享 `.pytest_tmp` 在并行执行时出现 Windows `WinError 5`，必须改为每个 worker/run 独立目录。
- “109 个测试文件中 101 个使用 mock”无法按可复现口径验证；删除该断言，后续统计必须附脚本与定义。
- Eval E2E 引用的 `tests/fixtures/eval_task_set_v1.json` 不存在时会回退默认任务集并继续通过；缺 fixture 必须 fail closed。
- 现有单 Agent baseline 仅 5 个声明式任务，不足以充当可执行生成基准。

## 合并门

M0/M1 合并前必须满足：全量测试 0 failed、0 xfail，skip 仅允许白名单；连续运行两次结果一致；2 个并行 worker 无临时目录冲突。新增契约测试必须覆盖 Runtime Profile/health、manifest 文件数与字节截断、`finish_reason=length`、畸形 tool call、429/5xx/timeout 退避、401 不重试、预算耗尽不可晋级、primary failure code 守卫。

## Nightly 门

录制回放 provider 加真实本地 sandbox/smoke/Preview；固定 10 个故障注入场景，每场至少 3 次。要求错误分类 100%、静默丢文件 0、预算/成本/transcript 对账 100%。任何越权、数据泄露、假成功或假 `ACHIEVED` 立即失败。

“repair gap 单调不增”只适用于同一失败类别的确定性 fixture；真实任务允许修复暴露新的下游 gap，应报告 gap 生命周期而非强制总数单调。

## 候选基准

冻结 20–30 个公开开发任务与至少 20 个隐藏验收任务，覆盖 Python API、静态前端、TypeScript 前端、持久化 CRUD、依赖安装和修复任务，并按类型/难度分层。同任务、同预算配对 baseline/candidate，至少 3 seeds。每次记录 commit、镜像、模型快照、prompt/tool/Profile/Skill hash、seed、token、成本、分段时延和完整 failure taxonomy。

- 工程正确性：基础设施自造失败 0；artifact integrity 100%；任一安全或假成功事件即失败。
- 产品比例门：不能用 20 个样本的点估计宣称达标。若要求 `preview_ready ≥ 30%`，至少使用 n=40 且 95% CI 下界达到预注册阈值；更正式的策略比较按功效计算扩样。
- 收敛门：配对比较 `first_runnable`、`verified_success`、repair 中位数/P90、总 token、成本和分段时延；最低改善量和最大退化在运行前冻结。
- Skill 门：隐藏任务上的 on/off 配对消融；成功率差 95% CI 下界大于 0；路由同时报告 macro-F1，不只报告 accuracy；至少两类未见 Goal 正迁移，成本增幅不超过预注册护栏。

## Canary 与回滚

最终 canary 观察连续 7 天或至少 100 个独立 Goal，取更晚者；失败、超时和取消按 intent-to-treat 进入分母。3 位用户闭环只算定性证据。

以下任一事件立即回到 0% agentic：数据泄露、越权、假 `ACHIEVED`、发布未经验证 Artifact。以下任一滚动 20 Goal 窗触发回滚到上一镜像与冻结 bundle：`preview_ready` 或 `first_runnable` 低于 baseline 10 个百分点以上，`unplanned_rescue_rate > 0.40`，P95 时延或成本超过 baseline 1.5 倍。恢复必须包含根因、回归测试、完整重放基准，并按 5% → 25% → 50% → 100% 分级；在途 Run 按创建时冻结版本完成或取消，不跨版本续跑。
