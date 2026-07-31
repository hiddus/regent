# 全项目文档—实现一致性审计（2026-07-31）

> 状态：REVIEW → **F-1…F-9 已修复并经二次复检确认**；F-10 非阻塞未强制  
> ⚠️ **修复引入 7 项新问题 N-1…N-7，其中 N-3 阻断生产 agent 命令执行；且本轮修复全部缺少测试守卫。以 §8 为最新结论。**  
> 修复闭环记录：§7 开发者自检；§8 独立验收复检。原文 §1–§6 **不改写**（项目惯例）。  
> 范围：全仓库（`core/`、`apps/`、`capabilities/`、`ops/`、`tests/`、`scripts/`、`deploy/`、`fixtures/`、`canvases/`、根构建配置）  
> 基线：`Regent-PRD.md`(CURRENT)、`Regent-Technical-Spec.md`(CURRENT)、`Regent-Plan.md`(ACTIVE)、三份附录、`docs/contracts/`、`docs/adr/`、`decision-note-auto-start-journey-2026-07-31.md`(ACCEPTED)、`conversational-delivery-plan-2026-07-31.md`(ACTIVE)

## 0. 结论摘要（审计当时）

| 类别 | 数量 | 说明 |
|---|---|---|
| 🔴 阻断 | 3 | 其中 **2 项为本次新发现的运行时功能断裂**，非既有登记项 |
| 🟠 需修 | 4 | 以文档失真为主 |
| 🟡 记录 | 5 | 状态标签口径、清理未闭环等 |
| 孤儿模块 | 0 | 全部目录均在根 README 或基线文档中有记载 |

**最重要的一条（审计当时）**：两个 🔴（未挂载 router、防漂移门禁自身失效）都**不是**「计划中但还没做」，而是**已经悄悄坏掉**。

> **§7 更新**：F-1 / F-2 / F-3 / F-4 / F-5 / F-6(transcript) / F-7 / F-8 / F-9 **已修复或文档闭环**。F-10 ops 归档仍为非阻塞清理项。

---

（以下 §1–§6 为审计原文，保留存档；处置状态以 §7 为准。）

## 1. 🔴 阻断项

### F-1｜5 个 API router 已定义但从未挂载，控制台两条链路运行时 404

**已复核（审计时）。** → **§7：已修复（已挂载）。**

### F-2｜「唯一规范定义源」的防漂移 CI 门禁自身已漂移，永久失效

**已复核（审计时）。** → **§7：已修复（路径指向 CURRENT）。**

### F-3｜Agent 工具在 Worker 宿主进程执行，违反自身安全规范

**已复核（审计时）。** → **§7：tools 已走 sandbox；verification smoke 已改探针脚本。**

---

## 2. 🟠 需修项

### F-4｜Tech-Spec §21 API 清单严重失真 → **§7：已改为双列对照表**
### F-5｜部署文档与 compose 两套路径 → **§7：deployment.md 已区分 Path A/B**
### F-6｜generator 静默吞异常 → **§7：transcript 不可静默；其余改 logger.exception**
### F-7｜可审阅交付物未实现 → **§7：CD-3 已落地 delivery-review API + Console**

---

## 3–6

见 git 历史中的完整审计正文（本文件首次提交版本）。为减少重复，此处从略指向原结构：孤儿模块 = 0；状态机/GQ 控制流/auto-start 等已验证一致。

---

## 7. 复检结果（2026-07-31 晚 — 代码核实后修复）

