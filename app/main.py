import asyncio
import datetime
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from fastapi import (
    FastAPI,
    Request,
    Response,
    UploadFile,
    File,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import init_db, get_db
from app.cleanup import run_cleanup_job
from app.sessions import get_or_create_session, delete_session, hash_session_token
from app.csv_io import parse_csv_upload, generate_report_csv, normalize_profile_url
from app.scanner import (
    orchestrate_scan,
    scan_manager,
    global_limiter,
    close_http_client,
    load_required_badges,
)

# Logging configuration
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lwc.api")

cleanup_stop_event = asyncio.Event()
cleanup_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    await init_db()
    global cleanup_task
    cleanup_stop_event.clear()
    cleanup_task = asyncio.create_task(run_cleanup_job(cleanup_stop_event))
    yield
    # Shutdown
    logger.info("Shutting down application...")
    cleanup_stop_event.set()
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    await close_http_client()


app = FastAPI(
    title="Learn With Communities - Skill Badge Tracker",
    lifespan=lifespan,
)

# Mount static files
static_dir = config.BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Rate limiting per session for API abuse protection
_session_request_timestamps: Dict[str, List[float]] = {}
_session_rate_lock = asyncio.Lock()


async def check_session_api_rate(session_hash: str) -> None:
    now = time.time()
    async with _session_rate_lock:
        timestamps = _session_request_timestamps.get(session_hash, [])
        timestamps = [t for t in timestamps if now - t < 1.0]
        if len(timestamps) >= 30:  # 30 API calls per second per session cap
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": "rate_limited", "message": "Too many requests to API."},
            )
        timestamps.append(now)
        _session_request_timestamps[session_hash] = timestamps


@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        return HTMLResponse("<h1>LWC Tracker</h1>", status_code=200)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health_check():
    limiter_stats = global_limiter.get_stats()
    db_ok = True
    try:
        async with get_db() as conn:
            await conn.execute("SELECT 1")
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "error",
        "limiter": limiter_stats,
    }


@app.get("/api/config")
async def get_campaign_config(request: Request, response: Response):
    await get_or_create_session(request, response)
    badge_cfg = load_required_badges()
    return {
        "campaign_name": badge_cfg.campaign_name,
        "required_total": badge_cfg.required_total,
        "badges": badge_cfg.badges,
    }


