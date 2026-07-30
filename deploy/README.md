# Deploy（部署配置）

存放运行时部署相关的配置片段，配合仓库根 `compose.yaml` 与 `core/Dockerfile` 使用。

## 当前内容

- `squid/squid.conf` — **出口代理（Egress Proxy）配置**。
  - 约束 Worker 与能力对外的 HTTP 出口（白名单 / ACL），实现 fail-closed 出口策略。
  - Core 的证据采集（`allowlisted-http-source-v1` 等能力）与对外调用均经此代理，确保只访问 Goal 授权 URL 与认证默认 feed。
  - 历史上曾因 ACL 过严导致 403（`ProxyError`），修正后应保持"默认拒绝、显式放行"。

## 相关

- `compose.yaml`：编排 `regent-api` / `regent-worker` / `regent-egress`（Squid）/ `regent-postgres`。
- `core/Dockerfile`、`core/Dockerfile.incremental`：Core 镜像构建。
- `apps/regent-console/Dockerfile` + `nginx.conf`：控制台生产镜像。
