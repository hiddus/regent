# CD-6 执行级修复计划 · 沙箱真执行 + 回归守卫

> 状态：**ACTIVE（执行级）**（2026-07-31；**S0 Docker 验证通过**）  
> 从属：[`conversational-delivery-next-plan-2026-07-31.md`](./conversational-delivery-next-plan-2026-07-31.md) §2。  
> 服务器验证：`ops/verify_cd6_on_server.py`（镜像/三联/N-3c）；worker e2e：`ops/_resync_cd6_and_e2e.py`（需 worker `--group-add docker`）。  
> S0 已设：`REGENT_SANDBOX_MODE=docker`、`REGENT_AGENT_SANDBOX_IMAGE=regent-agent-exec-v1:1`、`REGENT_HOST_PATH_MAP=/opt/regent=/opt/regent`。  
> 门禁：CD-6 主机验收已完成；开 canary 仍须 CD-7 全绿。

---

## 0. 本轮计划为何要在 next-plan 之上再展开

next-plan §2 把 CD-6 概括为「传 `--entrypoint ""` 或专用镜像；compose docker.sock 或同容器隔离；补 `REGENT_SANDBOX_MODE`；补测试守卫」。核实代码后发现**该范围不足以让 docker 模式真正跑通**：即使按 next-plan 修完 entrypoint 与 docker.sock，**写路径与可见 workspace 仍不可信**（N-3c/N-3d）；`echo ok` 可能假绿，原因是两条尚未登记的缺陷。

| 新编号 | 严重度 | 问题 | 证据 |
|---|---|---|---|
| **N-3c** | 🔴 | **uid 错配导致沙箱内无写权限**。worker 容器以 `USER nobody`(65534) 运行（`core/Dockerfile:13`），agent 的 `write_file` 在 worker 进程内落盘，文件属主 65534；而沙箱容器强制 `--user 65532:65532`（`sandbox.py:181-182`）挂载同一 workspace。65532 对 65534 拥有的目录**无写权限** → **写盘类**命令（`pytest` 建 cache、编译产物、生成文件）会 EACCES。只读/`echo` 类命令**仍可能 exit=0（假绿）**，故不得以 `echo ok` 单独验收。另：镜像 `USER 65532` 且未设 `HOME`，`pip` 无可写 home/cache，`pip install` 亦会失败。 |
| **N-3d** | 🔴 | **容器化 worker 的 `--mount src` 是容器路径，daemon 按宿主路径解析**。compose 中 workspace 为绑定挂载 `/opt/regent/workspaces:/var/lib/regent/workspaces`（`compose.yaml:32`），worker 内 `workspace.resolve()` 得到 `/var/lib/regent/workspaces/...`，而 `sandbox.py:186` 直接把它作为 `--mount src` 交给宿主 daemon。宿主无此路径 → 挂空目录或报错，**且常是静默的**（挂空目录时命令"成功"但看不到任何文件）。 |

**结论：N-3 不是单点 bug，是「构建沙箱被复用为通用命令沙箱」这一错配派生出的一组问题（N-3 / N-3c / N-3d）。** 只修 entrypoint 会进入更糟的状态 —— 命令开始「执行」了，但写路径与可见 workspace 仍不可信（空目录 / EACCES），失败信息易被误判为业务代码问题。

---

## 1. 工作包总表

| 包 | 名称 | 解决 | 依赖 | 类型 |
|---|---|---|---|---|
| **CD-6.1** | 专用 agent 命令镜像 + entrypoint | N-3 | — | 修 bug |
| **CD-6.2** | 执行身份与写权限一致 | N-3c | 6.1 | 修 bug |
| **CD-6.3** | 容器↔宿主路径映射 + fail-closed | N-3d | 6.1 | 修 bug |
| **CD-6.4** | docker 访问打通或支持矩阵定界 | N-3b | 6.3 | 运维/文档 |
| **CD-6.5** | 回归测试守卫（6 条 + 1 项整改） | §8.3 | 6.1–6.3 | 制度 |
| **CD-6.6** | 运维配套：环境变量与部署文档 | N-2 | 6.4 | 配置/文档 |
| **CD-6.7** | 死代码清理 | N-1 | — | 清理 |

严格顺序：**6.1 → 6.2 → 6.3 → 6.4 → 6.5 → 6.6**（6.7 任意时点）。
6.5 可与 6.1–6.3 同批提交，但**不得晚于 6.6**——否则重复 F-2 教训（修了 bug 没守住修复）。