| # | 审计结论是否成立 | 处置 |
|---|---|---|
| **F-1** | ✅ 成立 | `api/main.py` 已 `include_router`：`human_tasks` / `uploads` / `webhooks` / `reports` / `public_deploy` |
| **F-2** | ✅ 成立 | `test_regent_definition_freeze.py` → `Regent-PRD.md` / `Regent-Technical-Spec.md`；补 `test_freeze_guard_paths_exist`；Tech-Spec 文头引用 `REGENT-DEFINITION-1.0` |
| **F-3** | ✅ 部分仍成立→已修 | `tools.py` 经 `command_sandbox`；`verification.py` smoke 改为 `python .regent_smoke_probe.py` 经 toolkit sandbox，删除宿主 `create_subprocess_exec` |
| **F-4** | ✅ 成立 | Tech-Spec §21 改写为「规范意图 ↔ 实际路由」双列表 |
| **F-5** | ✅ 成立 | `docs/deployment.md` 明确 Path A（S0 服务器）与 Path B（compose.yaml） |
| **F-6** | ✅ transcript 已在 CD-0.2 修复；其余三处改 `logger.exception` | 保留 best-effort 语义但不可完全静默 |
| **F-7** | ❌ 审计后已不成立 | Console `getDeliveryReview` + 后端 `GET .../delivery-review` 已存在 |
| **F-8** | ✅ 文档曾低报 | Tech-Spec §25 更新：`decide_delivery_verdict` **已接线** |
| **F-9** | ✅ 口径不一 | Tech-Spec §25 增加「已实现但默认不可启用」等状态标签 |
| **F-10** | ✅ 仍成立 | ops 一次性脚本归档 — **非阻塞**，未在本轮强制搬迁 |
| **F-11** | 记录项 | 无需改代码 |
| **F-12** | 随 F-1 关闭 | README 同步为「已挂载」 |

### 同步更新的文档

- `Regent-Technical-Spec.md` §21 / §25
- `docs/deployment.md`
- 根 `README.md`、`core/README.md`、`core/src/regent/api/README.md`
- `docs/README.md`（本审计状态）
- `Regent-PRD.md`（日期/交叉引用，如需要）

### 验证命令

```text
python -m pytest tests/architecture/test_regent_definition_freeze.py -q
# 新版 FastAPI 用 _IncludedRouter，须查 OpenAPI，勿只扫 app.routes.path
PYTHONPATH=core/src python -c "from regent.api.main import create_app; p=set(create_app().openapi()['paths']); assert any('human-tasks' in x for x in p); assert '/v1/uploads' in p"
```

---

## 8. 修复验收复检（2026-07-31 二次 — 独立复核）

> 方法：两路独立复检 + 关键论断由第三方逐行读码裁决。本节只记录**已核实**的结论。

### 8.1 F-1…F-12 验收结论

| # | §7 声明 | 复检判定 | 证据 |
|---|---|---|---|
| F-1 | 已挂载 5 个 router | ✅ 属实 | `api/main.py:266-294` 共 28 处 `include_router`，含 `human_tasks:290` / `uploads:291` / `webhooks:292` / `reports:293` / `public_deploy:294` |
| F-2 | 冻结门禁路径已修 | ✅ 属实，且**超出声明** | `test_regent_definition_freeze.py:13-14` 已指向无后缀 CURRENT 基线；新增 `test_freeze_guard_paths_exist:19-24` 把「路径错」从 `FileNotFoundError` 升级为清晰断言 —— 这是对 F-2 根因的正确修法 |
| F-3 | agent 命令走沙箱 | ⚠️ **仅 production 名义闭环**，见 N-3 / N-5 | `tools.py:232-236` 无 sandbox 即 `RuntimeError`；6 处构造点全部注入 `build_agent_sandbox()`（`generator.py:95`、`subagent.py:70`、`code_generator.py:325`、`delivery_batch_pipeline.py:394/465`） |
| F-4 | §21 双列对照表 | ✅ 属实 | `Regent-Technical-Spec.md:663-692` |
| F-5 | deployment Path A/B | ✅ 属实 | `docs/deployment.md:14-27 / 28-37` |
| F-6 | transcript 不再静默 | ✅ 属实 | `generator.py:163-178` sidecar + 阻断；其余 `except` 均带 `logger.exception`（:202/:232/:326） |
| F-7 | 审计后已不成立 | ✅ 属实 | `api/app_projects.py:106`、`console/src/lib/api.ts:100`、`ArtifactPanel.tsx:124` |
| F-8 | verdict 已接线 | ✅ 属实 | `execution_orchestrator.py:3719/3726/3734`；`Tech-Spec:767,796` 已更新 |
| F-9 | §25 状态标签 | ✅ 属实 | `Regent-Technical-Spec.md:782-789` 四档标签表 |
| F-10 | ops 未搬迁（非阻塞） | ✅ 属实 | `ops/` 根仍有约 30 个一次性脚本 |
| F-11 | 记录项 | ✅ | `docs/adr/README.md:11`；`capabilities/bootstrap/` 实为 3 个 |
| F-12 | README 同步 | ⚠️ **部分未闭环** | 根/core/api README 已同步；但 12 份 README 仍描述已修复问题，见 §8.4 |

