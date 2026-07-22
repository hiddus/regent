# Regent P2 平台计划

> 状态：ACTIVE  
> 日期：2026-07-22  
> 前置：P1 全局 DoD 未全部满足；**禁止跳过 P2-0 直接开发 P2-1+**  
> 产品基线：`p1-core-capability-requirements.md`  
> 技术基线：`p1-core-final-technical-spec.md`  
> P1 执行清单：`p1-remaining-coding-plan.md`

## 0. 总判

P1 功能链接近完成，但可信验收、安全基线和发布纪律尚未毕业。  
当前不得正式进入纯 P2 能力开发。可先确定 P2 路线，同时完成短期 **P1 Graduation（P2-0）** 门禁。

```text
P2-0  P1 Graduation
  ↓
P2-1  多 Goal 调度
  ↓
P2-2  多 Runtime Profile
  ↓
P2-3  长期记忆
  ↓
P2-4  Champion/Challenger
  ↓
P2-5  生产发布
  ↓
P2-6  动态组织
  ↓
P2-7  自我改进应用链
  ↓
P2-8  能力生态
```

批次命名：`p1-graduation-01`…；正式 P2 从 `p2-scheduler-01` 起。  
提交批次 = 实施顺序，不是子产品版本。

---

## P2-0：P1 Graduation

目标：把现有 P1 从「功能可运行」提升到「证据可信、可发布、可审计」。

### 任务

1. 修复 Ruff；执行 Pytest、mypy、迁移和安全门禁（DoD 12）。
2. 删除 Preview 自动注入假按钮逻辑；缺 `data-regent-event` 必须 fail-closed。
3. 从 RequirementRevision 提取核心用户旅程，执行浏览器级验证（非仅 HTTP / API 注入）。
4. 接入至少一个真实、受控 Evidence Connector（不止 Goal 文本自证）。
5. 由非开发测试用户完成核心任务并产生真实 Observation。
6. 证明一次唯一 CONTINUE / REVISE / STOP 决策（基于真实观测）。
7. 测试 Worker 重启、重复投递和 Provider UNKNOWN。
8. 轮换已泄露凭据；清理脚本明文；禁止诊断脚本进库。
9. 建立 Git 初始基线、发布标签和可回滚部署。

### 完成条件

P1 全局 DoD 12 条均有可查询证据。通过后方可开始 `p2-scheduler-01`。

### 建议提交

- `p1-graduation-01`：质量门禁 + 假交互清除 + 凭据清理  
- `p1-graduation-02`：浏览器核心任务 + 真实 Evidence  
- `p1-graduation-03`：Git 基线、标签、可靠性回归与 DoD 证据包  

---

## P2-1：多 Goal 调度与资源治理

目标：从单 Goal 顺序执行升级为多 Goal 安全并发。

主要任务：Scheduler / 队列优先级 / 组织配额；CPU·内存·Token·外部调用与预算；公平调度、抢占、暂停、恢复、取消；Goal 间防饥饿；并发 Permit、Lease、幂等；Console 展示排队原因与预算。

验收：≥20 并发 Goal 无重复副作用；高优先级可受控抢占；Worker 宕机可重分配；预算超限 → BLOCKED（非静默降级）。

建议提交：`p2-scheduler-01`（模型/迁移/状态机）→ `p2-scheduler-02`（配额/公平队列/Lease/抢占）。

---

## P2-2：多 Runtime Profile

目标：突破单一 `python-web-v1`。

首批：`static-web-v1`、`python-web-v1`、`node-web-v1`、`python-data-v1`。

主要任务：Profile 注册表、版本、镜像摘要、能力声明；选择策略与 Requirement 兼容检查；每 Profile 独立 resolver/sandbox/验证合同；锁文件/SBOM/缓存隔离；升级与旧版本回放。

验收：适配 Profile 可重复构建；不兼容 Profile fail-closed。

建议提交：`p2-runtime-01`（注册表 + static/python-web 迁移）。

---

## P2-3：长期记忆与知识治理

Experience / Decision / Failure / Capability 四类记忆；来源、版本、有效期、范围、置信度；写入 Gate；检索绑定权限；冲突/过期/撤销/被遗忘；防 Prompt Injection。

验收：可提升后续 Goal 效率，但不得覆盖冻结约束或绕过 Evidence。

---

## P2-4：Champion/Challenger 平台

实验定义、流量分配、随机化、样本隔离；指标版本冻结与停止条件；污染检测；建议替换但保留人工闸门；决策记录与回滚。

验收：同窗口唯一实验决策；样本不足 → `INSUFFICIENT_EVIDENCE`。

---

## P2-5：生产发布控制面

Preview → Staging → Production；审批/Permit/变更窗口/职责分离；Canary/蓝绿/自动回滚；Secret/域名/证书/迁移；SLO 与事故审计。

验收：生产发布不可绕过审批；阈值故障可回滚到上一不可变版本。

---

## P2-6：动态组织与多 Agent 执行

角色模板、组织提案与预算审批、能力匹配、上下文最小化、独立 Reviewer、失败替换与回收。

验收：多 Agent 不改变 GoalSpec / Permit / Gate 权威边界。

---

## P2-7：受监管自我改进应用链

候选补丁审批与合并；隔离回归与安全审查；Canary Worker；自动回滚；禁止改 Permit/审计/评价器/安全边界。

验收：Core 不能自行批准、评价并发布自己的改动。

---

## P2-8：能力生态与市场准备

Capability Package、签名认证撤销、SBOM、成本模型；私有库优先、公共市场后置。

验收：未签名/已撤销/不兼容能力不得进入 ResolutionPlan。

---

## 明确非目标（不因 P2 自动纳入）

World Model、任意语言栈、无监督递归自改、默认动态组织、Kafka/多区域/K8s 等（见 P1 能力需求「明确非目标」）。

---

## 编码启动结论

结论：`GO`，从 **`p1-graduation-01`（P2-0）** 开始。  
未完成 Graduation 前，`p2-scheduler-01` 及后续批次不得开工。
