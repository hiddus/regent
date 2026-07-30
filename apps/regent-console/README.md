# Regent Console（Web 控制台）

基于 **React 19 + Vite + TypeScript** 的前端控制台，对接 Regent Core 的 REST API，用于可视化目标/工作项执行、与 Agent 对话式交互、处理人工确认任务。

## 功能

- 目标（Goal）/ 工作项（Work）看板与状态跟踪
- Artifact 面板（`ArtifactPanel`）与对话式交互（`Composer` / `MessageList`，SSE 流式）
- 进度节点展示（`ProgressNodeCard`）、人工任务确认卡片（`ConfirmationCard`）
- 侧边栏导航（`Sidebar`）、任务卡片（`TaskCard`）

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
