# core/src/regent/infrastructure

基础设施：数据库（database.py）、Artifact Store（artifact_store.py）、代码生成器（code_generator.py）、部署（deployment.py）、证据采集（evidence_capability.py / evidence_sources.py）、能力确保（delivery_review_capability.py / product_surface_capability.py）、Webhook 连接器、报告生成器。

## 沙箱现状（F-3 已闭环，但存在后续缺陷）

构建路径与 agent 工具路径**均已经过 sandbox driver**：`sandbox.py:442 build_agent_sandbox()` 为 agent 命令构造沙箱，6 处 `WorkspaceToolkit` 构造点全部注入；`agent/tools.py:232` 在缺少 `command_sandbox` 时直接 `RuntimeError` 拒绝执行，不再回退宿主进程。

生产已 fail-closed：`config.py:72` 在 `environment=production` 且 `sandbox_mode != docker` 时拒绝构造 `Settings`，`sandbox.py:452` 再做一次运行期拦截。

> 🔴 **N-3：docker 模式下 agent 命令实际不会被执行。**
> `workspace_exec_command`（`sandbox.py:163-191`）拼出 `docker run … <image> sh -lc <cmd>`，但**未传 `--entrypoint`**。默认 `sandbox_image`（`config.py:16` = `regent-python-web-v1-sandbox:1`）对应 `capabilities/bootstrap/sandbox/Dockerfile:5` 的 `ENTRYPOINT ["python","/opt/sandbox/main.py"]`，于是实际执行 `python /opt/sandbox/main.py sh -lc "<cmd>"` —— 命令被当作参数吞掉。
> 根因：该镜像是**构建沙箱**，被直接复用为**通用命令沙箱**。修复需为 agent 命令提供专用镜像或显式 `--entrypoint`。
>
> 🔴 **N-3b：容器化 worker 无法 `docker run`。** `compose.yaml` 未挂载 `docker.sock`、未设 `privileged`；且 `--mount src=<宿主路径>` 在容器内路径对不上。
>
> 🟠 **N-4：`pip` / `curl` 自动开网绕过 egress 治理。** `agent/tools.py:113` `_NETWORK_PREFIXES` → `:237` `allow_network=True` → `sandbox.py:162` `--network bridge`，无 Permit、无 egress proxy、无域名白名单；而同文件 `DockerDependencyMaterializer:472-473` 是有 `egress_proxy` + `permit_validator` 的。与 Technical-Spec §13:450 / §19:626「网络默认拒绝」不一致。
>
> 🟡 **N-5：dev/test 仍是宿主执行。** `build_agent_sandbox:450-463` 在非 production 返回 `LocalSandboxDriver`，其 `exec_in_workspace:412-439` 使用宿主 `create_subprocess_shell` 且忽略 `allow_network`。属有意的分层防御，但开发环境的实质风险仍在。

另注：`application/app_delivery.py:199-202` 与 `worker/main.py:265-268` 存在**两处独立的 sandbox_mode 分支**，即 API 进程也会直接起沙箱。

## 已验证对齐

`models.py:1469` 的 `ExternalOperationModel`（`operation_key` 唯一约束 + `dispatch_generation` + `local_fencing_token`）与 Technical-Spec §9/§10 及《Durable-Execution-and-External-Effects》附录一致。

## 目录内容

文件：
- `__init__.py`
- `aar1_models.py`
- `artifact_store.py`
- `browser_journey.py`
- `code_generator.py`
- `database.py`
- `delivery_review_capability.py`
- `deployment.py`
- `evidence_capability.py`
- `evidence_sources.py`
- `html_evidence.py`
- `models.py`
- `product_surface_capability.py`
- `public_deployment.py`
- `report_generators.py`
- `research_report.py`
- `run_reconciler.py`
- `sandbox.py`
- `search_evidence.py`
- `self_improvement_sandbox.py`
- `static_app_publisher.py`
- `webhook_connector.py`
- `workspace_writer.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
