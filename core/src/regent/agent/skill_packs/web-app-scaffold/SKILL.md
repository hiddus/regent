---
name: web-app-scaffold
version: 1.0.0
title: Web App Scaffold
description: Minimal Flask/FastAPI scaffold with persistence and a first Journey. Deliver a runnable surface first.
applies_when: [todo, crud, persist, sqlite, notes, scaffold, flask, website, 待办, 笔记, 网站, 应用, 系统]
gap_codes: [ARTIFACT_INCOMPLETE, STATIC_FAILED, forbid-pure-static-backend, forbid-demo-shell]
anti_examples: [single-file dump without routes or templates]
---

# Web App Scaffold

Deliver a **runnable minimal surface first**, then deepen.

- Prefer a small layered layout: `src/app.py`, templates/static as needed, `requirements.txt`, `README.md`.
- Persist with SQLite when the goal mentions save/list/history.
- First Journey must create then list (or seed then read) real data — no fabricated empty shells.
- Avoid pure static backends that only `send_from_directory` a marketing page.
- Do not block delivery on exhaustive tests; get `index.html` + working routes on disk, then iterate.
