# Learn With Communities — Skill Badge Tracker

A lightweight, robust web application built for organizers of the Google Developer Group (GDG) **"Learn With Communities"** campaign. 

Organizers can upload a CSV list of participants' public Google Skills profile URLs. The tracker inspects each profile, verifies how many of the 10 required skill badges each participant has earned, streams progress in real time via Server-Sent Events (SSE), renders an interactive results dashboard with SVG charts and per-score links, and exports a report CSV preserving original row order.

A campaign is considered complete when a participant earns **10 of 10** required badges.

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
- Python 3.10+ (tested on Python 3.12 and 3.14)
- `pip`

### Installation
```bash
# Clone or navigate to the repository
cd gdg_lead_project

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running the App
```bash
# Run in development mode
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and visit: `http://127.0.0.1:8000`

### Running in Mock Mode (Offline Testing)
To test without hitting live Google Skills profiles:
```bash
MOCK_FETCH=1 uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Running the Test Suite
The complete test suite runs completely offline with pytest:
```bash
pytest tests/ -vv
```

---

## 2. CSV Input and Report Formats

### Input CSV Format
The input file must be a CSV file (up to 2 MB, max 1,000 rows). Columns:
```csv
Google Skills Profile URL,[INFO]
https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7,All skill badges completed
https://www.skills.google/public_profiles/8f0d00c3-7674-4042-ba15-d26a3fae84f9,Some skill badges completed
https://www.skills.google/public_profiles/2fafe410-5b6c-463b-9a1d-61dbfb43e9ac,None of the skill badges completed
https://www.skills.google/public_profiles/172773ae-1097-499b-a064-0be32bd06b81,Account which is now private
https://www.skills.google/public_profiles/9f130a25-e289-41ea-a90a-febbf37b8dc1,Randomly Chosen Profile
https://www.skills.google/public_profiles/f0633f18-fd81-4cc0-b375-1454da352885,Randomly Chosen Profile
```

- Accepts UTF-8 with or without BOM, `\n` or `\r\n`, quoted fields, and skips blank lines.
- Finds URL column by name case-insensitively; falls back to the first column.
- The `[INFO]` column on input is ignored and always computed from the live profile.
- Invalid URLs are never discarded: they are marked `Invalid URL` and included in the output report.
- Accepts `http/https`, `www` or no `www`, trailing slashes, query parameters, fragments, and legacy `cloudskillsboost.google` URLs. Canonicalizes to `https://www.skills.google/public_profiles/<lowercase-uuid>`.

### Report CSV Format
When downloading the report (`GET /api/report.csv`):
```csv
Google Skills Profile URL,[INFO]
https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7,All skill badges completed (10/10)
https://www.skills.google/public_profiles/8f0d00c3-7674-4042-ba15-d26a3fae84f9,Some skill badges completed (6/10)
https://www.skills.google/public_profiles/2fafe410-5b6c-463b-9a1d-61dbfb43e9ac,None of the skill badges completed (0/10)
https://www.skills.google/public_profiles/172773ae-1097-499b-a064-0be32bd06b81,Account which is now private
https://www.skills.google/public_profiles/9f130a25-e289-41ea-a90a-febbf37b8dc1,None of the skill badges completed (0/10)
https://www.skills.google/public_profiles/f0633f18-fd81-4cc0-b375-1454da352885,All skill badges completed (10/10)
```
- Original row order is strictly preserved, including duplicates and invalid rows.
- Encoded with **UTF-8 with BOM** (`\ufeff`) so Microsoft Excel opens it seamlessly.
- **CSV Formula Injection Guard**: Any cell starting with `=`, `+`, `-`, or `@` is prefixed with `'` to prevent spreadsheet formula injection attacks.
- Optional query parameters:
  - `?score_column=true`: Adds a 3rd column `Score`.
  - `?details=true`: Adds `Profile Name`, `Missing Badges`, and `Checked At`.

---

## 3. Badge Verification Rules

