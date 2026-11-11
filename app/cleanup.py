import asyncio
import logging
import time

from app import config
from app.db import get_db

logger = logging.getLogger("lwc.cleanup")


async def run_cleanup_job(stop_event: asyncio.Event) -> None:
    """
    Background cleanup task: runs every CLEANUP_INTERVAL_SECONDS.
    Deletes sessions whose expires_at has passed.
    Skips sessions with a running scan and extends their expiry instead.
    """
    logger.info("Starting background session cleanup task")
    while not stop_event.is_set():
        try:
            # Wait for interval or stop event
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.CLEANUP_INTERVAL_SECONDS)
                break  # Stop event was set
            except asyncio.TimeoutError:
                pass  # Timeout elapsed, run cleanup

            await cleanup_expired_sessions()
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}", exc_info=True)


async def cleanup_expired_sessions() -> int:
    now = time.time()
    async with get_db() as conn:
        # First, find running scans that might have passed expires_at, and extend them
        running_sessions = await conn.execute_fetchall(
            """
            SELECT id_hash FROM sessions
            WHERE scan_state = 'running' AND expires_at <= ?
            """,
            (now,),
        )
        if running_sessions:
            extended_expires = now + config.SESSION_TTL_SECONDS
            for s in running_sessions:
                await conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE id_hash = ?",
                    (extended_expires, s["id_hash"]),
                )
            logger.info(f"Extended expiry for {len(running_sessions)} active scanning sessions.")

        # Delete expired sessions (scan_state != 'running' and expires_at <= now)
        cursor = await conn.execute(
            """
            DELETE FROM sessions
            WHERE scan_state != 'running' AND expires_at <= ?
            """,
            (now,),
        )
        deleted_count = cursor.rowcount
        await conn.commit()
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} expired sessions.")
        return deleted_count
