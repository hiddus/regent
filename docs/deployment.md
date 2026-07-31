# Deployment

Regent 有两套部署路径，**不要混读为同一套**：

| 路径 | 入口 | 用途 |
|---|---|---|
| **A. 生产 / 服务器手工编排** | 本节「Current S0 deployment」 | 线上主机 `docker run` + 自建网络/卷 |
| **B. 本地 Compose** | 仓库根目录 [`compose.yaml`](../compose.yaml) | 开发者本机一键起 postgres/api/worker |

二者服务名、网络名、数据卷名、端口策略**不相同**。以实际使用的编排文件为准。

---

## Path A — Current S0 deployment（服务器）

- Host: `118.31.171.159`
- Install directory: `/opt/regent`
- API container: `regent-api`
- Worker container: `regent-worker`
- Database container: `regent-postgres`
- Docker network: `regent-net`
- Database volume: `regent-postgres-data`
- API port: TCP 8000
- **Agent 沙箱（CD-6）**：宿主或 Path A worker 使用 `REGENT_SANDBOX_MODE=docker`、`REGENT_AGENT_SANDBOX_IMAGE=regent-agent-exec-v1:1`；先构建 agent-exec 镜像（见下方矩阵）

PostgreSQL is **not** published on a host port in this path. Its randomly generated password is
stored on the server in `/opt/regent/.deploy.env` with owner-only permissions.

## Path B — Local Compose（`compose.yaml`）

服务名：`postgres` / `api` / `worker`（**不是** `regent-*` 前缀）。

默认端口（开发便利，**非生产安全基线**）：

- API：`8000:8000`
- PostgreSQL：`5432:5432`（**发布到宿主**，便于本机客户端连接）

安全提示：若用 Compose 接近生产环境，应去掉或条件化 `postgres.ports`（例如仅在设置 `REGENT_PG_PUBLISH=1` 时发布），并使用独立网络与强密码。勿把 Path B 的端口暴露策略套用到 Path A。

### Agent 沙箱支持矩阵（CD-6）

| 组合 | `REGENT_SANDBOX_MODE` | 要求 | 结果 |
|---|---|---|---|
| Path A 宿主/容器 worker | `docker` | 已构建 `regent-agent-exec-v1:1`；worker 挂 `docker.sock` + `--group-add docker`；`REGENT_HOST_PATH_MAP=/opt/regent=/opt/regent`（S0 同路径绑定） | ✅ 已在 S0（2026-07-31）验证 |
| Path B compose（默认） | `local` | 无 docker.sock | ✅ 开发支持（非生产） |
| Path B compose + docker | `docker` | **必须** `REGENT_HOST_PATH_MAP=/var/lib/regent=/opt/regent` **且**挂载 docker.sock（Owner 风险接受）或改用宿主 worker | 缺 map → **fail-closed**（N-3d） |
| `environment=production` + `local` | — | — | 启动/配置 **ValueError** |

构建 agent 命令镜像：

```bash
docker build -t regent-agent-exec-v1:1 -f capabilities/bootstrap/agent-exec/Dockerfile capabilities/bootstrap/agent-exec
```

构建沙箱（依赖物化 / 构建门）仍用：

```bash
docker build -t regent-python-web-v1-sandbox:1 -f capabilities/bootstrap/sandbox/Dockerfile capabilities/bootstrap/sandbox
```

## Health checks

```text
GET /health/live
GET /health/ready
```

## Cloud firewall

The host firewall allows TCP 8000. Alibaba Cloud Security Group or Cloud Firewall
must also allow inbound TCP 8000 before the API is reachable from the public
internet. Prefer restricting the source CIDR during development.

## Image build

The default package source is PyPI. An alternate mirror can be selected without
editing the Dockerfile:

```text
docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  -t regent-core:0.1.0 -f core/Dockerfile .
```
