# Deploy（部署配置）

存放运行时部署相关的配置片段，配合仓库根 `compose.yaml` 与 `core/Dockerfile` 使用。

## 当前内容

- `squid/squid.conf` — **出口代理（Egress Proxy）配置**。
  - 约束 Worker 与能力对外的 HTTP 出口（白名单 / ACL），实现 fail-closed 出口策略。
  - Core 的证据采集（`allowlisted-http-source-v1` 等能力）与对外调用均经此代理，确保只访问 Goal 授权 URL 与认证默认 feed。
  - 历史上曾因 ACL 过严导致 403（`ProxyError`），修正后应保持"默认拒绝、显式放行"。

## 相关

> **注意（一致性修正）**：`squid/squid.conf` 是一份**参考/待接入**的出口代理配置样例。默认 `compose.yaml` 当前仅编排 `regent-api` / `regent-worker` / `regent-postgres` 三项服务，**未包含 `regent-egress`（Squid）服务**，出口代理尚未接入默认部署路径。fail-closed 出口策略（Worker 与能力对外 HTTP 经 Squid 白名单）目前属于**规划态，默认部署不生效**，需显式接入编排后方可启用。

- `compose.yaml`：当前编排 `api` / `worker` / `postgres` 三项服务（无 `regent-egress`）。
- `squid/squid.conf`：出口代理参考配置，**默认未接入** `compose.yaml`。
- `core/Dockerfile`、`core/Dockerfile.incremental`：Core 镜像构建。
- `apps/regent-console/Dockerfile` + `nginx.conf`：控制台生产镜像。

## ⚠️ 两套部署路径差异对照（F-5 已在 `deployment.md` 区分 Path A / Path B）

`docs/deployment.md:14` 的 Path A 是 S0 主机上的**手工 `docker run` 部署**，`:28` 的 Path B 才是仓库根的 `compose.yaml`。二者关键差异：

| 项 | `compose.yaml` | `docs/deployment.md` |
|---|---|---|
| PostgreSQL 端口 | `ports: ["5432:5432"]`（L13，**发布到宿主**） | L14「PostgreSQL is **not** published on a host port」 |
| 服务/容器名 | `api` / `worker` / `postgres` | `regent-api` / `regent-worker` / `regent-postgres` |
| 网络 | Compose 默认网络 | `regent-net` |
| 数据卷 | `regent-postgres` | `regent-postgres-data` |

其中**数据库端口暴露差异风险最高**：直接用 `compose.yaml` 起在公网可达的主机上，会把 PostgreSQL 暴露到 5432，且该实例使用 compose 内硬编码的弱口令（`regent/regent`）。

**使用前请确认你走的是哪一套。** 建议后续将 compose 的 5432 改为可选（如 `REGENT_PG_PORT`，默认不发布）。详见 [`docs/doc-implementation-alignment-audit-2026-07-31.md`](../docs/doc-implementation-alignment-audit-2026-07-31.md) F-5。

## 🔴 生产部署前置条件尚未打通（N-2 / N-3b）

`config.py:72` 现在对 `environment=production` **强制** `sandbox_mode=docker`，否则 `Settings()` 直接抛 `ValueError`，进程无法启动。

但当前**没有任何部署配置提供该变量**：`compose.yaml`、`docs/deployment.md` 与各 `.env` 模板中均无 `REGENT_SANDBOX_MODE`（全仓 grep `*.{yaml,yml,md,env,conf}` 仅命中说明文字）。

**首次以 `REGENT_ENVIRONMENT=production` 部署会启动失败。** 部署前必须：

1. 显式设置 `REGENT_SANDBOX_MODE=docker`；
2. 让 worker 容器能够访问 Docker daemon —— 当前 `compose.yaml` 未挂载 `/var/run/docker.sock`、未设 `privileged`，容器化 worker 无法执行 `docker run`；且 `sandbox.py:186` 的 `--mount src=<宿主路径>` 在容器内路径对不上；
3. 确认 agent 命令沙箱镜像可用 —— 见 N-3（默认镜像 entrypoint 与命令执行语义冲突），详见 `core/src/regent/infrastructure/README.md`。

> fail-closed 本身是正确设计（拒绝以不安全配置启动优于静默违规），缺的是运维侧配套。
