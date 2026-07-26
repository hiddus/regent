"""
Regent Run-Lease Reconciliation Job
Runs periodically to:
1. Mark leaked RUNNING runs (NULL started_at or >1h old) as FAILED
2. Report dead letters and stale goals
3. Log system health metrics
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("regent.reconcile")

# Ensure regent package is importable
sys.path.insert(0, "/usr/local/lib/python3.12/site-packages")

from sqlalchemy import text
from regent.config import get_settings
from regent.infrastructure.database import create_engine, create_session_factory

RECONCILE_INTERVAL = int(os.environ.get("RECONCILE_INTERVAL_SECONDS", "300"))  # 5 min default
RUN_LEAK_THRESHOLD_HOURS = 1


async def reconcile_runs(session_factory) -> dict:
    """Mark leaked RUNNING runs as FAILED."""
    stats = {"leaked_runs": 0, "dead_letters": 0, "stale_goals": 0}
    async with session_factory() as session:
        # Mark leaked runs as FAILED
        result = await session.execute(
            text(
                "UPDATE runs SET status='FAILED', finished_at=NOW(), "
                "result = jsonb_build_object('error', 'run leaked - auto-reconciled', "
                "'resolution', 'auto-reconcile', 'reconciled_at', NOW()::text) "
                "WHERE status='RUNNING' "
                "AND (started_at IS NULL OR started_at < NOW() - INTERVAL '1 hour')"
            )
        )
        if result.rowcount > 0:
            stats["leaked_runs"] = result.rowcount
            logger.warning(f"Reconciled {result.rowcount} leaked RUNNING runs -> FAILED")
        
        await session.commit()
        
        # Count dead letters
        dl_count = await session.scalar(
            text("SELECT count(*) FROM outbox_events WHERE status='DEAD_LETTER'")
        )
        stats["dead_letters"] = dl_count or 0
        if stats["dead_letters"] > 0:
            logger.warning(f"Dead letters: {stats['dead_letters']}")
        
        # Count stale goals (ACTIVE with NULL stage or no progress)
        stale = await session.scalar(
            text(
                "SELECT count(*) FROM goals WHERE status='ACTIVE' "
                "AND (metadata->>'execution_stage' IS NULL OR metadata->>'execution_stage' = '') "
                "AND created_at < NOW() - INTERVAL '1 hour'"
            )
        )
        stats["stale_goals"] = stale or 0
        if stats["stale_goals"] > 0:
            logger.warning(f"Stale ACTIVE goals (NULL stage): {stats['stale_goals']}")
        
        # System metrics
        pending = await session.scalar(
            text("SELECT count(*) FROM outbox_events WHERE status='PENDING'")
        )
        active = await session.scalar(
            text("SELECT count(*) FROM goals WHERE status='ACTIVE'")
        )
        achieved = await session.scalar(
            text("SELECT count(*) FROM goals WHERE status='ACHIEVED'")
        )
        running = await session.scalar(
            text("SELECT count(*) FROM runs WHERE status='RUNNING'")
        )
        
        logger.info(
            f"Health: goals={active}A/{achieved}AV, runs={running}R, "
            f"pending={pending}P, dead={stats['dead_letters']}D, "
            f"stale={stats['stale_goals']}S"
        )
    
    return stats


async def main():
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    
    logger.info(f"Run-Lease reconciler started (interval={RECONCILE_INTERVAL}s)")
    
    while True:
        try:
            await reconcile_runs(session_factory)
        except Exception as e:
            logger.error(f"Reconciliation error: {e}", exc_info=True)
        
        await asyncio.sleep(RECONCILE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
