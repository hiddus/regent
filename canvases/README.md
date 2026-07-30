# Canvases（可视化画布产物）

存放由对话 / 报告生成的可视化画布产物。每个画布由一对文件组成：

- `*.canvas.tsx` — 渲染组件（React/TSX 片段）。
- `*.canvas.status.json` — 状态与元数据（生成时间、来源、版本等）。

## 当前内容

- `regent-v3-gap-repair-summary` — v3 缺口修复总结画布。
- `v3-component-conflict-resolution` — v3 组件冲突消解画布。
- `v3-freeze-summary` — v3 冻结总结画布。
- `v3-implementation-report` — v3 实施报告画布。

## 说明

- 这些是**生成物而非源码**，可由对应的生成流程重新产出，不要求手工维护。
- 历史上根目录曾散落 `regent-v3-gap-repair-summary.*` 与 `v3-freeze-summary.*` 等同名画布文件，已归类到本目录或归档，保持仓库根整洁。
- 归档的旧画布见根 `archive/`。
