# Ops archive

One-off debugging / hotfix scripts previously littered the repository root and
release trees. They diluted delivery quality by normalizing "fix via temp script"
instead of migrations + tests.

## Layout

- `archive/oneoff/` — historical `check_*` / `fix_*` / `q*` / `verify_*` scripts
- Keep new operational tools documented here; do **not** put them back in repo root

## Rules

1. Product/runtime code lives under `core/`, `apps/`, `capabilities/`
2. Schema changes go through Alembic under `core/migrations/versions/`
3. Temporary diagnosis scripts belong in `ops/archive/oneoff/` (or a dated subfolder)
4. CI enforces root whitelist via `ops/check_repo_hygiene.py`
