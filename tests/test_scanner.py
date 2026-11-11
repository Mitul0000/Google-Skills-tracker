import asyncio
import json
import pytest
from app import config
from app.db import init_db, get_db
from app.scanner import get_or_fetch_profile, orchestrate_scan
from app.models import ProfileScrapeResult


@pytest.mark.asyncio
async def test_inflight_deduplication(monkeypatch):
    monkeypatch.setattr(config, "MOCK_FETCH", True)
    uuid_to_test = "be556f7b-ab4b-4be7-82fb-9be45202ddd7"

    # Launch two concurrent requests for the same UUID
    t1 = asyncio.create_task(get_or_fetch_profile(uuid_to_test))
    t2 = asyncio.create_task(get_or_fetch_profile(uuid_to_test))

    res1, res2 = await asyncio.gather(t1, t2)
    assert res1.score == 10
    assert res2.score == 10
    assert res1.uuid == res2.uuid


@pytest.mark.asyncio
async def test_reupload_logic_caching(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_reupload.db")
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    monkeypatch.setattr(config, "MOCK_FETCH", True)
    await init_db()

    session_hash = "test-session-hash"
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO sessions (id_hash, created_at, last_active_at, expires_at) VALUES (?, 100, 100, 999999)",
            (session_hash,),
        )
        await conn.commit()

    complete_uuid = "be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    partial_uuid = "8f0d00c3-7674-4042-ba15-d26a3fae84f9"

    # First scan with complete and partial
    rows_scan1 = [
        (f"https://www.skills.google/public_profiles/{complete_uuid}", f"https://www.skills.google/public_profiles/{complete_uuid}", complete_uuid),
        (f"https://www.skills.google/public_profiles/{partial_uuid}", f"https://www.skills.google/public_profiles/{partial_uuid}", partial_uuid),
    ]

    await orchestrate_scan(session_hash, rows_scan1)

    # Check stored rows
    async with get_db() as conn:
        p_rows = await conn.execute_fetchall("SELECT uuid, status, score FROM profiles WHERE session_id_hash = ?", (session_hash,))
        assert len(p_rows) == 2

    # Second scan: complete should be skipped (from_cache), partial rechecked, and a removed URL should be dropped
    new_uuid = "2fafe410-5b6c-463b-9a1d-61dbfb43e9ac"
    rows_scan2 = [
        (f"https://www.skills.google/public_profiles/{complete_uuid}", f"https://www.skills.google/public_profiles/{complete_uuid}", complete_uuid),
        (f"https://www.skills.google/public_profiles/{new_uuid}", f"https://www.skills.google/public_profiles/{new_uuid}", new_uuid),
    ]

    await orchestrate_scan(session_hash, rows_scan2)

    async with get_db() as conn:
        p_rows2 = await conn.execute_fetchall("SELECT uuid, status, score FROM profiles WHERE session_id_hash = ?", (session_hash,))
        uuids = {r["uuid"] for r in p_rows2}
        assert complete_uuid in uuids
        assert new_uuid in uuids
        # partial_uuid was removed from the new list, so it was dropped
        assert partial_uuid not in uuids