### 8.2 修复引入的新问题

| # | 严重度 | 问题 | 证据 |
|---|---|---|---|
| **N-3** | 🔴 | **docker 模式下 agent 命令实际不会被执行**。`workspace_exec_command`（`sandbox.py:163-191`）拼出 `docker run … <image> sh -lc <cmd>`，**未传 `--entrypoint`**；而 `sandbox_image` 默认 `regent-python-web-v1-sandbox:1`（`config.py:16`）对应 `capabilities/bootstrap/sandbox/Dockerfile:5` 的 `ENTRYPOINT ["python","/opt/sandbox/main.py"]`。实际执行的是 `python /opt/sandbox/main.py sh -lc "<cmd>"` —— 命令被当参数吞掉。该镜像是**构建沙箱**，被直接复用为**通用命令沙箱** |
| **N-3b** | 🔴 | **docker-in-docker 未打通**。`compose.yaml` 无 `docker.sock` 挂载、无 `privileged`（grep 无命中），容器化 worker 无法 `docker run`；`--mount src=<宿主路径>` 在容器内也对不上 |
| **N-2** | 🟠 | **生产运维路径未打通**。`config.py:72` 要求 production 必须 `sandbox_mode=docker`，但 `compose.yaml` / `docs/deployment.md` / 任何 `.env` 均未提供 `REGENT_SANDBOX_MODE`（grep `*.{yaml,yml,md,env,conf}` 仅命中文档说明文字）。**fail-closed 本身是对的**，缺的是运维侧配套 |
| **N-4** | 🟠 | **`pip` / `curl` 自动开网，绕过 egress 治理**。`tools.py:113` `_NETWORK_PREFIXES=("pip ","curl ")` → `:237` 置 `allow_network=True` → `sandbox.py:162` `--network bridge`。无 Permit、无 egress proxy、无域名白名单；而同文件 `DockerDependencyMaterializer:472-473` 明确持有 `egress_proxy` + `permit_validator`。与 Tech-Spec §13:450 / §19:626「网络默认拒绝」不一致 |
| **N-6** | 🟠 | **transcript 阻断缺错误码与重试分级**。`generator.py:163-178` 把 DB 持久化失败升级为 `DeliveryRejection`，reason 为 `f"transcript-persist-failed: {exc}"[:400]` 裸拼异常，无稳定 `error_code`（与 Tech-Spec §21:661 不符），且不区分可重试/不可重试 —— DB 抖动会把**已生成成功**的交付判为拒绝 |
| **N-5** | 🟡 | dev/test 仍宿主执行。`build_agent_sandbox:450-463` 非 production 一律返回 `LocalSandboxDriver`，其 `exec_in_workspace:412-439` 仍是宿主 `create_subprocess_shell` 且 `del allow_network`。属**有意的分层防御**（生产由 `config.py:72` + `sandbox.py:452` 双重 fail-closed 拦截），但 F-3 的实质风险在开发环境仍在 |
| **N-1** | 🟡 | `config.py:80-91` canary 校验为**不可达死代码** —— 其条件是 `:72` 的真子集，`:72` 已先行抛错。**保护实际存在**（由更强的 `:72` 提供），仅代码冗余，宜删或改为断言 |

> 复检中一条子审计结论被推翻：有报告称「沙箱镜像仓内无 Dockerfile」。实际 `capabilities/bootstrap/sandbox/Dockerfile` 存在。真问题不是缺镜像，而是 **N-3 的 entrypoint 语义错配**。

#### 8.2a 补登记（2026-07-31 执行计划核实）

制定 CD-6 执行计划时复核代码，发现 **N-3 并非单点 bug，而是「构建沙箱被复用为通用命令沙箱」派生的一组问题**。以下两项与 N-3 同源、同批处置：

