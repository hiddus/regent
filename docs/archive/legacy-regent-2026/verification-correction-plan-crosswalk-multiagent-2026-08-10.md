# Regent 实际验证计划与修正计划

**主题**：以「中国→海外数据合规 Crosswalk 操作手册站」为业务载体，验证多 Agent 共同维护可容错内容汇总站的自治能力；在实践中发现问题并闭环修正。  
**日期**：2026-08-10  
**状态**：`READY_TO_EXECUTE`（计划已冻结；执行前须完成 V0 基线门禁）  
**载体 Goal 类型**：App / Live Preview（触发 Delivery Role Swarm 全名册）

---

## 0. 一句话结论

Regent **已有** Product/Tech/Test/UX/Ops 交付 Swarm + PM/Dev/QA Hive + 主机自愈 + Crosswalk 深度启发式门禁，但 **尚不能** 无人值守地持续运营合规手册站。本计划用同一业务 Goal 做「平台自治验证」，用角色 RACI 驱动「能力缺口→修正→再验证」，禁止把 soft-pass / babysit 脚本成功当成业务达成。

---

## 1. 业务验收北极星（产品侧，非平台口号）

终端用户（中国 AI 设备出口合规负责人）打开站点后，必须能按手册逐步执行，并在中国法规前提下达成目标国合规。

| ID | 北极星能力 | 可观测验收 |
|----|------------|------------|
| B1 | **国别义务库** | CN / SG / US 各国 ≥10 条实质义务；字段含 statute、source URL、obligation、scenario、risk；非口号 |
| B2 | **双边 Crosswalk** | 至少 `CN→SG`、`CN→US`、`US↔SG` 可打开；每对 ≥10 步可操作步骤；每步有「做什么 / 产出物 / 证据」 |
| B3 | **累加减免** | 已完成国 A 再做国 B 时，展示「可复用 / 需补充」差分；欧洲样例可用「已做 DE → 补 FR」模拟 |
| B4 | **容错采集** | 多源抓取；单源失败站点仍可用；`/api/health` 可 degraded；法源更新有时间戳与失败重试 |
| B5 | **持续更新** | 无人工 babysit 脚本时，定时/触发刷新仍能产生可审计变更记录 |
| B6 | **可操作手册体验** | 品牌可读、列表→详情可点、无乱码、非默认灰底模板壳 |

**明确非目标（本轮）**：法务签字级意见书、真实律所背书、付费转化闭环（销售验证只做漏斗契约，不做真实收款）。

---

## 2. Regent 现状映射（为何这样验）

| 层 | 现状 | 对本验证的含义 |
|----|------|----------------|
| Core | Goal → 生成 → Preview → Gate → CONTINUE/REVISE | 合规站是 **Generated App**，不是 Core 内置 CMS |
| Hive | `pm-dev-independent-qa-v1`（PM→Dev→结构 QA） | 结构过 ≠ 产品过 |
| Delivery Swarm | product / tech / test / ux / ops（固定目录，可自补充） | 本轮**基线跑**用固定名册以便对照；沙箱内新增候选角色默认开放（定义 3.0 ATTRIBUTE_4），仅不得未经授权承担生产交付职责 |
| 内容能力 | `allowlisted-http-source-v1` 等；工具桥接未完全打通 | 运营 Agent 可能「说要采」但采不到 |
| 自愈 | `environment-heal-v1` + LESSONS | 主机可愈；业务内容不可愈 |
| 自进化 | SelfImprovement 的**生产 rollout** 为 `ROLLOUT_NOT_ALLOWED` | 不得宣称已自治进化；沙箱内候选改进与学习更新照常进行 |
| 历史 Crosswalk | 多轮 babysit；曾出现乱码、`/crosswalk/US-SG` 404、依赖人工续跑 | **必须作为回归基线**，禁止重蹈「有 Preview 无手册」 |

### 2.1 角色对照（用户要求 ↔ Regent 可执行体）