---

## 2. CD-6.1 · 专用 agent 命令镜像 + entrypoint

**现状**（`sandbox.py:187-190`）：

```python
    self._image,
    "sh",
    "-lc",
    shell_command,
```

镜像 `capabilities/bootstrap/sandbox/Dockerfile:5` 为 `ENTRYPOINT ["python", "/opt/sandbox/main.py"]`，实际执行 `python /opt/sandbox/main.py sh -lc "<cmd>"`，命令被当参数吞掉。
`build_agent_sandbox`（`sandbox.py:459-462`）复用 `settings.sandbox_image`，与构建沙箱**共用同一镜像**。

**两个方案**

| | A · 仅加 entrypoint（hotfix） | B · 专用镜像（推荐终态） |
|---|---|---|
| 改动 | argv 插 `--entrypoint sh`，命令改 `[..., "--entrypoint", "sh", image, "-lc", cmd]` | 新增 `capabilities/bootstrap/agent-exec/Dockerfile` + 配置 `agent_sandbox_image` |
| 优点 | 一行级，立即可验 | 消除根因；可按 agent 白名单裁剪工具链；可内置 `HOME` / pip 配置（连带缓解 N-3c、N-4） |
| 缺点 | **保留「构建镜像当命令沙箱」的错配**；镜像内工具链是按构建需求装的，与 agent 白名单不对应；`--entrypoint ""` 在旧版 docker 行为不一致，须用 `--entrypoint sh` | 需要新增镜像构建与分发步骤 |

**建议：直接做 B，A 仅在需要当日验证时作为临时补丁，且不得作为 CD-6.1 的验收形态。** 理由：N-3 的根因就是镜像复用，A 修掉症状但把根因留在原地，下一次改构建镜像时会再次打断 agent 路径。

**具体改造点**

1. 新增 `capabilities/bootstrap/agent-exec/Dockerfile`：不设 `ENTRYPOINT`（或 `ENTRYPOINT ["sh","-lc"]` 二选一，与 argv 保持单一约定）；`ENV HOME=/tmp PIP_CACHE_DIR=/tmp/pip XDG_CACHE_HOME=/tmp/cache`；工具链覆盖 `tools.py:131-147` 白名单前缀（至少 `python` / `pytest` / `pip`；其余按 base image 审计）。
2. `config.py` 新增 `agent_sandbox_image`（默认 `regent-agent-exec-v1:1`），**与 `sandbox_image` 分离**。
3. `sandbox.py:459-462` `build_agent_sandbox` 改读 `agent_sandbox_image`。
4. `workspace_exec_command`（`sandbox.py:154-191`）显式声明 entrypoint，不依赖镜像默认值。

**验收**
- 单测：`workspace_exec_command` argv 含 `--entrypoint`，且 `--network none` / `--cap-drop ALL` / `--security-opt no-new-privileges` / `--user` 未被削弱。
- 集成（`skipif` 无 docker）：`run_command("echo ok")` → `exit=0`、stdout 含 `ok`。

---

## 3. CD-6.2 · 执行身份与写权限一致（N-3c）

**问题链**：worker 进程 uid 65534 写文件 → 沙箱容器 uid 65532 读写同目录 → EACCES。

**方案（择一，需 Owner 拍板）**

| 方案 | 做法 | 权衡 |
|---|---|---|
| **B1 对齐 uid**（推荐） | 沙箱 `--user` 取运行时 uid（Linux `os.getuid()`），配置项 `agent_sandbox_uid` 可覆盖 | 简单、无属主变更；但沙箱不再固定非特权 uid，需断言 uid ≠ 0 |
| B2 统一为 65532 | worker 容器改 `USER 65532`，宿主 `/opt/regent/*` chown 65532 | 一致性最好；改动触及部署既有数据目录属主 |
| B3 共享 gid | workspace 目录 `g+rwxs`，两侧同 gid | 最小侵入；setgid 语义易在运维中丢失 |

**必做（与方案无关）**：镜像内设 `HOME=/tmp`，否则 `pip`（白名单首项）在只读 home 下必失败。

**验收**
- docker 模式下 `python -c "open('probe','w').write('1')"` 成功，且宿主可见该文件。
- `pytest` 能创建 `.pytest_cache`（不报 EACCES）。
- 断言沙箱 uid ≠ 0（不得为修权限而退化成 root）。

