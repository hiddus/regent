# Regent 失败案例复盘 · 设计稿

## 画布与母版（A/B/C 三区）
- 画布：1280×720，16:9
- A 标题块：0–120px，主标题 32–40px bold
- B 内容区：120–660px，540px 可用
- C 页脚条：660–720px，左项目名「Regent 失败案例复盘」+ 右页码 `NN / 20`，14px 灰字 `#64748B`
- 页面 padding：上下 20px，左右 64px

## 颜色系统（≤4 hex + 中性）
| 角色 | hex | 用途 |
|---|---|---|
| 主色 primary | `#3B82F6` | 标题栏、主视觉、强调块 |
| 辅色 secondary | `#06B6D4` | 卡片描边、次视觉、图表第二系列 |
| 强调/警示 accent | `#EF4444` | 仅用于失败根因、关键警示数字 |
| 深色 hero | `#0F172A` | 封面/过渡/结束深色底 |
| 文本主色 | `#1E293B` | 正文 |
| 中性灰 | `#64748B` | 脚注、次要文字 |
| 背景 | `#FFFFFF` | 内容页底色 |

面积分配：主色≤60%、辅色≤30%、强调≤10%（Hero 可到 15–20%）。
渐变：`linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)`；半透明 `rgba(59,130,246,0.08)`。
卡片阴影：`0 4px 20px rgba(0,0,0,0.08)`。

## 字体系统
- 标题：PingFang SC / Microsoft YaHei Bold
- 正文：PingFang SC / Microsoft YaHei / Inter，regular
- 字号阶梯：封面主标 72 / 章节大字 64 / 巨型数字 72–110 / 页面主标 34 / 卡片小标 24 / 正文 22 / 脚注 14
- 字体家族串：`'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif`

## 视觉节奏
- Hero：01/11/14/20，深色或巨型数字冲击。
- 章节过渡：03/06/10/13/17，统一深色渐变 + 大章节号。
- 内容页全部非对称或表格，密度高（≥180 字/页）。

## 页面映射表
| # | 文件 | 类型 | 角色 | 版式 | 字数 | 留白 | 色彩 |
|---|---|---|---|---|---|---|---|
| 01 | slide_01_cover | cover | hero | 全屏深色+骑线标题 | 30 | 35% | 深色60%+蓝青渐变 |
| 02 | slide_02_agenda | catalog | supporting | 左标题+右内容 | 160 | 25% | 蓝40% |
| 03 | slide_03_sec_goal | section | transition | 全屏大字 | 30 | 40% | 深色 |
| 04 | slide_04_what_is | content | supporting | 非对称双栏 | 220 | 22% | 蓝50%青20% |
| 05 | slide_05_constraints | content | supporting | 左标题+右表格 | 200 | 20% | 蓝40% |
| 06 | slide_06_sec_framework | section | transition | 全屏大字 | 30 | 40% | 深色 |
| 07 | slide_07_p0_kernel | content | supporting | 左大图+右文 | 240 | 20% | 蓝50% |
| 08 | slide_08_p1_loop | content | supporting | 上流程下表格 | 220 | 18% | 蓝40%青20% |
| 09 | slide_09_governance | content | supporting | 非对称双栏 | 260 | 18% | 蓝45% |
| 10 | slide_10_sec_evolution | section | transition | 全屏大字 | 30 | 40% | 深色 |
| 11 | slide_11_timeline | content | hero | 横向时间轴 | 200 | 25% | 蓝青渐变 |
| 12 | slide_12_insight | content | supporting | 巨型数字+图 | 150 | 30% | 红警示15% |
| 13 | slide_13_sec_failure | section | transition | 全屏大字 | 30 | 40% | 深色 |
| 14 | slide_14_evidence | content | hero | 巨型数字阵列 | 120 | 25% | 红20% |
| 15 | slide_15_rootcauses | content | supporting | 非对称双栏 | 300 | 16% | 红青 |
| 16 | slide_16_basics | content | supporting | 左标题+右表格 | 220 | 18% | 蓝40% |
| 17 | slide_17_sec_conclusion | section | transition | 全屏大字 | 30 | 40% | 深色 |
| 18 | slide_18_how_agent | content | supporting | 非对称双栏 | 320 | 16% | 蓝50% |
| 19 | slide_19_how_multiagent | content | supporting | 非对称双栏 | 300 | 16% | 蓝45%青15% |
| 20 | slide_20_ending | ending | hero | 居中金句+练习 | 120 | 35% | 深色 |