| 用户角色 | Regent 现有可执行体 | 缺口 | 本轮处置 |
|----------|---------------------|------|----------|
| 产品 | Delivery `product` + Hive `pm` + skill `product` | 缺「手册可执行性」合同字段 | **V1 补验收清单**，不 invent 角色 |
| 项目 | `execution_plan_items` / dispatch / Console 计划面 | 无独立 Project Agent | 由 **项目 Owner（人）+ PM** 持有里程碑；修正项 M-PROJ |
| 技术 | Delivery `tech` + Hive `dev` | API 路径逃逸曾 soft-pass | 强制 Preview 前缀探针 |
| UI | skill `ui`（与 UX 共用） | UI/UX 未拆分 | UX Agent 兼 UI 门禁；修正项区分视觉 token vs 旅程 |
| UX | Delivery `ux` | 曾放行「有 CSS 但丑/乱码」 | 增加乱码/可读性硬失败 |
| 测试 | Delivery `test` + Hive `qa` | 结构 QA ≠ 场景矩阵 | Test 必须跑 Live 场景 |
| 运维 | Delivery `ops` + heal | babysit 脚本外置 | 把续跑从 ops 脚本迁入 Goal CONTINUE |
| 运营 | **无一等角色** | 内容刷新/法源时效无人负责 | **修正项 M-OPS-CONTENT**：先用元数据 roster 扩权 + Content skill；不得假称已有运营 Agent |
| 销售 | **无** | 无获客/手册转化漏斗 | **修正项 M-SALES**：先做人机 RACI + 漏斗观测契约；角色目录扩展需 DecisionRecord |

---

## 3. 验证计划（Verification）

### 原则

1. **一次业务 Goal，多层闸门**：业务北极星 ∧ 交付 Swarm ∧ Hive ∧ 主机健康。任一失败 → REVISE，禁止人工改 HTML 冒充 Agent 成功。  
2. **失败即资产**：每次失败写入 gap_code + 责任角色 + 修正票。  
3. **禁伪达成**：`PREVIEW_SUCCEEDED` + soft-pass + open_items 非空 ≠ 达成。  
4. **禁外挂成功**：`ops/_babysit_crosswalk_*.py` 仅作诊断，**不计入** V3+ 通过证据。

### V0 — 基线与门禁冻结（0.5 天，项目+运维）

| 检查 | Owner | Pass |
|------|-------|------|
| 生产/目标环境磁盘·内存·preview-venv 健康或已 heal | 运维 | heal receipt 或健康快照 |
| Delivery catalog 对「crosswalk/合规」Goal 选出 `product,tech,test,ux,ops` | 技术 | `delivery_role_catalog` 断言 |
| 历史 Crosswalk 缺陷清单入库（乱码、404、依赖 babysit） | 项目 | 回归用例表 R1–R6 |
| M6/GQ-4/自适应拓扑仍为**禁止扩大生产流量**（沙箱试验不受限） | 项目 | 本计划不触碰生产晋级 |

**回归用例（必须复测）**

| ID | 历史缺陷 | 复测断言 |
|----|----------|----------|
| R1 | 中文乱码 | 可见文本无 `璺ㄥ` 类 mojibake；`Content-Type` charset 正确 |
| R2 | `/crosswalk/US-SG` 404 | 页面与 `/api/crosswalks/{pair}` 均 200 且 steps≥10 |
| R3 | 仅有国家卡片无步骤 | Live QA content-depth 与人工抽检一致 |
| R4 | soft-pass 掩盖 smoke 失败 | open_items 空或全部 closed 才允许产品 seal |
| R5 | 人工 hotfix UX | 无仓库外直接改 Preview 文件的「假绿」 |
| R6 | soft-pause / CANCELLED 中断运营 | 连续 2 个刷新周期无 Goal 取消 |

### V1 — 多角色联合产品合同（1 天，产品主导）

冻结 **Goal 输入文本**（中英关键）与 **Acceptance Contract**，全角色会签：

```text
Goal（摘要）：建设 Regent 官方维护的跨境数据合规 Crosswalk 操作手册站。
以中国数据合规为起点，持续对标海外国家（先 SG、US），输出可逐步执行的适配手续；
支持「已完成国家 → 新目标国」的累加差分；多源内容采集容错；
由多 Agent（产品/技术/测试/UX/运维，并暴露运营与销售缺口）共同维护，禁止大纲壳。
```

