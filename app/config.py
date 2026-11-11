import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# Rate Limiter & Concurrency
RATE_PER_SEC = float(os.getenv("RATE_PER_SEC", "8.0"))
BURST_CAPACITY = float(os.getenv("BURST_CAPACITY", "8.0"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "8"))

# Retry & Backoff
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "4"))
BASE_BACKOFF = float(os.getenv("BASE_BACKOFF", "1.0"))
MAX_BACKOFF = float(os.getenv("MAX_BACKOFF", "10.0"))

# Database & Sessions
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "lwc_tracker.db"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "7200"))  # 2 hours
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))  # 5 minutes
MAX_ROWS_PER_UPLOAD = int(os.getenv("MAX_ROWS_PER_UPLOAD", "1000"))
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(2 * 1024 * 1024)))  # 2 MB

# Session Cookie
COOKIE_NAME = "lwc_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
COOKIE_SAMESITE = "lax"

# Mock Mode
MOCK_FETCH = os.getenv("MOCK_FETCH", "0").lower() in ("true", "1", "yes")

# Badges Config Path
REQUIRED_BADGES_PATH = os.getenv("REQUIRED_BADGES_PATH", str(BASE_DIR / "required_badges.json"))
