import asyncio
import io
import pytest
from httpx import AsyncClient, ASGITransport
from app import config
from app.db import init_db
from app.main import app


@pytest.mark.asyncio
async def test_api_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_api_upload_oversized(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_api_oversized.db")
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_BYTES", 50)
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        big_content = b"Google Skills Profile URL\n" + b"https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7\n" * 10
        files = {"file": ("test.csv", big_content, "text/csv")}
        resp = await ac.post("/api/upload", files=files)
        assert resp.status_code == 413


@pytest.mark.asyncio
async def test_api_upload_and_results(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_api.db")
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    monkeypatch.setattr(config, "MOCK_FETCH", True)
    await init_db()

    csv_data = (
        "Google Skills Profile URL,[INFO]\n"
        "https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7,Complete\n"
        "https://www.skills.google/public_profiles/172773ae-1097-499b-a064-0be32bd06b81,Private\n"
    ).encode("utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        files = {"file": ("test.csv", csv_data, "text/csv")}
        resp = await ac.post("/api/upload", files=files)
        assert resp.status_code == 200
        start_data = resp.json()
        assert start_data["status"] == "started"
        assert start_data["unique_profiles"] == 2

        # Second concurrent scan in same session returns 409
        resp2 = await ac.post("/api/upload", files=files)
        assert resp2.status_code == 409

        # Wait briefly for mock scan to complete
        await asyncio.sleep(0.3)

        # Get results
        results_resp = await ac.get("/api/results")
        assert results_resp.status_code == 200
        res_json = results_resp.json()
        assert res_json["summary"]["complete_count"] == 1
        assert res_json["summary"]["private_count"] == 1

        # Download report CSV
        rep_resp = await ac.get("/api/report.csv")
        assert rep_resp.status_code == 200
        assert "All skill badges completed (10/10)" in rep_resp.text
        assert "Account which is now private" in rep_resp.text

        # Clear session
        del_resp = await ac.delete("/api/session")
        assert del_resp.status_code == 200
