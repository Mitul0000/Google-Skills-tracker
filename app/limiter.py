import asyncio
import time
import logging

logger = logging.getLogger("lwc.limiter")


class TokenBucketLimiter:
    """
    Global token-bucket rate limiter with concurrency semaphore and adaptive slow-down (AIMD).
    Shared across all sessions.
    """

    def __init__(
        self,
        rate_per_sec: float = 8.0,
        burst_capacity: float = 8.0,
        max_concurrency: int = 8,
    ):
        self.base_rate = float(rate_per_sec)
        self.current_rate = float(rate_per_sec)
        self.burst_capacity = float(burst_capacity)
        self.tokens = float(burst_capacity)
        self.last_refill = time.monotonic()
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        self.lock = asyncio.Lock()

        # AIMD Adaptive slow-down tracking
        self.recent_429_timestamps = []
        self.cooloff_until = 0.0
        self.last_recovery_time = 0.0

    async def acquire(self) -> None:
        """
        Acquires one token from the token bucket, sleeping if necessary.
        """
        while True:
            async with self.lock:
                now = time.monotonic()

                # Check if in cooloff period; if cooloff expired, restore rate gradually
                # (+1.0 req/sec per second of elapsed time, instead of per acquire call)
                if self.cooloff_until > 0 and now >= self.cooloff_until:
                    if self.current_rate < self.base_rate:
                        if self.last_recovery_time == 0.0:
                            self.last_recovery_time = self.cooloff_until
                        elapsed_since_recovery = now - self.last_recovery_time
                        if elapsed_since_recovery >= 1.0:
                            steps = int(elapsed_since_recovery)
                            self.current_rate = min(self.base_rate, self.current_rate + steps * 1.0)
                            self.last_recovery_time += steps
                            logger.info(f"Limiter restoring rate to {self.current_rate:.1f} req/s")
                    if self.current_rate >= self.base_rate:
                        self.cooloff_until = 0.0
                        self.last_recovery_time = 0.0

                # Refill tokens
                elapsed = now - self.last_refill
                self.last_refill = now
                self.tokens = min(self.burst_capacity, self.tokens + elapsed * self.current_rate)

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                else:
                    needed = 1.0 - self.tokens
                    wait_time = needed / self.current_rate

            await asyncio.sleep(wait_time)

    def record_429(self) -> None:
        """
        Call when a 429 occurs. If several 429s occur in a short window (e.g. 3 in 5 seconds),
        halve the rate for a 15-second cooloff period (AIMD style).
        """
        now = time.monotonic()
        self.recent_429_timestamps.append(now)
        # Keep only timestamps in the last 5 seconds
        self.recent_429_timestamps = [
            t for t in self.recent_429_timestamps if now - t <= 5.0
        ]
        if len(self.recent_429_timestamps) >= 3:
            # Trigger adaptive slow-down
            self.current_rate = max(1.0, self.current_rate / 2.0)
            self.cooloff_until = now + 15.0
            self.last_recovery_time = 0.0
            self.recent_429_timestamps.clear()
            logger.warning(
                f"Adaptive slow-down triggered! Halved rate to {self.current_rate:.1f} req/s until {self.cooloff_until:.1f}"
            )

    def get_stats(self) -> dict:
        return {
            "base_rate": self.base_rate,
            "current_rate": self.current_rate,
            "burst_capacity": self.burst_capacity,
            "available_tokens": round(self.tokens, 2),
            "max_concurrency": self.max_concurrency,
            "is_throttled": time.monotonic() < self.cooloff_until,
        }