Badges are parsed directly from DOM tree elements using `selectolax` (Lexbor engine). A badge is **verified** only if **all** of the following conditions are met:
1. **Target Profile Link**: Its link matches `/public_profiles/<this profile's uuid>/badges/<id>` (preventing navigation or foreign profile badges from being counted).
2. **Title**: It contains a non-empty badge title.
3. **Earned Date**: It contains an "Earned `<date>`" string.
4. **Normalized Title Match**: The normalized badge title matches one of the required badges in `required_badges.json`.
5. **Deduplication**: Each badge is counted at most once (deduped by numeric badge id and normalized title).

### Title Normalization Algorithm
- Unicode NFKC normalization
- Lowercased
- Collapsed consecutive whitespace
- Dash variants (`-`, `–`, `—`, `−`) unified to standard hyphen `-`
- Trailing punctuation stripped (`.`, `,`, `;`, `:`, `!`, `?`, `_`, `-`)
- Matched exactly against normalized required badges.

### Editing Required Badges
The required badges are defined in `required_badges.json`:
```json
{
  "campaign_name": "Learn With Communities",
  "required_total": 10,
  "badges": [
    "Explore Generative AI in Agent Platform",
    "Store, Process, and Manage Data on Google Cloud - Console",
    "Using the Google Cloud Speech API",
    "Create Your First Gemini Enterprise Application",
    "Build a Website on Google Cloud",
    "Analyze Images with the Cloud Vision API",
    "Develop AI-Powered Prototypes in Google AI Studio",
    "Implement Speech and Language Solutions with Pre-trained APIs",
    "Configure Service Accounts and IAM Roles for Google Cloud",
    "The Basics of Google Cloud Compute"
  ]
}
```
No code changes are required to modify the badges. The raw verified badge names are persisted in SQLite, allowing statuses and scores to be recalculated if the list is updated.

---

## 4. Rate Limiting and Fetching Engine

All rate limiting, backoff, and concurrency logic is handcrafted using standard library primitives:

1. **TokenBucketLimiter**:
   - Shared globally across all concurrent sessions.
   - Default: 8.0 tokens/second with burst capacity of 8.0.
   - Concurrency capped using `asyncio.Semaphore(MAX_CONCURRENCY)` (default 8).
2. **AIMD Adaptive Slow-down**:
   - If 3 or more HTTP 429 errors occur within 5 seconds, the limiter triggers an adaptive slow-down: the rate is temporarily halved (e.g. to 4.0 req/s) for a 15-second cool-off window.
   - After the cool-off expires, the rate gradually recovers back to base rate (+1.0 req/s).
3. **In-Flight Request Deduplication**:
   - An in-flight map `uuid -> asyncio.Future` ensures that if multiple rows or multiple concurrent user sessions request the same profile simultaneously, exactly one HTTP request is dispatched and shared between all requesters.
4. **Retry Logic & Backoff**:
   - Retries on HTTP 429, 500, 502, 503, 504, timeouts, and transport errors.
   - Max 4 attempts with exponential backoff and full jitter: `uniform(0, min(MAX_BACKOFF, BASE_BACKOFF * 2^attempt))`.
   - Honours `Retry-After` header when provided.
   - Never retries on 404, 403, 410, or unparseable HTML.

---

## 5. Statuses and the `[INFO]` Text

| Condition | `[INFO]` Text in Report |
|---|---|
| Score = 10 | `All skill badges completed (10/10)` |
| 1 ≤ Score ≤ 9 | `Some skill badges completed (N/10)` |
| Score = 0 | `None of the skill badges completed (0/10)` |
| Private / Deleted Profile | `Account which is now private` |
| Bad / Non-profile URL | `Invalid URL` |
| Network Timeout | `Could not check: timeout` |
| 429 After Retries | `Could not check: rate limited` |
| 5xx Server Error After Retries | `Could not check: server error (HTTP <code>)` |
| Connection / Network Failure | `Could not check: network error` |
| Unparseable Page Structure | `Could not check: layout changed` |

---

## 6. Sessions, Caching and Re-upload Rules

