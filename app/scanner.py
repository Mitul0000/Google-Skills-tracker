import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Set
import httpx

from app import config
from app.db import get_db
from app.limiter import TokenBucketLimiter
from app.retry import compute_backoff, parse_retry_after
from app.scraper import parse_profile_html, load_required_badges
from app.models import ProfileScrapeResult, ScanProgress
from app.csv_io import format_info_column

logger = logging.getLogger("lwc.scanner")

# Global Limiter shared by all sessions
global_limiter = TokenBucketLimiter(
    rate_per_sec=config.RATE_PER_SEC,
    burst_capacity=config.BURST_CAPACITY,
    max_concurrency=config.MAX_CONCURRENCY,
)

# Global in-flight request deduplication: uuid -> asyncio.Future
in_flight_requests: Dict[str, asyncio.Future] = {}
in_flight_lock = asyncio.Lock()

# Global shared httpx AsyncClient
_shared_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        limits = httpx.Limits(
            max_connections=config.MAX_CONCURRENCY * 2,
            max_keepalive_connections=config.MAX_CONCURRENCY,
            keepalive_expiry=30.0,
        )
        timeout = httpx.Timeout(
            connect=5.0,
            read=10.0,
            write=10.0,
            pool=10.0,
        )
        _shared_client = httpx.AsyncClient(
            http2=True,
            limits=limits,
            timeout=timeout,
            headers={
                "User-Agent": "GDG-LearnWithCommunities-Tracker/1.0 (+https://skills.google)",
                "Accept-Encoding": "gzip",
            },
            follow_redirects=True,  # Follow redirects to reach the canonical landing or profile page
        )
    return _shared_client


async def close_http_client() -> None:
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


