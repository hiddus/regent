# Dependency Resolution and Sandbox Protocol v1

状态：Frozen for P1  
日期：2026-07-18

## 两阶段原则

Build 默认断网，但依赖获取需要受控网络，因此拆分为：

1. DependencyResolve/Materialize；
2. Offline VerifyBuild。

## DependencyResolve

- 输入是冻结 source hash、依赖意图和 Runtime Profile；
- 必须持有绑定域名、方法、配额和有效期的 Permit；
- 仅访问白名单包源；
- 禁止执行项目安装脚本；
- 解析并冻结确切版本和制品 hash；
- 生成 lockfile、dependency bundle 和 SBOM；
- 检查许可证、已知漏洞和禁止包；
- 保存请求、响应摘要和来源 Evidence；
- 输出不可变 dependency bundle hash。

## Offline VerifyBuild

- 完全断网；
- 非 root；
- 只读 source snapshot 与 dependency bundle；
- 独立可写输出目录；
- CPU、内存、进程、磁盘和墙钟限制；
- 不挂载 Docker socket；
- 不提供模型、部署和数据库 Secret；
- 执行 Runtime Profile 的 VerifyBuild；
- 输出 Artifact、VerificationReport 和日志 hash。

## UNKNOWN

Worker 或 Provider 失联时进入 UNKNOWN。必须查询沙箱或外部执行 ID并保存 reconciliation Evidence，随后转为 PASSED 或 FAILED。RECONCILED 不是最终业务结果状态。

## 失败分类

- POLICY_DENIED；
- DEPENDENCY_UNRESOLVED；
- SUPPLY_CHAIN_REJECTED；
- RESOURCE_EXCEEDED；
- TEST_FAILED；
- SECURITY_FAILED；
- INFRASTRUCTURE_FAILED；
- UNKNOWN_RESULT。
