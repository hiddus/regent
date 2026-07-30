# Deploy（部署配置）

存放运行时部署相关的配置片段，配合仓库根 `compose.yaml` 与 `core/Dockerfile` 使用。

## 当前内容

- `squid/squid.conf` — **出口代理（Egress Proxy）配置**。
  - 约束 Worker 与能力对外的 HTTP 出口（白名单 / ACL），实现 fail-closed 出口策略。
  - Core 的证据采集（`allowlisted-http-source-v1` 等能力）与对外调用均经此代理，确保只访问 Goal 授权 URL 与认证默认 feed。
  - 历史上曾因 ACL 过严导致 403（`ProxyError`），修正后应保持"默认拒绝、显式放行"。

## 相关

> **注意（一致性修正）**：`squid/squid.conf` 是一份**参考/待接入**的出口代理配置样例。默认 `compose.yaml` 当前仅编排 `regent-api` / `regent-worker` / `regent-postgres` 三项服务，**未包含 `regent-egress`（Squid）服务**，出口代理尚未接入默认部署路径。fail-closed 出口策略（Worker 与能力对外 HTTP 经 Squid 白名单）目前属于**规划态，默认部署不生效**，需显式接入编排后方可启用。

- `compose.yaml`：当前编排 `regent-api` / `regent-worker` / `regent-postgres`（无 `regent-egress`）。
- `squid/squid.conf`：出口代理参考配置，**默认未接入** `compose.yaml`。
- `core/Dockerfile`、`core/Dockerfile.incremental`：Core 镜像构建。
- `apps/regent-console/Dockerfile` + `nginx.conf`：控制台生产镜像。