# Session scan state tracking for SSE / UI
class ScanManager:
    """
    Manages active scans per session, progress streaming queues, and cancellation events.
    """

    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.cancel_events: Dict[str, asyncio.Event] = {}
        self.progress_queues: Dict[str, List[asyncio.Queue]] = {}
        self.last_progress: Dict[str, Dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    def register_queue(self, session_hash: str) -> asyncio.Queue:
        q = asyncio.Queue()
        if session_hash not in self.progress_queues:
            self.progress_queues[session_hash] = []
        self.progress_queues[session_hash].append(q)
        return q

    def unregister_queue(self, session_hash: str, q: asyncio.Queue) -> None:
        if session_hash in self.progress_queues:
            if q in self.progress_queues[session_hash]:
                self.progress_queues[session_hash].remove(q)
            if not self.progress_queues[session_hash]:
                del self.progress_queues[session_hash]

    async def broadcast(self, session_hash: str, event_type: str, data: Any) -> None:
        if event_type == "progress":
            self.last_progress[session_hash] = data
        queues = self.progress_queues.get(session_hash, [])
        for q in list(queues):
            try:
                q.put_nowait({"event": event_type, "data": data})
            except Exception:
                pass

    def cleanup_session_memory(self, session_hash: str) -> None:
        """Removes session from in-memory tracking maps to prevent unbounded memory growth."""
        self.active_tasks.pop(session_hash, None)
        self.cancel_events.pop(session_hash, None)
        self.progress_queues.pop(session_hash, None)
        self.last_progress.pop(session_hash, None)


scan_manager = ScanManager()


async def fetch_profile_mock(uuid_str: str) -> ProfileScrapeResult:
    """
    Mock fetcher for testing and offline development (MOCK_FETCH=1).
    """
    await asyncio.sleep(0.05)  # simulate brief network delay
    req_badges = load_required_badges()
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

    # Known mock behaviors
    if uuid_str == "be556f7b-ab4b-4be7-82fb-9be45202ddd7":
        fixture_file = fixtures_dir / "sample_complete.html"
    elif uuid_str == "8f0d00c3-7674-4042-ba15-d26a3fae84f9":
        fixture_file = fixtures_dir / "sample_partial_6of10.html"
        if not fixture_file.is_file():
            fixture_file = fixtures_dir / "sample_partial.html"
    elif uuid_str == "2fafe410-5b6c-463b-9a1d-61dbfb43e9ac":
        fixture_file = fixtures_dir / "sample_zero.html"
    elif uuid_str == "172773ae-1097-499b-a064-0be32bd06b81":
        return ProfileScrapeResult(
            uuid=uuid_str,
            status="private",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(req_badges.badges),
            error_reason="Account which is now private",
        )
    elif uuid_str == "mock-429":
        return ProfileScrapeResult(
            uuid=uuid_str,
            status="rate_limited",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(req_badges.badges),
            error_reason="rate limited",
        )
    elif uuid_str == "mock-500":
        return ProfileScrapeResult(
            uuid=uuid_str,
            status="server_error",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(req_badges.badges),
            error_reason="HTTP 500",
        )
    elif uuid_str == "mock-timeout":
        return ProfileScrapeResult(
            uuid=uuid_str,
            status="timeout",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(req_badges.badges),
            error_reason="timeout",
        )
    elif uuid_str == "mock-layout-changed":
        fixture_file = fixtures_dir / "sample_layout_changed.html"
    elif uuid_str == "9f130a25-e289-41ea-a90a-febbf37b8dc1":
        fixture_file = fixtures_dir / "sample_random1.html"
    elif uuid_str == "f0633f18-fd81-4cc0-b375-1454da352885":
        fixture_file = fixtures_dir / "sample_random2.html"
    else:
        fixture_file = fixtures_dir / "sample_zero.html"

    if fixture_file.is_file():
        with open(fixture_file, "r", encoding="utf-8") as f:
            html = f.read()
        return parse_profile_html(html, uuid_str, req_badges)

    return ProfileScrapeResult(
        uuid=uuid_str,
        status="none",
        score=0,
        verified_badges=[],
        other_badges=[],
        missing_badges=list(req_badges.badges),
        error_reason=None,
    )


async def fetch_profile_live(uuid_str: str) -> ProfileScrapeResult:
    """
    Fetches and parses a single profile URL with token bucket rate limiting,
    concurrency control, retry logic, exponential backoff, and adaptive slowdown.
    Follows redirects and inspects final landing/profile page.
    """
    url = f"https://www.skills.google/public_profiles/{uuid_str}"
    client = get_http_client()
    req_badges = load_required_badges()

    last_error_type = "network_error"
    last_error_msg = ""

    for attempt in range(config.MAX_ATTEMPTS):
        # 1. Acquire token from TokenBucketLimiter
        await global_limiter.acquire()

        # 2. Acquire semaphore for concurrency cap
        async with global_limiter.semaphore:
            try:
                resp = await client.get(url)

                # Check for 404 / 403 / 410 -> Never retry, maps to private
                if resp.status_code in (403, 404, 410):
                    return ProfileScrapeResult(
                        uuid=uuid_str,
                        status="private",
                        score=0,
                        verified_badges=[],
                        other_badges=[],
                        missing_badges=list(req_badges.badges),
                        error_reason="Account which is now private",
                    )

                # Check for 200 OK
                if resp.status_code == 200:
                    # Check if final redirect landed on the marketing root rather than a profile
                    final_url = str(resp.url).lower()
                    if uuid_str.lower() not in final_url and (final_url.endswith(".google/") or final_url.endswith(".google")):
                        return ProfileScrapeResult(
                            uuid=uuid_str,
                            status="private",
                            score=0,
                            verified_badges=[],
                            other_badges=[],
                            missing_badges=list(req_badges.badges),
                            error_reason="Account which is now private",
                        )

                    html = resp.text
                    res = parse_profile_html(html, uuid_str, req_badges)
                    return res

                # Check for retryable HTTP errors (429, 500, 502, 503, 504)
                if resp.status_code in (429, 500, 502, 503, 504):
                    if resp.status_code == 429:
                        global_limiter.record_429()
                        last_error_type = "rate_limited"
                        last_error_msg = "rate limited"
                    else:
                        last_error_type = "server_error"
                        last_error_msg = f"HTTP {resp.status_code}"

                    retry_after = parse_retry_after(resp.headers.get("retry-after"))
                    backoff = compute_backoff(
                        attempt,
                        base=config.BASE_BACKOFF,
                        max_backoff=config.MAX_BACKOFF,
                        retry_after=retry_after,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Any other non-200 status code
                last_error_type = "server_error"
                last_error_msg = f"HTTP {resp.status_code}"
                return ProfileScrapeResult(
                    uuid=uuid_str,
                    status="server_error",
                    score=0,
                    verified_badges=[],
                    other_badges=[],
                    missing_badges=list(req_badges.badges),
                    error_reason=last_error_msg,
                )

            except (httpx.TimeoutException, asyncio.TimeoutError):
                last_error_type = "timeout"
                last_error_msg = "timeout"
                backoff = compute_backoff(
                    attempt, base=config.BASE_BACKOFF, max_backoff=config.MAX_BACKOFF
                )
                await asyncio.sleep(backoff)
            except (httpx.NetworkError, httpx.TransportError) as exc:
                last_error_type = "network_error"
                last_error_msg = f"network error: {str(exc)}"
                backoff = compute_backoff(
                    attempt, base=config.BASE_BACKOFF, max_backoff=config.MAX_BACKOFF
                )
                await asyncio.sleep(backoff)
            except Exception as exc:
                last_error_type = "network_error"
                last_error_msg = str(exc)
                backoff = compute_backoff(
                    attempt, base=config.BASE_BACKOFF, max_backoff=config.MAX_BACKOFF
                )
                await asyncio.sleep(backoff)

    # Exceeded max attempts
    return ProfileScrapeResult(
        uuid=uuid_str,
        status=last_error_type,
        score=0,
        verified_badges=[],
        other_badges=[],
        missing_badges=list(req_badges.badges),
        error_reason=last_error_msg,
    )


async def get_or_fetch_profile(uuid_str: str) -> ProfileScrapeResult:
    """
    Request deduplication: if two rows or two sessions need the same UUID at the same time,
    perform one request and share the result using in_flight_requests map.
    """
    async with in_flight_lock:
        if uuid_str in in_flight_requests:
            future = in_flight_requests[uuid_str]
        else:
            future = asyncio.get_running_loop().create_future()
            in_flight_requests[uuid_str] = future

            async def _worker():
                try:
                    if config.MOCK_FETCH:
                        res = await fetch_profile_mock(uuid_str)
                    else:
                        res = await fetch_profile_live(uuid_str)
                    future.set_result(res)
                except Exception as ex:
                    future.set_exception(ex)
                finally:
                    async with in_flight_lock:
                        if uuid_str in in_flight_requests:
                            del in_flight_requests[uuid_str]

            asyncio.create_task(_worker())

    return await asyncio.shield(future)


async def orchestrate_scan(
    session_id_hash: str,
    parsed_rows: List[Tuple[str, Optional[str], Optional[str]]],
    force_full_rescan: bool = False,
) -> None:
    """
    Executes scan for a session:
    - Stores the uploaded rows (position, raw URL, UUID) in scan_rows table
    - Diff against session cache
    - Queue workers
    - Save results into SQLite
    - Stream SSE progress and row events
    - Guaranteed cleanup in try/finally
    """
    cancel_event = asyncio.Event()
    scan_manager.cancel_events[session_id_hash] = cancel_event

    start_time = time.time()
    final_state = "error"
    done_unique_count = 0
    total_unique_count = 0
    status_counts = {
        "complete": 0,
        "partial": 0,
        "none": 0,
        "private": 0,
        "errors": 0,
        "from_cache": 0,
        "invalid": sum(1 for _, _, uid in parsed_rows if uid is None),
    }
    recent_errors: List[Dict[str, str]] = []

    try:
        # 1. Record uploaded input rows in scan_rows table (replacing old scan_rows for this session)
        async with get_db() as conn:
            await conn.execute("DELETE FROM scan_rows WHERE session_id_hash = ?", (session_id_hash,))
            for idx, (raw_url, can_url, uid) in enumerate(parsed_rows):
                await conn.execute(
                    """
                    INSERT INTO scan_rows (session_id_hash, row_order, raw_url, canonical_url, uuid)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id_hash, idx, raw_url, can_url, uid),
                )
            await conn.commit()

        # 2. Load existing cached profiles for session
        existing_profiles: Dict[str, Dict[str, Any]] = {}
        async with get_db() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT uuid, status, score, verified_badges, other_badges, missing_badges,
                       profile_name, error_reason
                FROM profiles
                WHERE session_id_hash = ?
                """,
                (session_id_hash,),
            )
            for r in rows:
                existing_profiles[r["uuid"]] = dict(r)

        # 3. Determine unique UUIDs in the new list and drop removed ones from cache
        new_list_uuids = set(uid for _, _, uid in parsed_rows if uid is not None)
        async with get_db() as conn:
            for old_uuid in list(existing_profiles.keys()):
                if old_uuid not in new_list_uuids:
                    await conn.execute(
                        "DELETE FROM profiles WHERE session_id_hash = ? AND uuid = ?",
                        (session_id_hash, old_uuid),
                    )
                    del existing_profiles[old_uuid]
            await conn.commit()

        # 4. Deduplicate unique UUIDs to fetch vs from_cache
        work_uuids: Set[str] = set()
        cached_results: Dict[str, ProfileScrapeResult] = {}
        req_badges = load_required_badges()

        for _, _, uid in parsed_rows:
            if not uid:
                continue
            if uid in existing_profiles and not force_full_rescan:
                cached = existing_profiles[uid]
                if cached["status"] == "complete" and cached["score"] == req_badges.required_total:
                    if uid not in cached_results:
                        cached_results[uid] = ProfileScrapeResult(
                            uuid=uid,
                            status="complete",
                            score=cached["score"],
                            verified_badges=json.loads(cached["verified_badges"]),
                            other_badges=json.loads(cached["other_badges"]),
                            missing_badges=json.loads(cached["missing_badges"]),
                            profile_name=cached["profile_name"],
                            error_reason=None,
                            from_cache=True,
                        )
                    continue
            work_uuids.add(uid)

        for c_res in cached_results.values():
            status_counts["complete"] += 1
            status_counts["from_cache"] += 1

        queue: asyncio.Queue = asyncio.Queue()
        for uid in work_uuids:
            queue.put_nowait(uid)

        done_unique_count = len(cached_results)
        total_unique_count = len(work_uuids) + len(cached_results)

        # Initial progress event
        await scan_manager.broadcast(
            session_id_hash,
            "progress",
            {
                "done": done_unique_count,
                "total": total_unique_count,
                "percent": round(done_unique_count / max(1, total_unique_count) * 100, 1),
                "counts": status_counts,
                "current_rate": 0.0,
                "eta_seconds": None,
                "recent_errors": recent_errors,
                "state": "running",
            },
        )

        # Emit row events for cached items
        for uid, c_res in cached_results.items():
            prev_score = existing_profiles[uid].get("prev_score") if uid in existing_profiles else None
            await scan_manager.broadcast(
                session_id_hash,
                "row",
                {
                    "uuid": uid,
                    "url": f"https://www.skills.google/public_profiles/{uid}",
                    "status": c_res.status,
                    "score": c_res.score,
                    "from_cache": True,
                    "profile_name": c_res.profile_name,
                    "prev_score": prev_score,
                    "error_reason": c_res.error_reason,
                },
            )

        # Worker tasks
        num_workers = min(config.MAX_CONCURRENCY, max(1, len(work_uuids)))
        db_write_lock = asyncio.Lock()

        async def worker():
            nonlocal done_unique_count
            while not queue.empty() and not cancel_event.is_set():
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                prev_score = existing_profiles.get(uid, {}).get("score")
                try:
                    res = await get_or_fetch_profile(uid)
                except Exception as e:
                    res = ProfileScrapeResult(
                        uuid=uid,
                        status="network_error",
                        score=0,
                        verified_badges=[],
                        other_badges=[],
                        missing_badges=list(req_badges.badges),
                        error_reason=str(e),
                    )

                done_unique_count += 1

                # Update status counts
                if res.status == "complete":
                    status_counts["complete"] += 1
                elif res.status == "partial":
                    status_counts["partial"] += 1
                elif res.status == "none":
                    status_counts["none"] += 1
                elif res.status == "private":
                    status_counts["private"] += 1
                else:
                    status_counts["errors"] += 1
                    recent_errors.append(
                        {"uuid": uid, "reason": res.error_reason or res.status}
                    )
                    if len(recent_errors) > 10:
                        recent_errors.pop(0)

                # Persist to database
                checked_time = time.time()
                async with db_write_lock:
                    async with get_db() as conn:
                        order_idx = next(
                            (i for i, (_, _, u) in enumerate(parsed_rows) if u == uid), 0
                        )
                        await conn.execute(
                            """
                            INSERT OR REPLACE INTO profiles
                            (session_id_hash, uuid, url, status, score, verified_badges, other_badges,
                             missing_badges, profile_name, error_reason, prev_score, last_checked_at, row_order)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                session_id_hash,
                                uid,
                                f"https://www.skills.google/public_profiles/{uid}",
                                res.status,
                                res.score,
                                json.dumps(res.verified_badges),
                                json.dumps(res.other_badges),
                                json.dumps(res.missing_badges),
                                res.profile_name,
                                res.error_reason,
                                prev_score,
                                checked_time,
                                order_idx,
                            ),
                        )
                        await conn.commit()

                # Stream row event
                await scan_manager.broadcast(
                    session_id_hash,
                    "row",
                    {
                        "uuid": uid,
                        "url": f"https://www.skills.google/public_profiles/{uid}",
                        "status": res.status,
                        "score": res.score,
                        "from_cache": False,
                        "profile_name": res.profile_name,
                        "prev_score": prev_score,
                        "error_reason": res.error_reason,
                    },
                )

                elapsed = max(0.1, time.time() - start_time)
                rate = done_unique_count / elapsed
                remaining = total_unique_count - done_unique_count
                eta = (remaining / rate) if rate > 0 else None

                await scan_manager.broadcast(
                    session_id_hash,
                    "progress",
                    {
                        "done": done_unique_count,
                        "total": total_unique_count,
                        "percent": round(
                            done_unique_count / max(1, total_unique_count) * 100, 1
                        ),
                        "counts": status_counts,
                        "current_rate": round(rate, 2),
                        "eta_seconds": round(eta, 1) if eta else None,
                        "recent_errors": recent_errors,
                        "state": "running",
                    },
                )
                queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(num_workers)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        final_state = "cancelled" if cancel_event.is_set() else "complete"

    except Exception as ex:
        logger.error(f"Error in orchestrate_scan for session {session_id_hash}: {ex}", exc_info=True)
        final_state = "error"
    finally:
        # Guaranteed reset of session state to idle upon completion, cancellation, or crash
        async with get_db() as conn:
            await conn.execute(
                "UPDATE sessions SET scan_state = 'idle' WHERE id_hash = ?",
                (session_id_hash,),
            )
            await conn.execute(
                """
                UPDATE scans
                SET done = ?, state = ?, finished_at = ?
                WHERE session_id_hash = ? AND state = 'running'
                """,
                (done_unique_count, final_state, time.time(), session_id_hash),
            )
            await conn.commit()

        # Emit final progress & complete events
        elapsed = max(0.1, time.time() - start_time)
        final_progress = {
            "done": done_unique_count,
            "total": total_unique_count,
            "percent": 100.0 if final_state == "complete" else round(done_unique_count / max(1, total_unique_count) * 100, 1),
            "counts": status_counts,
            "current_rate": round(done_unique_count / elapsed, 2),
            "eta_seconds": 0.0,
            "recent_errors": recent_errors,
            "state": final_state,
        }
        await scan_manager.broadcast(session_id_hash, "progress", final_progress)
        await scan_manager.broadcast(session_id_hash, "complete", final_progress)
