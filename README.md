# Learn With Communities — Skill Badge Tracker

[![Live App](https://img.shields.io/badge/Live_App-skills.digifello.dev-blue)](https://skills.digifello.dev)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A high-performance, respectful verification tool built for organizers of the Google Developer Group (GDG) **"Learn With Communities"** campaign.

Organizers can upload a participant CSV of public Google Skills profile URLs. The tracker inspects each profile, verifies completion of the 10 required skill badges, streams real-time progress via Server-Sent Events (SSE), provides an interactive analytics dashboard, and exports report spreadsheets with preserved input row order.

A campaign is considered complete when a participant earns **10 of 10** required badges.

**Live Instance:** [https://skills.digifello.dev](https://skills.digifello.dev)

---

## Key Features

- **Live Real-Time Streaming**: Live verification streaming over Server-Sent Events (SSE) with live ETA and rate counters.
- **Strict Row Order Preservation**: The output report maintains exact original input sequence, preserving duplicate rows and invalid entries.
- **Respectful Rate Limiting**: Built-in Token Bucket rate limiter (8 req/s) with concurrency caps and AIMD (Additive Increase / Multiplicative Decrease) adaptive slow-down.
- **Smart Result Caching**: Verified 10/10 profiles are cached to avoid redundant network requests across re-uploads; partial, private, or failed rows are re-checked.
- **Spreadsheet Security**: CSV export features automatic CSV formula injection defenses (`=`, `+`, `-`, `@` escaped).
- **Zero Permanent Tracking**: Ephemeral SQLite sessions with automatic cleanup after 2 hours of inactivity, plus instant data wipe button.

---

## Architecture Flow

```
[ Organizer ]
      │
      ▼  Upload CSV
[ Web UI (SPA) ]
      │
      ├─────────────────────── POST /api/upload ───────────────┐
      │                                                        ▼
      │                                             [ CSV Parser & Normalizer ]
      │                                                        │
      │                                                        ▼
      ├─────── GET /api/progress (SSE Stream) ────── [ Scanner Orchestrator ]
      │        ◄─── row & progress events ─────────    │  (Worker Tasks)
      │                                                ├── TokenBucketLimiter (8 req/s)
      │                                                ├── Concurrency Semaphore (8 in-flight)
      │                                                ├── Request Deduplication (Futures map)
      │                                                └── Retry / AIMD Backoff
      ▼                                                        │
[ Results Dashboard ]                                          ▼
 • Summary strip                                    [ Public Google Skills HTTP ]
 • Interactive SVG Pie Chart                                   │
 • 11 Score Buckets (10..0)                                    ▼
 • Download Report (CSV / JSON)                     [ SQLite DB with WAL mode ]
```

---

## 1. Quick Start

### Prerequisites
- Python 3.10+
- `pip`

### Installation
```bash
# Clone the repository
git clone https://github.com/Mitul0000/Google-Skills-tracker.git
cd Google-Skills-tracker

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running Locally
```bash
# Start the server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to `http://127.0.0.1:8000`.

### Running in Offline Mock Mode
To test without hitting live Google Skills profiles:
```bash
MOCK_FETCH=1 uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Running the Test Suite
```bash
pytest tests/ -vv
```

---

## 2. CSV Input and Report Formats

### Input CSV Specification
The input file must be a standard CSV file (up to 2 MB, max 1,000 rows). Columns:
```csv
Google Skills Profile URL,[INFO]
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000001,Participant A
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000002,Participant B
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000003,Participant C
```

- Accepts UTF-8 with or without BOM, `\n` or `\r\n`, quoted fields, and ignores blank lines.
- Finds URL column by name case-insensitively; falls back to the first column.
- Canonicalizes public profile URLs (`https://www.skills.google/public_profiles/<uuid>`).
- Accepts legacy `cloudskillsboost.google` URLs automatically.
- Invalid URLs are never discarded: they are flagged as `Invalid URL` and included in the output report.

### Report CSV Format
When downloading the report (`GET /api/report.csv`):
```csv
Google Skills Profile URL,[INFO]
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000001,All skill badges completed (10/10)
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000002,Some skill badges completed (6/10)
https://www.skills.google/public_profiles/00000000-0000-0000-0000-000000000003,None of the skill badges completed (0/10)
```
- Original row order is strictly preserved, including duplicates and invalid rows.
- Encoded with **UTF-8 with BOM** (`\ufeff`) for compatibility with Microsoft Excel and Google Sheets.
- **CSV Formula Injection Guard**: Cells beginning with `=`, `+`, `-`, or `@` are safely escaped with `'`.

---

## 3. Statuses and the `[INFO]` Text

| Condition | `[INFO]` Text in Report |
|---|---|
| Score = 10 | `All skill badges completed (10/10)` |
| 1 ≤ Score ≤ 9 | `Some skill badges completed (N/10)` |
| Score = 0 | `None of the skill badges completed (0/10)` |
| Private / Deleted Profile | `Account which is now private` |
| Bad / Non-profile URL | `Invalid URL` |
| Network Timeout | `Could not check: timeout` |
| Rate Limited After Retries | `Could not check: rate limited` |
| Server Error (5xx) | `Could not check: server error (HTTP <code>)` |
| Connection / Network Failure | `Could not check: network error` |
| Unparseable Page Structure | `Could not check: layout changed` |

---

## 4. Badge Verification Rules

Badges are parsed directly from public profile markup:
1. **Target Profile Validation**: Badge links must match `/public_profiles/<profile-uuid>/badges/<id>` to prevent counting badges from foreign or navigational links.
2. **Title Validation**: Must contain a valid title.
3. **Earned Date Validation**: Must contain an "Earned `<date>`" string.
4. **Normalized Title Match**: Matches against the campaign's required badges list using Unicode NFKC normalization, whitespace collapsing, and dash unification.
5. **Deduplication**: Badges are deduplicated by badge ID and normalized title.

Required badges are defined in `required_badges.json`.

---

## 5. Rate Limiting and Performance

- **Global Token Bucket**: Enforces a global rate limit of 8 req/s across all concurrent sessions.
- **Concurrency Limiting**: Uses `asyncio.Semaphore` (max 8 in-flight connections).
- **AIMD Adaptive Backoff**: Dynamically halves dispatch rates upon detecting consecutive HTTP 429 responses, recovering gradually.
- **Request Deduplication**: Re-uses single in-flight `asyncio.Future` promises for shared UUID requests across multiple rows or concurrent uploads.
- **Exponential Jitter Retries**: Automatically retries transient 429/5xx status codes up to 4 attempts with randomized jitter.

---

## 6. API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serves Single Page Application |
| `GET /api/health` | GET | Service health and rate limiter statistics |
| `GET /api/config` | GET | Active campaign settings and required badge list |
| `POST /api/upload` | POST | Upload CSV list; returns scan initial summary |
| `GET /api/progress` | GET | Server-Sent Events (SSE) live progress stream |
| `GET /api/status` | GET | Polling endpoint for scan status |
| `POST /api/scan/cancel` | POST | Cancels an ongoing scan |
| `GET /api/results` | GET | Complete profile result details grouped by score |
| `GET /api/report.csv` | GET | Export report as CSV |
| `GET /api/report.json` | GET | Export report as JSON |
| `DELETE /api/session` | DELETE | Purges session data and deletes cookie |

---

## 7. Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Server host |
| `PORT` | `8000` | Server port |
| `RATE_PER_SEC` | `8.0` | Global token bucket rate (requests/sec) |
| `BURST_CAPACITY` | `8.0` | Max burst capacity of token bucket |
| `MAX_CONCURRENCY` | `8` | Maximum concurrent HTTP connections |
| `MAX_ATTEMPTS` | `4` | Maximum retry attempts per URL |
| `BASE_BACKOFF` | `1.0` | Base exponential backoff (seconds) |
| `MAX_BACKOFF` | `10.0` | Maximum backoff cap (seconds) |
| `DATABASE_PATH` | `lwc_tracker.db` | SQLite database file location |
| `SESSION_TTL_SECONDS` | `7200` | Session lifetime (2 hours) |
| `CLEANUP_INTERVAL_SECONDS` | `300` | Cleanup task interval (5 minutes) |
| `MAX_ROWS_PER_UPLOAD` | `1000` | Maximum rows allowed per CSV upload |
| `MAX_UPLOAD_SIZE_BYTES` | `2097152` | Maximum upload size in bytes (2 MB) |
| `MOCK_FETCH` | `0` | Enable mock fixture fetching (`1` to enable) |

---

## License

This project is open-source under the [MIT License](LICENSE).