| # | 严重度 | 问题 | 证据 |
|---|---|---|---|
| **N-3c** | 🔴 | **uid 错配 → 沙箱内无写权限**。worker 容器以 `USER nobody`(65534) 运行（`core/Dockerfile:13`），agent `write_file` 在 worker 进程内落盘；沙箱容器强制 `--user 65532:65532`（`sandbox.py:181-182`）挂载同一 workspace → `pytest` / 编译 / 写文件均 EACCES。且镜像 `USER 65532` 未设 `HOME`，白名单首项 `pip`（`tools.py:132`）无可写 home/cache，必然失败 |
| **N-3d** | 🔴 | **容器路径被当宿主路径解析，且静默**。compose workspace 为绑定挂载 `/opt/regent/workspaces:/var/lib/regent/workspaces`（`compose.yaml:32`）；worker 内 `workspace.resolve()` 得 `/var/lib/regent/...`，被 `sandbox.py:186` 直接作为 `--mount src` 交给宿主 daemon。宿主无此路径 → **挂空目录**，命令"成功"但看不到任何文件 |

> **对处置顺序的影响**：§8.6 第 1 项（N-3/N-3b）若按原范围只修 entrypoint，会进入更糟状态 —— 命令开始执行，但在错误的空目录里、以无权限身份运行，失败信息指向业务代码而非环境。三者必须绑定验收。
> 另据 `build_agent_sandbox:459-462` 复用 `sandbox_image`（构建镜像）—— 这是 N-3 根因，故修法取**专用 agent 命令镜像**，而非仅补 `--entrypoint`。
> 工作包拆解见 [`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md)。

### 8.3 测试守卫缺口（🔴 本轮最大风险）

grep `tests/` 关键词 `build_agent_sandbox` / `command_sandbox` / `sandbox_mode` / `regent_smoke_probe` / `transcript-persist` / `human-tasks` / `/v1/uploads` —— **全部 0 命中**。

| 修复 | 测试守卫 |
|---|---|
| 5 个 router 挂载 | ❌ 无 → F-1 会原样漂移回去 |
| 沙箱注入（禁裸 `WorkspaceToolkit(root)`） | ❌ 无架构测试；测试自身全部裸构造（`test_agentic_generation.py:48/72/176`） |
| production 禁 local | ❌ `tests/unit/test_config.py` 仅 8 行，只测 dev 默认值 |
| transcript 不可丢 | ❌ 无 |
| smoke 探针路径 | ❌ 测试全部 `run_smoke=False`（`test_agentic_generation.py:88/216`），改后路径零覆盖 |
| verdict 接线 | ⚠️ `test_delivery_state.py:129-133` 用 `assert "decide_delivery_verdict(" in src` 源码字符串检查 —— 正是 Tech-Spec §23:721 明令禁止的方式 |

**结论：本轮修复全部没有回归防护。** F-2 的教训（门禁自身漂移）尚未转化为制度 —— 修复了 bug，但没有守住修复。

### 8.4 README 反向漂移

上一轮为记录偏差而写入 README 的「已知偏差」表，在修复后**反向成为新的漂移源**：12 份 README 仍在描述已修复的问题（详见 §8.6 处置清单）。这是本项目「文档—实现漂移」的镜像形态，已在本轮一并修正。

### 8.5 审计可追溯性受损

`doc-implementation-alignment-audit-2026-07-31.md:50-52` 将 §3–§6 正文删除并改为「见 git 历史」，导致 F-8…F-12 的**原始判据在文件内不可复核**。审计文档应自证，建议恢复摘要或以附录保留原始表格。

### 8.6 处置清单（按严重度）

| 序 | 事项 | 类型 |
|---|---|---|
| 1 | **N-3 / N-3b**：为 agent 命令沙箱提供专用镜像与 `--entrypoint`；打通容器化 worker 的 docker 访问（或改用同容器内的隔离方案） | 修 bug（阻断生产） |
| 2 | **补测试守卫**：router 挂载完整性、禁裸 `WorkspaceToolkit`、production 禁 local、transcript 不可丢 | 制度（防再漂移） |
| 3 | **N-2**：`compose.yaml` / `deployment.md` 补 `REGENT_SANDBOX_MODE` 与 docker 前提 | 补文档/配置 |
| 4 | **N-4**：`pip`/`curl` 开网纳入 Permit + egress proxy，对齐 `DockerDependencyMaterializer` | 修规范违反 |
| 5 | **N-6**：transcript 拒绝赋稳定 `error_code`，区分可重试 | 修 bug |
| 6 | **N-1** 删死代码；**§8.5** 恢复审计正文；**F-10** ops 归档 | 清理（非阻塞） |
