# Regent 失败案例复盘 · 课堂 PPT 叙事文档

## ① 用户意图对齐

- **目标受众**：公司内部研发/算法团队，2 小时「Agent 架构学习课堂」，听讲者是会写代码、但容易重蹈 Regent 覆辙的小伙伴。
- **核心目标**：讲完之后，观众能记住三件事——① Regent 定义/需求其实很优秀；② 它失败在"一直在周边敲边鼓、核心单 Agent 闭环没打通"；③ 正确搭 Agent / 多 Agent 的先后次序与底线。
- **PPT 长度**：20 页（含 1 封面、4 章节过渡、1 结束），Hero 页约 4 张（封面 / 框架演变时间线 / 证据墙 / 结束），占比 20%。
- **视觉调性**：科技现代、克制、有冲击力；蓝为主、青为辅、红作"警示"强调，白色底为主、深色仅用于 Hero。
- **内容边界**：必讲=项目目标、各阶段框架、框架演变、失败原因、Agent/多Agent 结论；不讲=具体代码实现细节、不替 Regent 辩护、不发散到其他产品。

## ② 页面布局骨架

- **总页数 20**，分五章：目标 / 框架 / 演变 / 失败 / 结论。
- **Hero 页**：01 封面、11 演变时间线、14 证据墙、20 结束。
- **章节过渡**：03 / 06 / 10 / 13 / 17（共 5 页 transition）。
- **rhythm 曲线**：封面 peak → 02 valley → 03 transition → 04/05 valley → 06 transition → 07/08/09 valley → 10 transition → 11 peak → 12 valley → 13 transition → 14 peak → 15/16 valley → 17 transition → 18/19 valley → 20 peak。
- **非对称版式预算**：约 12/15 内容页用非对称（左大图+右文、非对称双栏、巨型数字+洞察、左标题+右内容），仅 08 验收矩阵用表格、09 框架总览用卡片网格（非对称双栏承载）。
- **对称版式**：仅 05（10原则表）与 16（根因→基本功表）两页用表格类对称，不连续。

## ③ 页面大纲

| # | title | type | role | rhythm | layout | visual | visual_role | density | anti_pattern |
|---|---|---|---|---|---|---|---|---|---|
| 01 | 封面 | cover | hero | peak | 全屏深色+骑线大标题 | L1 深色底纹 | anchor | 30字 | 禁止装饰小图 |
| 02 | 课堂议程 | catalog | supporting | valley | 左标题+右内容 | L3 序号 | evidence | 160字 | 禁止等宽四卡 |
| 03 | 第一章·项目目标 | section | transition | transition | 全屏大字+章节号 | L1 渐变 | atmosphere | 30字 | 禁止四卡预览 |
| 04 | Regent 到底是什么 | content | supporting | valley | 非对称双栏(左定义/右身份) | L2 公式卡 | evidence | 220字 | 禁止等分双栏 |
| 05 | 目标的硬约束 | content | supporting | valley | 左标题+右表格 | Table | evidence | 200字 | 禁止稀薄 |
| 06 | 第二章·各阶段框架 | section | transition | transition | 全屏大字 | L1 渐变 | atmosphere | 30字 | 禁止四卡 |
| 07 | P0 可靠内核框架 | content | supporting | valley | 左大图(架构)+右文字 | L2 架构框 | anchor | 240字 | 禁止N卡横排 |
| 08 | P1 运营闭环 + 验收矩阵 | content | supporting | valley | 上流程+下表格 | Diagram+Table | evidence | 220字 | 禁止等宽卡 |
| 09 | 治理/多Agent/测量框架 | content | supporting | valley | 非对称双栏(左多Agent/右测量) | L2 卡片网格 | evidence | 260字 | 禁止等分 |
| 10 | 第三章·框架演变 | section | transition | transition | 全屏大字 | L1 渐变 | atmosphere | 30字 | 禁止四卡 |
| 11 | 框架演变时间线 | content | hero | peak | 横向时间轴 | Diagram(时间轴) | anchor | 200字 | 禁止卡片堆 |
| 12 | 演变洞察：复杂度↑ 交付力↓ | content | supporting | valley | 巨型数字+图 | Chart(折线/柱) | evidence | 150字 | 禁止N卡 |
| 13 | 第四章·失败原因 | section | transition | transition | 全屏大字 | L1 渐变 | atmosphere | 30字 | 禁止四卡 |
| 14 | 证据墙：项目确实失败了 | content | hero | peak | 巨型数字阵列 | 大数字 | anchor | 120字 | 禁止装饰小图 |
| 15 | 五大失败根因 | content | supporting | valley | 非对称双栏(5条) | L2 编号卡 | evidence | 300字 | 禁止等宽横排 |
| 16 | 根因 → 漏掉的基本功 | content | supporting | valley | 左标题+右表格 | Table | evidence | 220字 | 禁止稀薄 |
| 17 | 第五章·结论 | section | transition | transition | 全屏大字 | L1 渐变 | atmosphere | 30字 | 禁止四卡 |
| 18 | Agent 到底该怎么搭 | content | supporting | valley | 非对称双栏(7步) | L2 步骤卡 | evidence | 320字 | 禁止等分 |
| 19 | 多Agent 到底该怎么搭 | content | supporting | valley | 非对称双栏(原则+协议) | L2 卡片 | evidence | 300字 | 禁止等分 |
| 20 | 收束金句 + 课堂练习 | ending | hero | peak | 居中金句+练习条 | L1 深色 | anchor | 120字 | 禁止堆砌 |
