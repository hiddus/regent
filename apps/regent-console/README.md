# Regent Console（Web 控制台）

基于 **React 19 + Vite + TypeScript** 的前端控制台，对接 Regent Core 的 REST API，用于可视化目标执行、对话式交互与人工闸门。

## 功能（UX 重梳 2026-08-03）

- **三轨构图**：App 列表 · 对话主叙述 · 工作区证据面
- **工作区 Tab**：清单（默认）/ 运行（Agent 名册 + 活动流）/ 改动 / 预览 / 审阅
- **顶栏**：阶段微标 + **清单完成比** + 运行控制（**停止一等**；批准/拒绝仅在对话卡）
- **闸门卡片族** `InterventionCard`：计划批准 / 权限 / 问人 / 恢复
- **结果卡** `ResultCard`：COMPLETE / STOP 一等可见
- **工具轨迹** `ToolTrace`：默认折叠摘要
- 对话流 SSE + 进度节点详略；Composer 含侧问（快问）

## 目录结构

```
src/
  App.tsx
  main.tsx
  index.css
  components/
    Sidebar.tsx          # App 轨 + StageBar（RunControls）
    MessageList.tsx
    Composer.tsx
    ArtifactPanel.tsx    # 工作区 Tab
    InterventionCard.tsx
    ConfirmationCard.tsx
    TaskCard.tsx
    RecoveryCard.tsx
    ResultCard.tsx
    ProgressNodeCard.tsx
    ToolTrace.tsx
  hooks/
  lib/
```

产品语义见仓库根 `Regent-PRD.md` §4.3；方案见 `docs/console-ux-redesign-2026-08-03.md`。

## 开发

```bash
npm install
npm run dev
npm run build
```
