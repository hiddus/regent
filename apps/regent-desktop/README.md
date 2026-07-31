# Regent Desktop

> **状态（与需求文档对齐）**：Tauri 桌面端是 **探索性非目标（PRD §12 / Technical-Spec §25）**，不计入 P0/P1/P2 验收范围；产品的正式交付面是 **Core（API/Worker）+ Web Console**（`apps/regent-console`）。本目录当前为骨架/参考实现，请勿据此判断产品成熟度。桌面端通过 iframe 内嵌 Web Console，默认连接 `http://localhost:8000`。
>
> 相应地，本目录**未纳入根 `compose.yaml` 的编排**（该文件仅含 `api` / `worker` / `postgres` 三项服务），也不参与 Core 的验收门禁。

Desktop application wrapper for Regent using Tauri.

## Prerequisites

- Node.js 18+ and npm
- Rust and Cargo (for Tauri)
- System dependencies for Tauri (see [Tauri documentation](https://tauri.app/v1/guides/getting-started/prerequisites))

## Installation

```bash
npm install
```

## Development

Start the development server:

```bash
npm run tauri dev
```

This will:
1. Start the Vite dev server on port 5173
2. Launch the Tauri desktop app
3. The app will connect to the Regent API at http://localhost:8000 by default

## Build

Build the desktop application:

```bash
npm run tauri build
```

The built application will be in `src-tauri/target/release/bundle/`.

## Configuration

The desktop app connects to the Regent API server. By default, it uses `http://localhost:8000`. You can change this in the app's header input field.

Make sure the Regent API server is running before using the desktop app:

```bash
cd ../..
docker compose up -d  # or however you start the API server
```

## Features

- Native desktop application
- Embedded Regent console via iframe
- Configurable API endpoint
- Dark theme matching the web console
- Cross-platform (Windows, macOS, Linux)

## Troubleshooting

### App won't start
- Make sure the Regent API server is running
- Check that port 5173 is available for the Vite dev server
- Verify Tauri prerequisites are installed

### Can't connect to API
- Ensure the API URL in the header is correct
- Check that the API server is accessible from your machine
- Verify CORS settings if running the API on a different origin
