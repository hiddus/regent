# Runtime Contract Skill

- Read the active Runtime Profile before writing entry modules.
- Put the HTTP app object at `entry_module:entry_object` (default `src.app:app`).
- Only add health/readiness routes when the Profile declares them.
- Smoke routes come from the Profile `smoke_routes`, never invent `/health` unless declared.
- Install and test commands must match Profile; do not skip required tests.
