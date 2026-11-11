import aiosqlite
import asyncio
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from app import config

_db_lock = asyncio.Lock()


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Async context manager for SQLite database connection.
    Configures WAL mode, foreign keys, and Row factory.
    """
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        yield conn


async def init_db() -> None:
    async with _db_lock:
        async with get_db() as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id_hash TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    scan_state TEXT NOT NULL DEFAULT 'idle',
                    scan_started_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY,
                    session_id_hash TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    state TEXT NOT NULL DEFAULT 'running',
                    FOREIGN KEY (session_id_hash) REFERENCES sessions(id_hash) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_scans_session_id ON scans(session_id_hash);

                CREATE TABLE IF NOT EXISTS scan_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id_hash TEXT NOT NULL,
                    row_order INTEGER NOT NULL,
                    raw_url TEXT NOT NULL,
                    canonical_url TEXT,
                    uuid TEXT,
                    FOREIGN KEY (session_id_hash) REFERENCES sessions(id_hash) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_scan_rows_session ON scan_rows(session_id_hash, row_order);

                CREATE TABLE IF NOT EXISTS profiles (
                    session_id_hash TEXT NOT NULL,
                    uuid TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    verified_badges TEXT NOT NULL DEFAULT '[]',
                    other_badges TEXT NOT NULL DEFAULT '[]',
                    missing_badges TEXT NOT NULL DEFAULT '[]',
                    profile_name TEXT,
                    error_reason TEXT,
                    prev_score INTEGER,
                    last_checked_at REAL NOT NULL,
                    row_order INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id_hash, uuid),
                    FOREIGN KEY (session_id_hash) REFERENCES sessions(id_hash) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_profiles_session_status ON profiles(session_id_hash, status);
                CREATE INDEX IF NOT EXISTS idx_profiles_session_order ON profiles(session_id_hash, row_order);
                """
            )
            # Startup cleanup: Reset any stuck 'running' sessions to 'idle'
            await conn.execute("UPDATE sessions SET scan_state = 'idle' WHERE scan_state = 'running'")
            await conn.execute("UPDATE scans SET state = 'cancelled' WHERE state = 'running'")
            await conn.commit()