- **No Accounts Required**: Sessions are identified by a cryptographically secure random cookie (`secrets.token_urlsafe(32)`), stored as an SHA-256 hash in SQLite.
- **Cookie Security**: `HttpOnly`, `SameSite=Lax`, and `Secure` when behind HTTPS.
- **Sliding Expiry**: Sessions expire after 2 hours (`SESSION_TTL_SECONDS=7200`) of inactivity. Every request or active scan automatically extends expiry.
- **Re-upload Caching Rules**:
  - `Complete (10/10)`: Skipped (marked `from_cache`) because earned badges do not disappear.
  - `Partial (1-9)` or `None (0)`: Re-checked.
  - `Private`: Re-checked (user may have toggled profile to public).
  - `Errors`: Re-checked.
  - `Removed URLs`: Dropped from session database.
  - `Force full re-scan` toggle: Can be enabled to bypass cache.
- **Background Cleanup**: An asynchronous task runs every 5 minutes (`CLEANUP_INTERVAL_SECONDS=300`) to delete expired sessions. It explicitly skips and extends sessions with an active scan.

---

## 7. User Interface & Dashboard

Built with vanilla HTML5, CSS3, and JavaScript (no heavy UI frameworks or external chart libraries):
- **Live Progress Graph**: Real-time progress bar (0–100%), ETA, speed counter, live status counters, and live activity feed.
- **Handcrafted SVG Pie/Donut Chart**: Interactive SVG charts with hover tooltips and click-to-scroll navigation.
- **Per-Score Buckets**: 11 collapsible sections (10/10 down to 0/10), plus Private and Error sections. Each section displays:
  - Mini SVG donut chart reflecting the bucket's share.
  - Clickable profile links (`rel="noopener noreferrer"`).
  - Missing required badges.
  - Score diff comparisons (e.g. `↑ Up from 4/10`, `No change`).
  - Search filter and "Copy Links" button.
- **Global Search & Filter**: Search by name, UUID, or badge title, with a toggle for changed scores only.

---

## 8. API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serves single-page UI |
| `GET /api/health` | GET | Health check, limiter stats, DB status |
| `GET /api/config` | GET | Active campaign and required badge list |
| `POST /api/upload` | POST | Multipart CSV upload; returns scan summary |
| `GET /api/progress` | GET | Server-Sent Events (SSE) live progress stream |
| `GET /api/status` | GET | Polling fallback for scan state and progress |
| `POST /api/scan/cancel` | POST | Cancels active scan cleanly |
| `GET /api/results` | GET | Detailed results grouped by score |
| `GET /api/report.csv` | GET | Download report CSV |
| `GET /api/report.json` | GET | Export full JSON report |
| `DELETE /api/session` | DELETE | Clears session data and removes cookie |

---

## 9. Configuration (Environment Variables)

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
| `CLEANUP_INTERVAL_SECONDS` | `300` | Cleanup job frequency (5 minutes) |
| `MAX_ROWS_PER_UPLOAD` | `1000` | Upload row limit |
| `MAX_UPLOAD_SIZE_BYTES` | `2097152` | Upload file size limit (2 MB) |
| `MOCK_FETCH` | `0` | Set `1` for offline mock fixture mode |

---

## 10. Design Decisions & Assumptions

1. **Badge List Confirmation**: The 10 badges configured in `required_badges.json` were extracted from the verified sample complete profile `be556f7b-ab4b-4be7-82fb-9be45202ddd7`. If the GDG organizers update the required list, editing `required_badges.json` immediately takes effect without code modification.
2. **Private Profile Redirection**: Google Skills redirects private profiles via HTTP 302 to the homepage (`https://www.skills.google/`). Our HTTP client captures 302/301 redirects and inspects landing page markers to correctly report `Account which is now private`.
3. **Data Retention**: SQLite databases store verified badges and metadata for up to 2 hours of user inactivity. Users can also immediately purge their data using the "Clear My Data" button (`DELETE /api/session`).
4. **Formula Injection Guard**: All cells in the exported CSV that begin with `=`, `+`, `-`, or `@` are safely prefixed with a single quote (`'`).
