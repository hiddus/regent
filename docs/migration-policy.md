# Alembic migration policy (P2-3)

## Rules

1. **All schema changes go through Alembic** under `core/migrations/versions/`.
2. **Never** mutate production/dev schema with one-off `check_*/fix_*.py` scripts.
3. Migration IDs are chronological: `YYYYMMDD_NNNN_short_slug.py`.
4. `down_revision` must form a single linear chain (no unmerged heads in main).
5. Prefer expandable migrations (add nullable → backfill → constrain) over rewrite-in-place.
6. Every migration that changes behavior should have a unit/integration assertion or
   a graduation harness check that would catch a missing upgrade.

## Stamp / history debt

Historical note: environments were previously `stamp`ed to `20260725_0029`.
Going forward:

- New environments: `alembic upgrade head`
- Existing stamped hosts: confirm current revision, then `alembic upgrade head`
  (do not re-stamp over pending revisions)
- Latest AAR-1 Foundation Contract revision: `20260727_0033_aar1_foundation_contract`
  (after Expand `20260727_0032`; independently rollbackable)

## Forbidden

- `op.execute("UPDATE ...")` inside random ops scripts against live DBs as the
  primary fix path
- Checking in root-level DB repair scripts (CI: `ops/check_repo_hygiene.py`)