| 角色 | 必须写入合同的验收项 | 否决权 |
|------|----------------------|--------|
| 产品 | B1–B3 字段级；拒口号目录 | 有 |
| 项目 | 里程碑 M0–M4、预算、止损条件 | 有（范围） |
| 技术 | 路由/API 契约、UTF-8、持久化路径 | 有 |
| UI | 字体/色板/信息层级非默认栈 | 并入 UX |
| UX | 首屏品牌+手册入口；列表→详情；无乱码 | 有 |
| 测试 | 场景矩阵 S1–S8（见下） | 有 |
| 运维 | health/degraded、磁盘、Preview 存活 | 有 |
| 运营 | 刷新 API、源状态、更新时间戳（可先记为缺口） | 顾问票 |
| 销售 | 「按手册可达合规」价值主张页 + CTA 占位（不计转化） | 顾问票 |

**场景矩阵 S1–S8（Test 必跑 Live）**

1. 首页 → 国家目录 → SG 详情（≥10 points）  
2. CN→SG Crosswalk 逐步展开  
3. CN→US Crosswalk 逐步展开  
4. 已标记「完成 SG」后再开 US，出现差分「复用/补充」  
5. `/api/collect` 或等价刷新：成功源更新时间戳  
6. 人为禁用/打挂一源：站点 degraded 仍可浏览  
7. 检索/过滤手册步骤  
8. Agent/控制台入口仅展示状态，不代替手册正文

### V2 — 冷启动生成验证（1–2 天，技术+全 Swarm）

1. 用 V1 合同创建 **新 Goal**（不要复活已 CANCELLED 的脏 Workspace，除非 REPLACE 策略已验证）。  
2. 观察 Durable Deployments：Hive + `delivery-roles-v1` 是否物化。  
3. 等待 Preview；**禁止**中途 SSH 改站点文件。  
4. 收集证据包：transcript、dispatch、Live QA JSON、各角色 `delivery.*.review` 产物。

**平台通过标准（P-pass）**

- 五角色均产生独立 review 产物（非复制粘贴同一段）  
- 失败角色触发 follow-up / `evolve_failed_delivery_roles` 可追踪  
- 主机不健康时 Ops 否决，而非 Product 单独绿

**业务通过标准（B-pass）**：B1–B4 + R1–R3。

### V3 — 对抗与容错验证（1 天，测试+运维+运营）

| 注入 | 期望 | Owner |
|------|------|-------|
| 拔掉一个 allowlist 源 / 返回 5xx | health=degraded；手册仍可读 | 测试/运维 |
| 填满磁盘或抬高 preview-venv | Ops 触发 heal 或硬失败；不假绿 | 运维 |
| 提交空洞大纲 PR 式内容 | Product/UX/Test 拒绝 | 产品/测试 |
| 故意错误 charset 输出 | UX/Tech 失败 | UX/技术 |
| 连续两次内容刷新 | 产生审计差分；无 CANCELLED | 运营/项目 |

### V4 — 「自主维护」窗口（2–3 天，运营+项目）

在 **无人执行 babysit 脚本** 的前提下：

| 观测 | Pass |
|------|------|
| 定时/手动刷新 ≥3 次 | 有变更或明确「无源更新」记录 |
| Swarm follow-up 在缺口重开时再次出现 | 审计链完整 |
| 销售漏斗事件（手册完成步数、CTA 点击）可观测或显式记为未实现 | 不撒谎 |
| 累计差分在新增「假想 EU-FR」时只需补充清单而非全文重写 | 产品抽检 |

**若 V4 失败**：记入修正计划 M-LOOP，**不**用人工值守冒充通过。

### V5 — 多 Agent 完整性审计（0.5 天，项目）

产出一份 `docs/crosswalk-multiagent-verify-<date>.json`，字段至少：

- `roster_selected` / `roster_executed` / `missing_roles`  
- `hive_accepted` / `swarm_accepted_by_role`  
- `business_gates` B1–B6  
- `pseudo_success_flags`（soft-pass、babysit、人工 hotfix）  
- `correction_tickets` 列表

---

## 4. 修正计划（Correction）

修正遵循：**先证明缺口 → 最小内核补丁 → 再跑同一验证子集**。禁止并行发明框架。

### 优先级定义