---

## 4. CD-6.3 · 容器↔宿主路径映射（N-3d）

**改造点**

1. 新增配置 `host_path_map`（`REGENT_HOST_PATH_MAP`），格式 `容器前缀=宿主前缀`，可多组分号分隔。compose 场景取值 `/var/lib/regent=/opt/regent`。
2. `workspace_exec_command` 在拼 `--mount src=` 前做前缀翻译。
3. **fail-closed**：若检测到运行在容器内（`/.dockerenv` 存在或 cgroup 命中）**且** `sandbox_mode=docker` **且** 未配置 `host_path_map` → 抛错拒绝执行，而非静默挂空目录。这条比映射本身更重要 —— N-3d 的危害在于它**不报错**。

**验收**
- 单测：给定 map，`--mount src=` 为宿主路径。
- 单测：容器内 + docker 模式 + 无 map → 抛出明确错误（含配置项名）。
- 集成：容器化 worker 内 `run_command("ls")` 能看到 workspace 中由 `write_file` 写入的文件。

---

## 5. CD-6.4 · docker 访问打通或定界（N-3b）

compose 未挂 `/var/run/docker.sock`、未设 `privileged`（`compose.yaml:26-35` 无相关键）。两条路线：

| 路线 | 做法 | 适用 |
|---|---|---|
| **打通 DinD**（配合 6.3） | worker 挂 `docker.sock` + `group_add: [docker gid]` | Path B compose 部署 |
| **明确定界** | 声明「容器化 worker 不支持 agent 沙箱，仅 Path A 宿主 worker 支持」，并让 `build_agent_sandbox` 在该组合下 fail-closed | 若不接受 sock 暴露的权限提升风险 |

**注意**：挂 `docker.sock` 等于把宿主 docker 控制权交给 worker 容器，安全性上**弱于**当前设计意图。若选此路线，需在 [`docs/appendices/Security-Tenancy-and-Recovery.md`](./appendices/Security-Tenancy-and-Recovery.md) 补一条风险接受说明，不能默默加；并写清 `group_add` / socket 权限，以及 api 进程是否也会起沙箱（若会，须同等挂载或 fail-closed）。

next-plan 另提「同容器隔离」第三选项：本执行计划**默认不展开**（与 DinD 相比改动面更大）。若 Owner 选择该路线，须另开工作包，不得默认为 CD-6.4 已覆盖。

**验收**：二选一并落文档——要么容器内跑通一条沙箱命令（含写文件可见），要么 `deployment.md` 出现明确的支持矩阵且不支持组合会启动/首次 exec 即报错（含配置项名）。

---

## 6. CD-6.5 · 回归测试守卫（本批最高制度价值）

审计 §8.3：`build_agent_sandbox` / `command_sandbox` / `sandbox_mode` / `regent_smoke_probe` / `transcript-persist` / `human-tasks` / `/v1/uploads` 在 `tests/` 全部 0 命中。**本轮所有修复目前均无回归防护。**

| # | 守卫 | 断言方式 | 防止 |
|---|---|---|---|
| T1 | router 挂载完整性 | 读 `create_app().openapi()["paths"]`，断言含 `human-tasks` / `/v1/uploads` / webhooks / reports / public-deploy。**不得扫 `app.routes`**（新版 FastAPI 用 `_IncludedRouter`，扫不到） | F-1 复发 |
| T2 | 禁裸 `WorkspaceToolkit` | 架构测试 AST 扫 `core/src` **与 `tests/`**，构造时必须传 `command_sandbox`（白名单显式登记） | F-3 复发 |
| T3 | production 禁 local | **分层断言**：`Settings(environment=production, sandbox_mode=local)` → `ValueError`（`config.py:70-76`）；可选另测 `build_agent_sandbox` 在生产+local → `RuntimeError`（`sandbox.py:452-456`）。勿混写异常类型。现 `tests/unit/test_config.py` 仅测 dev 默认值 | 配置回退 |
| T4 | transcript 不可丢 | mock 持久化抛错 → 断言 `DeliveryRejection` + sidecar 落盘 | F-6 复发 |
| T5 | smoke 探针路径 | 至少一条 `run_smoke=True` 用例（现 `test_agentic_generation.py:88/216` 全为 `False`）；断言 `.regent_smoke_probe.py` 在 `verification.py` `finally` 清理，且不进 `snapshot_files` / artifact 快照 | 探针混入交付物 |
| T6 | 沙箱 argv 契约 | 断言含 `--entrypoint`、`--network none`（默认）、`--cap-drop ALL`、`--user`；映射生效 | N-3 / N-3c / N-3d 复发 |