@app.post("/api/upload")
async def upload_csv(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    force_rescan: bool = Query(False),
):
    raw_token, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    # 1. Check if scan already running for this session (Rule: 1 running scan per session -> 409)
    async with get_db() as conn:
        s_row = await conn.execute_fetchall(
            "SELECT scan_state FROM sessions WHERE id_hash = ?", (session_hash,)
        )
        if s_row and s_row[0]["scan_state"] == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "scan_in_progress",
                    "message": "A scan is already running for this session.",
                },
            )

    # 2. Check upload size cap (MAX_UPLOAD_SIZE_BYTES = 2MB)
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded file is empty."},
        )
    if len(content) > config.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "message": f"File exceeds maximum upload size of {config.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
            },
        )

    # 3. Parse CSV rows
    parsed_rows = parse_csv_upload(content)
    if not parsed_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_csv",
                "message": "File is empty or not a valid CSV format.",
            },
        )

    # 4. Check row cap (MAX_ROWS_PER_UPLOAD = 1000)
    if len(parsed_rows) > config.MAX_ROWS_PER_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "too_many_rows",
                "message": f"Upload exceeds maximum limit of {config.MAX_ROWS_PER_UPLOAD} rows.",
            },
        )

    # 5. Check cache vs to_fetch
    unique_uuids = set(uid for _, _, uid in parsed_rows if uid is not None)
    cached_uuids = set()
    req_badges = load_required_badges()

    if not force_rescan:
        async with get_db() as conn:
            c_rows = await conn.execute_fetchall(
                """
                SELECT uuid FROM profiles
                WHERE session_id_hash = ? AND status = 'complete' AND score = ?
                """,
                (session_hash, req_badges.required_total),
            )
            for r in c_rows:
                if r["uuid"] in unique_uuids:
                    cached_uuids.add(r["uuid"])

    to_fetch_count = len(unique_uuids) - len(cached_uuids)
    invalid_count = sum(1 for _, _, uid in parsed_rows if uid is None)

    # Update session scan state in DB
    now = time.time()
    scan_id = f"scan-{int(now)}"
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE sessions 
            SET scan_state = 'running', scan_started_at = ?
            WHERE id_hash = ?
            """,
            (now, session_hash),
        )
        await conn.execute(
            """
            INSERT INTO scans (id, session_id_hash, total, done, started_at, state)
            VALUES (?, ?, ?, 0, ?, 'running')
            """,
            (scan_id, session_hash, len(unique_uuids), now),
        )
        await conn.commit()

    # Launch background scan orchestration
    task = asyncio.create_task(
        orchestrate_scan(session_hash, parsed_rows, force_full_rescan=force_rescan)
    )
    scan_manager.active_tasks[session_hash] = task

    return {
        "status": "started",
        "total_rows": len(parsed_rows),
        "unique_profiles": len(unique_uuids),
        "to_fetch": to_fetch_count,
        "from_cache": len(cached_uuids),
        "invalid": invalid_count,
    }


@app.get("/api/progress")
async def progress_stream(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    queue = scan_manager.register_queue(session_hash)

    async def event_generator():
        try:
            # If there's an active or last known progress, send it immediately
            if session_hash in scan_manager.last_progress:
                lp = scan_manager.last_progress[session_hash]
                yield f"event: progress\ndata: {json.dumps(lp)}\n\n"

            while True:
                # Disconnect detection
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    evt = msg.get("event", "message")
                    data = msg.get("data", {})
                    yield f"event: {evt}\ndata: {json.dumps(data)}\n\n"
                    if evt == "complete":
                        break
                except asyncio.TimeoutError:
                    # Keep-alive comment
                    yield ": ping\n\n"
        finally:
            scan_manager.unregister_queue(session_hash, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/status")
async def get_status(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    async with get_db() as conn:
        s_row = await conn.execute_fetchall(
            "SELECT scan_state FROM sessions WHERE id_hash = ?", (session_hash,)
        )
        scan_state = s_row[0]["scan_state"] if s_row else "idle"

    last_prog = scan_manager.last_progress.get(session_hash)
    return {
        "scan_state": scan_state,
        "progress": last_prog,
    }


@app.post("/api/scan/cancel")
async def cancel_scan(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    if session_hash in scan_manager.cancel_events:
        scan_manager.cancel_events[session_hash].set()

    async with get_db() as conn:
        await conn.execute(
            "UPDATE sessions SET scan_state = 'idle' WHERE id_hash = ?",
            (session_hash,),
        )
        await conn.execute(
            """
            UPDATE scans SET state = 'cancelled', finished_at = ?
            WHERE session_id_hash = ? AND state = 'running'
            """,
            (time.time(), session_hash),
        )
        await conn.commit()

    return {"status": "cancelled"}


@app.get("/api/results")
async def get_results(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    async with get_db() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT uuid, url, status, score, verified_badges, other_badges,
                   missing_badges, profile_name, error_reason, prev_score,
                   last_checked_at, row_order
            FROM profiles
            WHERE session_id_hash = ?
            ORDER BY row_order ASC
            """,
            (session_hash,),
        )

    # Group results by score (10 down to 0) plus private and errors
    by_score: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(10, -1, -1)}
    private_list: List[Dict[str, Any]] = []
    errors_list: List[Dict[str, Any]] = []
    all_profiles: List[Dict[str, Any]] = []

    for r in rows:
        item = {
            "uuid": r["uuid"],
            "url": r["url"],
            "status": r["status"],
            "score": r["score"],
            "verified_badges": json.loads(r["verified_badges"]),
            "other_badges": json.loads(r["other_badges"]),
            "missing_badges": json.loads(r["missing_badges"]),
            "profile_name": r["profile_name"],
            "error_reason": r["error_reason"],
            "prev_score": r["prev_score"],
            "last_checked_at": r["last_checked_at"],
            "row_order": r["row_order"],
        }
        all_profiles.append(item)

        status_val = r["status"]
        if status_val == "private":
            private_list.append(item)
        elif status_val in ("complete", "partial", "none"):
            by_score[r["score"]].append(item)
        else:
            errors_list.append(item)

    total_count = len(all_profiles)
    complete_count = len(by_score[10])
    completion_rate = round((complete_count / max(1, total_count)) * 100, 1)
    
    valid_scores = [p["score"] for p in all_profiles if p["status"] in ("complete", "partial", "none")]
    avg_score = round(sum(valid_scores) / max(1, len(valid_scores)), 1) if valid_scores else 0.0

    return {
        "summary": {
            "total_rows": total_count,
            "unique_profiles": len(set(p["uuid"] for p in all_profiles if not p["uuid"].startswith("invalid"))),
            "complete_count": complete_count,
            "completion_rate": completion_rate,
            "average_score": avg_score,
            "private_count": len(private_list),
            "errors_count": len(errors_list),
        },
        "by_score": by_score,
        "private": private_list,
        "errors": errors_list,
        "all": all_profiles,
    }