- **P0**：阻断 B-pass 或导致伪达成  
- **P1**：阻断 V4 自主维护  
- **P2**：角色完备与销售/法务语义  
- **P3**：**生产晋级**类（GQ-4 默认切换、自适应拓扑扩流、SelfImprovement rollout）— 本计划默认不推进生产晋级；其沙箱候选试验与证据收集不受本级别限制

### 修正票（按发现顺序预置；执行中可增补）

| ID | 优先级 | 缺口 | 修正动作 | 验证回插 | Owner 角色 |
|----|--------|------|----------|----------|------------|
| M1 | P0 | 乱码 / charset | Tech 生成模板强制 UTF-8；Live QA 增加 mojibake 检测 | R1 + V2 | 技术/UX |
| M2 | P0 | Crosswalk 路由 404 或 steps 空 | Tech 契约测试锁定 `/crosswalk/{pair}` 与 API；Product 拒空壳 | R2–R3 | 技术/产品 |
| M3 | P0 | soft-pass + open_items 并存仍显示成功 | Console/Gate 语义：有未关闭 open_items 不得 product seal | R4 | 产品/项目 |
| M4 | P0 | 人工 babysit 才能续跑 | CONTINUE/REVISE 由 Worker 驱动；废弃作为成功证据的 babysit | V4 | 运维/项目 |
| M5 | P1 | 无运营角色，内容不刷新 | （a）Goal metadata 增加 content-ops 检查清单并由 Product 代持；（b）打通 allowlisted 源 → Agent tool；（c）DecisionNote 后再扩 `operations` 角色 | V3–V4 | 运营/技术 |
| M6 | P1 | 累加差分缺失 | 数据模型 `completion_profile` + `delta_to(target)`；Product/Test 场景 S4 | B3 | 产品/技术 |
| M7 | P1 | LESSONS 不进入下一轮生成 | 证明 harness lesson 注入生成上下文；对比前后 Preview | V2 重跑 | 技术 |
| M8 | P2 | 无销售角色 | 人机 RACI：销售只读漏斗事件；CTA 与「手册完成度」埋点；不扩 catalog 直至 DecisionRecord | V4 销售项 | 销售/产品 |
| M9 | P2 | 合规语义仅启发式 | 增加「法源 URL 可达 + 更新日期」检查；**不**声称法律正确性；显著免责声明 | B1/B4 | 产品/运营 |
| M10 | P2 | UI/UX 未拆分导致视觉债 | UX review 拆 `visual_tokens` 与 `journey` 两段产物 | B6 | UI/UX |
| M11 | P2 | 无 Project Agent | Console 里程碑与 correction ticket 绑定；项目 Owner 人工 | V5 | 项目 |
| M12 | P3 | 模型主动 spawn / 自适应拓扑 | **生产扩流保持 `ROLLOUT_NOT_ALLOWED`**；本验证主链用固定 Swarm 作对照基线，沙箱 spawn/拓扑试验开放并记录 `OrganizationExperiment` | — | 项目 |
| M13 | P3 | SelfImprovement 上线 | **生产 rollout 保持禁止**；沙箱候选改进与 `LearningUpdate` 证据照常产出 | — | 项目 |

### 修正执行节奏

```text
发现（V2/V3/V4）
  → 开票（gap_code + 证据）
  → 最小补丁（优先 Core 门禁 / Swarm / 生成契约，其次 App）
  → 单测 + 架构守卫
  → 重跑验证子集（不得跳级宣称 V4）
  → V5 归档
```

### 「自行长出」边界（防止幻觉）

| 允许 Regent 自补充 | 禁止假装已长出 |
|--------------------|----------------|
| 固定目录内角色自选（product…ops） | 自由 invent Legal/Sales Agent |
| 失败角色 durable follow-up | 无人值守法务结论 |
| 主机 heal + skill LESSONS | SelfImprovement 已生产晋级 |
| 能力解析：复用/配置/组合/构建/请人 | 请人票未关却称自治完成 |

缺运营/销售时的正确行为：**显式 escalate 请人 + 记录 capability gap**，而不是静默用 Product 角色冒充。

---

## 5. 各角色在验证中的日常动作