**同批整改**：`tests/unit/application/test_delivery_state.py:129-133` 使用 `assert "decide_delivery_verdict(" in src` 源码字符串断言，正是 `Regent-Technical-Spec.md` §23:721 明令禁止的方式，改为行为断言。

---

## 7. CD-6.6 · 运维配套（N-2）与 6.7 清理（N-1）

- `compose.yaml` 的 `api` / `worker` **声明** `REGENT_SANDBOX_MODE`，并与支持矩阵一致：Path B 开发默认可为 `local`；**勿**在无 docker.sock / 无 `host_path_map` 时默认写成 `docker`（否则 Path B 全红）。
- **补全**已有 `.env.example`（非新建）：列出 `REGENT_ENVIRONMENT` / `REGENT_SANDBOX_MODE` / `REGENT_HOST_PATH_MAP` / `REGENT_AGENT_SANDBOX_IMAGE`（现有文件已含部分 `SANDBOX_IMAGE` 类项）。
- `docs/deployment.md` Path A/B 各补：docker 前提、镜像构建步骤、支持矩阵（含「不支持组合 → 启动或首次 exec fail-closed」）。
- 顺带（已在 `deploy/README.md` 登记）：`compose.yaml:13` 发布 5432 且口令为 `regent/regent`，建议改 `REGENT_PG_PORT` 可选发布。
- **N-1**：`config.py:80-91` canary 校验为不可达死代码（条件是 `:72` 的真子集），删除或降级为断言注释。**保护本身由 `:72` 提供，删除不降低安全性** —— 但需在同一 PR 内附 T3 用例，避免"删了才发现没人测"。

---

## 8. Owner 可选加速项（默认仍属 CD-7.5）

next-plan 把 **N-4**（`pip`/`curl` 自动开网绕过 egress 治理）排在 **CD-7.5**。完整 Permit + egress 治理**默认不纳入 CD-6 出口**，以免执行范围越权。

**CD-6 主路径（推荐默认）**：验收时**临时移除 `_NETWORK_PREFIXES`**（默认全程 `--network none`），待 CD-7.5 再按治理要求重新开启。这样 CD-6.1 点亮 docker 真执行时，不会出现「裸开网能力刚生效就投入实验」的窗口。

**Owner 可选加速**：若希望与 CD-6.1 专用镜像同批做完 N-4（镜像层 `PIP_INDEX_URL`/CA + 对齐 `DockerDependencyMaterializer:472-473` 的 egress/Permit），须显式拍板并改 next-plan CD-7.5 状态；否则按主路径执行。

二者取其一，不可两者皆不做。

---

## 9. 风险登记

| 风险 | 触发 | 缓解 |
|---|---|---|
| 只修 N-3 未修 N-3c/N-3d | 按 next-plan 原范围施工 | 本文件已将三者绑定为同一验收；6.1 单独绿不算 CD-6 绿 |
| 挂 `docker.sock` 削弱隔离 | 选 DinD 路线 | 需附风险接受说明进安全附录，不得默默加 |
| 为解权限退化成 root | 处理 N-3c 图省事 | T6 断言 uid ≠ 0 |
| 修复无守卫再次漂移 | 6.5 被推迟 | 6.5 不得晚于 6.6 合入 |
| docker 集成测在 CI 无 daemon 上假绿 | 全量 `skipif` | argv 契约测（T6）必须**无条件运行**，仅真实执行测允许 skip |

---

## 10. CD-6 出口判据（全绿方可进 CD-7）

1. docker 模式下 `echo ok` / 写文件 / `pytest` 三条命令均真实执行且结果可见于宿主 workspace。
2. 容器内缺 `host_path_map` 时 fail-closed 报错，不静默挂空目录。
3. T1–T6 全部进 CI，其中 T6 无条件运行。
4. `deployment.md` 存在明确支持矩阵；`.env.example` 可直接用于 Path B 起服务。
5. 上述任一未达成 → `canary_gate` 保持 `False`，CD-8 不开窗。
6. **本批不含** CD-7.1–7.4（技 P1 marker/事务/画像/预算）与 GQ-3 开窗；N-4 完整治理默认留 CD-7.5（见 §8）。
