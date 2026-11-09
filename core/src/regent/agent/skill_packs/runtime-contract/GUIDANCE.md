# Runtime Contract Skill

- Read the active Runtime Profile before writing entry modules.
- Put the HTTP app object at `entry_module:entry_object` (default `src.app:app`).
- Smoke only Profile `smoke_routes` (and declared health/readiness). Default is `/` only.
- Never invent `/health` or `/ready` unless the Profile explicitly declares them — inventing them fails smoke when the checker probes declared routes only, and missing declared routes fails when the Profile requires them.
- For flask / exploratory Goals: serve `/` with a real response; start command must bind 127.0.0.1.
- Preview for flask/fastapi must be a **running process** (`preview_type=runtime`), not a static zip of HTML.
- Install and test commands must match Profile; do not skip required tests when `require_tests=true`.
- On build/smoke/preview failure envelopes in context: fix those exact errors before adding features.
