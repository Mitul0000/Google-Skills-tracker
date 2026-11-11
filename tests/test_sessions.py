import asyncio
import time
import pytest
from app import config
from app.db import init_db, get_db
from app.sessions import hash_session_token
from app.cleanup import cleanup_expired_sessions


@pytest.mark.asyncio
async def test_session_lifecycle_and_cleanup(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_sessions.db")
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    await init_db()

    now = time.time()
    # 1. Expired idle session
    s1_hash = hash_session_token("tok-1")
    # 2. Expired session with a running scan
    s2_hash = hash_session_token("tok-2")
    # 3. Active valid session
    s3_hash = hash_session_token("tok-3")

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO sessions (id_hash, created_at, last_active_at, expires_at, scan_state) VALUES (?, ?, ?, ?, 'idle')",
            (s1_hash, now - 8000, now - 8000, now - 100),
        )
        await conn.execute(
            "INSERT INTO sessions (id_hash, created_at, last_active_at, expires_at, scan_state) VALUES (?, ?, ?, ?, 'running')",
            (s2_hash, now - 8000, now - 8000, now - 100),
        )
        await conn.execute(
            "INSERT INTO sessions (id_hash, created_at, last_active_at, expires_at, scan_state) VALUES (?, ?, ?, ?, 'idle')",
            (s3_hash, now, now, now + 7200),
        )
        await conn.commit()

    # Run cleanup
    deleted = await cleanup_expired_sessions()
    assert deleted == 1  # Only s1 deleted

    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT id_hash, expires_at, scan_state FROM sessions")
        remaining_hashes = {r["id_hash"] for r in rows}
        assert s1_hash not in remaining_hashes
        assert s2_hash in remaining_hashes
        assert s3_hash in remaining_hashes

        # Check s2 got its expiry extended
        s2_row = next(r for r in rows if r["id_hash"] == s2_hash)
        assert s2_row["expires_at"] > now