@app.get("/api/report.csv")
async def download_report_csv(
    request: Request,
    response: Response,
    score_column: bool = Query(False),
    details: bool = Query(False),
):
    _, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    async with get_db() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT uuid, url, status, score, verified_badges, missing_badges,
                   profile_name, error_reason, last_checked_at, row_order
            FROM profiles
            WHERE session_id_hash = ?
            ORDER BY row_order ASC
            """,
            (session_hash,),
        )

    row_dicts = []
    for r in rows:
        row_dicts.append({
            "url": r["url"],
            "status": r["status"],
            "score": r["score"],
            "profile_name": r["profile_name"],
            "missing_badges": json.loads(r["missing_badges"]),
            "error_reason": r["error_reason"],
            "last_checked_at": datetime.datetime.fromtimestamp(
                r["last_checked_at"]
            ).strftime("%Y-%m-%d %H:%M:%S") if r["last_checked_at"] else "",
        })

    csv_bytes = generate_report_csv(
        row_dicts,
        include_score_column=score_column,
        include_details=details,
    )

    timestamp_str = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"learn-with-communities-report-{timestamp_str}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/report.json")
async def download_report_json(request: Request, response: Response):
    _, session_hash, _ = await get_or_create_session(request, response)
    await check_session_api_rate(session_hash)

    async with get_db() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT uuid, url, status, score, verified_badges, other_badges,
                   missing_badges, profile_name, error_reason, prev_score,
                   last_checked_at, row_order
            FROM profiles
            WHERE session_id_hash = ?
            ORDER BY row_order ASC
            """,
            (session_hash,),
        )

    results = []
    for r in rows:
        results.append({
            "uuid": r["uuid"],
            "url": r["url"],
            "status": r["status"],
            "score": r["score"],
            "verified_badges": json.loads(r["verified_badges"]),
            "other_badges": json.loads(r["other_badges"]),
            "missing_badges": json.loads(r["missing_badges"]),
            "profile_name": r["profile_name"],
            "error_reason": r["error_reason"],
            "prev_score": r["prev_score"],
            "last_checked_at": r["last_checked_at"],
        })

    return JSONResponse(content={"profiles": results})


@app.delete("/api/session")
async def clear_session_data(request: Request, response: Response):
    raw_token, session_hash, _ = await get_or_create_session(request, response)
    # Cancel running scans if any
    if session_hash in scan_manager.cancel_events:
        scan_manager.cancel_events[session_hash].set()

    await delete_session(raw_token, response)
    return {"status": "cleared", "message": "All session data has been deleted."}
