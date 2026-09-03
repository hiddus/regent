# Regent 系统介绍（省市领导汇报）· 设计稿

## 画布与母版（A/B/C 三区）
- 画布：1280×720，16:9
- A 标题块：0–120px，主标题 32–38px bold
- B 内容区：120–660px，540px 可用
- C 页脚条：660–720px，左项目名「Regent 系统介绍 · 树米科技」+ 右页码 `NN / 20`，14px 灰字 `#64748B`
- 页面 padding：上下 20px，左右 64px

## 颜色系统（≤4 hex + 中性）
| 角色 | hex | 用途 |
|---|---|---|
| 主色 primary | `#16335B` | 标题栏、主视觉、强调块、深色底 |
| 辅色 secondary | `#2E6DB4` | 卡片描边、次视觉、图表第二系列 |
| 强调 accent | `#D7263D` | 仅用于关键数字、点睛强调（党政红） |
| 浅蓝 tint | `#E8F0FA` | 卡片底、区块底 |
| 文本主色 | `#1F2A37` | 正文 |
| 中性灰 | `#64748B` | 脚注、次要文字 |
| 背景 | `#FFFFFF` | 内容页底色 |
| 深色 hero | `#0E1B2E` | 封面/章节/结束深色底 |

面积分配：主色≤60%、辅色≤30%、强调≤10%（Hero 可到 15–20%）。
渐变：`linear-gradient(135deg,#16335B 0%,#2E6DB4 100%)`；半透明 `rgba(46,109,180,0.08)`。
卡片阴影：`0 4px 20px rgba(22,51,91,0.08)`。

## 字体系统
- 标题：PingFang SC / Microsoft YaHei Bold
- 正文：PingFang SC / Microsoft YaHei / Inter，regular
- 字号阶梯：封面主标 64 / 章节大字 56 / 巨型数字 72 / 页面主标 34 / 卡片小标 22 / 正文 18–20 / 脚注 14
- 字体家族串：`'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif`

## 视觉节奏
- Hero：01 / 10 / 20，深色或强视觉冲击。
- 章节过渡：03 / 06 / 09 / 12 / 15，统一深色渐变 + 大章节号。
- 内容页：非对称双栏、卡片网格、流程、四宫格，密度适中（≥150 字/页）。

## 页面映射表
| # | 文件 | 类型 | 角色 | 版式 | 色彩 |
|---|---|---|---|---|---|
| 01 | slide_01_cover | cover | hero | 全屏深色+骑线大标题 | 深色70%+蓝渐变 |
| 02 | slide_02_agenda | catalog | supporting | 左标题+右列表 | 蓝40% |
| 03 | slide_03_sec_goal | section | transition | 全屏大字 | 深色 |
| 04 | slide_04_pain | content | supporting | 四卡网格 | 蓝50%红点缀 |
| 05 | slide_05_goals | content | supporting | 三卡+底部定位 | 蓝50% |
| 06 | slide_06_sec_def | section | transition | 全屏大字 | 深色 |
| 07 | slide_07_definition | content | supporting | 非对称双栏 | 蓝50% |
| 08 | slide_08_loop | content | supporting | 上流程+下说明 | 蓝40% |
| 09 | slide_09_sec_cap | section | transition | 全屏大字 | 深色 |
| 10 | slide_10_selfevolve | content | hero | 左流程+右要点 | 蓝青渐变 |
| 11 | slide_11_hive | content | supporting | 非对称双栏 | 蓝45% |
| 12 | slide_12_sec_gov | section | transition | 全屏大字 | 深色 |
| 13 | slide_13_governance | content | supporting | 六卡网格 | 蓝45% |
| 14 | slide_14_sec_scene | section | transition | 全屏大字 | 深色 |
| 15 | slide_15_scenes | content | supporting | 四宫格 | 蓝50% |
| 16 | slide_16_smartcity | content | supporting | 左图标+右要点 | 蓝50% |
| 17 | slide_17_tourism | content | supporting | 左图标+右要点 | 蓝50% |
| 18 | slide_18_venue_factory | content | supporting | 两栏 | 蓝45% |
| 19 | slide_19_whytree | content | supporting | 左优势+右请求 | 蓝50%红点睛 |
| 20 | slide_20_ending | ending | hero | 居中金句 | 深色 |
