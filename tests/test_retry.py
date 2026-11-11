import pytest
from app.retry import compute_backoff, parse_retry_after


def test_retry_after_parsing():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("   5.5  ") == 5.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("invalid") is None


def test_backoff_computation_with_jitter():
    # Attempt 0: base 1.0 -> [0, 1.0]
    for _ in range(20):
        bo = compute_backoff(0, base=1.0, max_backoff=10.0)
        assert 0.0 <= bo <= 1.0

    # Attempt 2: base 1.0 * 2^2 = 4.0 -> [0, 4.0]
    for _ in range(20):
        bo = compute_backoff(2, base=1.0, max_backoff=10.0)
        assert 0.0 <= bo <= 4.0

    # Respects Retry-After header
    bo_header = compute_backoff(1, base=1.0, max_backoff=10.0, retry_after=7.5)
    assert bo_header == 7.5