| 角色 | V0–V1 | V2–V3 | V4–V5 | 修正期 |
|------|-------|-------|-------|--------|
| 产品 | 冻结合同 B1–B6 | 抽检手册可执行性；拒大纲 | 确认累加差分价值 | M2/M3/M6/M9 |
| 项目 | 基线、止损、日程 | 阻断伪达成；管范围 | 产出 verify JSON | 全票优先级仲裁 |
| 技术 | catalog/UTF-8/API 合同 | 探针、持久化、采集桥 | 刷新链路稳定 | M1/M2/M5/M7 |
| UI | 视觉 token 写入合同 | 对照首屏品牌与可读性 | 回归视觉债 | M10 |
| UX | IA 与旅程 | 乱码/点击/层级否决 | 持续表面回归 | M1/M10 |
| 测试 | S1–S8 | Live 执行与证据 | 对抗注入 | 回归套件维护 |
| 运维 | 健康与 heal | 资源注入、Preview 存活 | 无 babysit 窗口 | M4 |
| 运营 | 源清单与更新 SLA | 采集对抗 | 自主刷新窗口 | M5/M9 |
| 销售 | 价值主张与 CTA 合同 | 占位页可达 | 漏斗事件或显式缺口 | M8 |

---

## 6. 通过 / 失败 / 止损

### 总通过（本轮验证成功）

- V0–V5 全部 Pass  
- B1–B4、B6 达成；B5 至少「半自动刷新 + 审计」（全自动可记 partial）  
- `pseudo_success_flags` 全 false  
- 预置 P0 修正票 M1–M4 若触发则已关闭并回归  

### 部分通过（平台有用、业务未闭环）

- Swarm/Hive/Preview 链路通，但 B5 或累加差分失败  
- **结论话术必须写**：平台可生成可审站点；自主运营未闭环  
- 进入 M5/M6，不开放「Regent 已自主运营合规站」对外表述  

### 止损（立即停）

- 连续 WorkspaceConflict / CANCELLED ≥3  
- 主机 heal 失败导致无法 Preview  
- 出现密钥/PII 泄漏（ComplianceChecker）  
- 有人用 babysit 伪装 V4 通过  

---

## 7. 交付物清单

| 交付物 | 路径/形态 |
|--------|-----------|
| 本计划 | `docs/verification-correction-plan-crosswalk-multiagent-2026-08-10.md` |
| Goal 合同原文 | Console Goal input + Artifact |
| 验证证据包 | `docs/crosswalk-multiagent-verify-<date>.json` |
| 修正票执行记录 | DecisionNote 或同目录 `correction-tickets-*.md` |
| 角色会签 | 项目纪要（人）+ Swarm review 产物（机） |

---

## 8. 建议执行顺序（日历）

| 日 | 内容 |
|----|------|
| D0 | V0 基线 + 角色会签 V1 合同 |
| D1–D2 | V2 冷启动；开 P0 票并当日修 |
| D3 | V3 对抗；关闭 M1–M4 |
| D4–D6 | V4 无 babysit 窗口 |
| D7 | V5 审计；冻结下一轮修正（M5+） |

---

## 9. 与既有决策的兼容

> 本节的「不」全部指**现实生产影响的扩大**（生产流量、真实数据、对外传播、资金与法律影响），不约束思想、候选角色、并行假设与沙箱组织试验——后者按定义 3.0 ATTRIBUTE_2/4/7 默认开放。

- **不**把 P2-5 自适应拓扑投入生产流量（`ROLLOUT_NOT_ALLOWED` 仅为 rollout 门禁）  
- **不**宣称 GQ-4 / M6 canary 晋级  
- **不**开启 SelfImprovement 的生产 rollout  
- Delivery 角色在**生产交付主链**上只从固定目录选；沙箱内可自由创建候选角色，运营/销售缺口先走修正票与人机 RACI  
- 延续「人辅助决断、证据说话」：本验证证明的是 **可治理多 Agent 交付 + 有限自治**，不是无人法务事务所  

---

## 10. 立即下一步（执行入口）

1. 项目 Owner 确认本文件为执行基线。  
2. 运维跑 V0 健康检查；技术跑 `delivery_role_catalog` 对合规 Goal 的断言。  
3. 产品发布 V1 Goal 全文；九角色（含运营/销售顾问）会签。  
4. 开跑 V2；任何 soft-pass 争议以本文件 §3 原则 3 裁决。
