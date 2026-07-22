# Bootstrap Runtime Profile v1

状态：Frozen for P1  
名称：python-web-v1  
日期：2026-07-18

## 定位

这是 P1 唯一预置的执行 ABI，不是产品模板。它限制首轮可生成 App 的运行环境，但不预设用户、业务模型、页面、功能或商业模式。

## 契约

- Python 3.12；
- ASGI HTTP 服务；
- 监听容器端口 8080；
- 提供 GET /health/live 与 GET /health/ready；
- 使用 pyproject.toml 和锁定依赖清单；
- 独立 src、tests、migrations、static/templates 可选目录；
- 独立 PostgreSQL 可选；
- Dockerfile 使用非 root 用户；
- stdout/stderr 结构化日志；
- SIGTERM 优雅退出；
- 不 import regent；
- 不访问 Core 数据库；
- 配置仅来自声明的环境变量；
- 生产 Secret 不进入镜像。

## 必需生成制品

- README；
- pyproject.toml；
- lockfile；
- Dockerfile；
- src；
- tests；
- 健康检查；
- 配置示例；
- 数据库迁移，如需要；
- source manifest。

## VerifyBuild 命令

- 依赖完整性验证；
- 格式检查；
- 类型检查；
- 单元与集成测试；
- 架构边界测试；
- 容器健康 smoke test；
- Secret 和依赖扫描。

## 不支持

需要 Node-only、移动原生、桌面 GUI、GPU 服务或其他运行时的候选，在 P1 必须选择其他候选、请求人类扩充 Runtime Profile，或进入 BLOCKED；不得静默硬编码支持。
