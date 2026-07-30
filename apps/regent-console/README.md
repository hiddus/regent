# Regent Console（Web 控制台）

基于 **React 19 + Vite + TypeScript** 的前端控制台，对接 Regent Core 的 REST API，用于可视化目标/工作项执行、与 Agent 对话式交互、处理人工确认任务。

## 功能

- 目标（Goal）/ 工作项（Work）看板与状态跟踪
- 对话流（`Composer` / `MessageList`，SSE 流式）与进度节点（`ProgressNodeCard`）
- 右侧面板（`ArtifactPanel`）：默认展示当前 Goal 的「参与 Agent」名册；产物与预览为可折叠次要区
- 人工任务确认卡片（`ConfirmationCard`）
- 侧边栏导航（`Sidebar`）、任务卡片（`TaskCard`）

### 右侧 `ArtifactPanel`（参与 Agent）

- 面板标题为 **参与 Agent**；主视图是当前 Goal 的 Agent 名册（活动态、主助手 / Core、Hive 部署等），不再以「汇总执行进度」作为默认主视图。
- 名册数据：优先使用 Core 状态接口返回的 `status.agents`；若为空（旧 Core），由 `lib/agents.ts` 的 `deriveAgents` 根据 Goal 拓扑推导，并用 `live_action`（经 `lib/liveActivity.ts`）补主助手活动摘要。
- 条目展示活动态标签（活动中 / 待命 / 等待确认等）、主助手与 Hive 角色元信息；有 `detail` 时显示一行说明。
- **产物与预览** 为可折叠次要区：应用预览 iframe、下载产出物等；预览就绪时可自动展开该折叠区。

### 对话流 `ProgressNodeCard`（动态详略）

- 三档视图：`detail`（详情）/ `overview`（概览）/ `compressed`（压缩）。
- Goal 仍在进行中（`liveMode`）且节点为 running/waiting 时，默认展开 **详情**；结束后默认 **概览**，可再点进 **压缩**。
- 点击标题区循环切换详略（进行中在详情 ↔ 概览间切换；已结束后概览 → 压缩 → 详情 → 概览）。

## 目录结构

```
src/
  App.tsx              # 入口组件
  main.tsx             # 挂载入口
  index.css            # 全局样式
  components/          # UI 组件（见上）
  hooks/
    useSSE.ts          # SSE 流式订阅
    useWorkspace.ts    # 工作区状态
  lib/
    api.ts             # API 封装
    agents.ts          # 参与 Agent 名册推导与活动态
    liveActivity.ts    # live_action / 连接态与相对时间文案
    progressNodes.ts   # 进度节点模型
    types.ts           # 共享类型
apps/regent-console/
  index.html           # HTML 模板
  vite.config.ts       # Vite 配置
  tsconfig.json        # TS 配置
  nginx.conf           # 生产 Nginx 配置
  Dockerfile           # 生产镜像（构建 dist/ 并由 Nginx 托管）
  dist/                # 构建产物（gitignore）
```

## 开发

```bash
npm install
npm run dev      # Vite 开发服务器
npm run build    # tsc -b && vite build → dist/
npm run preview  # 预览构建产物
```

生产部署由 `Dockerfile` + `nginx.conf` 负责，构建产物落入 `dist/`。
