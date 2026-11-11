import asyncio
import time
import pytest
from app.limiter import TokenBucketLimiter


@pytest.mark.asyncio
async def test_token_bucket_rate_limit():
    # Limiter configured to 10 tokens per sec, burst 2
    limiter = TokenBucketLimiter(rate_per_sec=10.0, burst_capacity=2.0, max_concurrency=5)
    
    start = time.monotonic()
    # Acquire 5 tokens: 2 immediate burst, then 3 at 0.1s each -> ~0.3s
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.25, f"Elapsed time {elapsed} was too fast for rate 10/s"


@pytest.mark.asyncio
async def test_adaptive_slowdown():
    limiter = TokenBucketLimiter(rate_per_sec=8.0, burst_capacity=8.0, max_concurrency=4)
    assert limiter.current_rate == 8.0

    # Record 3 429 errors
    limiter.record_429()
    limiter.record_429()
    limiter.record_429()

    # Rate should be halved to 4.0
    assert limiter.current_rate == 4.0
    stats = limiter.get_stats()
    assert stats["is_throttled"] is True
