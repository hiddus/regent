# Regent Desktop

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
