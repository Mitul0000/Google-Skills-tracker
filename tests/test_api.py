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


@pytest.mark.asyncio
async def test_report_preserves_duplicates_and_exact_input_order(tmp_path, monkeypatch):
    """
    Verifies that:
    1. Duplicate rows in input are NOT dropped from the report.
    2. Exact input ordering (e.g. f063, 8f0d, be55, 2faf) is preserved in report.csv.
    3. Re-uploading skipped rows are ordered according to the new input list, not the old list.
    """
    test_db = str(tmp_path / "test_report_order.db")
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    monkeypatch.setattr(config, "MOCK_FETCH", True)
    await init_db()

    f063 = "https://www.skills.google/public_profiles/f0633f18-fd81-4cc0-b375-1454da352885"
    be55 = "https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    _2faf = "https://www.skills.google/public_profiles/2fafe410-5b6c-463b-9a1d-61dbfb43e9ac"

    # Input has 4 rows including a duplicate of f063
    csv_input_1 = f"Google Skills Profile URL,[INFO]\n{f063}\n{be55}\n{f063}\n{_2faf}\n".encode("utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/upload", files={"file": ("test.csv", csv_input_1, "text/csv")})
        assert resp.status_code == 200

        await asyncio.sleep(0.3)

        rep = await ac.get("/api/report.csv")
        lines = [l for l in rep.text.splitlines() if l.strip()]
        # Header + 4 data rows = 5 lines (duplicate f063 must be preserved!)
        assert len(lines) == 5
        assert f063 in lines[1]
        assert be55 in lines[2]
        assert f063 in lines[3]
        assert _2faf in lines[4]

        # Re-upload with different ordering: be55, _2faf, f063
        csv_input_2 = f"Google Skills Profile URL,[INFO]\n{be55}\n{_2faf}\n{f063}\n".encode("utf-8")
        resp2 = await ac.post("/api/upload", files={"file": ("test2.csv", csv_input_2, "text/csv")})
        assert resp2.status_code == 200

        await asyncio.sleep(0.3)

        rep2 = await ac.get("/api/report.csv")
        lines2 = [l for l in rep2.text.splitlines() if l.strip()]
        assert len(lines2) == 4
        # New order must strictly be be55, _2faf, f063
        assert be55 in lines2[1]
        assert _2faf in lines2[2]
        assert f063 in lines2[3]
