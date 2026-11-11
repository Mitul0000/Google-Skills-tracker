import random
import time
from typing import Optional


def compute_backoff(
    attempt: int,
    base: float = 1.0,
    max_backoff: float = 10.0,
    retry_after: Optional[float] = None,
) -> float:
    """
    Computes backoff with full jitter:
    If Retry-After is specified, use max(retry_after, base).
    Otherwise, sleep = random.uniform(0, min(max_backoff, base * 2 ** attempt)).
    """
    if retry_after is not None and retry_after > 0:
        return min(max_backoff, max(base, retry_after))

    ceiling = min(max_backoff, base * (2 ** attempt))
    return random.uniform(0, ceiling)


def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """
    Parses Retry-After header which can be either seconds or an HTTP-date.
    """
    if not header_value:
        return None
    header_value = header_value.strip()
    try:
        # Seconds format
        return float(header_value)
    except ValueError:
        pass

    # Try HTTP date format
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(header_value)
        diff = (dt.timestamp() - time.time())
        return max(0.0, diff)
    except Exception:
        return None
