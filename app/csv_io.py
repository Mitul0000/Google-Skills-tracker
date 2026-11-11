import csv
import io
import re
from typing import List, Tuple, Optional, Dict, Any

UUID_REGEX = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def normalize_profile_url(raw_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Validates and normalizes profile URL.
    Matches: https://www.skills.google/public_profiles/<uuid>
    Accepts: http/https, www or no www, trailing slash, query string, #fragment,
    and old cloudskillsboost.google host.
    Returns: (canonical_url, lowercase_uuid) or (None, None) if invalid.
    """
    if not raw_url:
        return None, None

    cleaned = raw_url.strip()
    # Check if contains a valid UUID
    match = UUID_REGEX.search(cleaned)
    if not match:
        return None, None

    uuid_str = match.group(0).lower()

    # Verify domain is skills.google or cloudskillsboost.google
    if "skills.google" in cleaned.lower() or "cloudskillsboost.google" in cleaned.lower():
        canonical_url = f"https://www.skills.google/public_profiles/{uuid_str}"
        return canonical_url, uuid_str

    return None, None


def parse_csv_upload(file_bytes: bytes) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """
    Parses uploaded CSV bytes.
    Handles UTF-8 with or without BOM, \\n or \\r\\n, quoted fields, blank lines.
    Returns a list of tuples: (original_url, canonical_url, uuid_or_none)
    Rows that are invalid URLs are NOT dropped: (original_url, None, None).
    """
    # Decode handling UTF-8 with BOM automatically via utf-8-sig
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        return []

    # Find URL column case-insensitively
    url_col_idx = 0
    header_found = False
    for idx, col in enumerate(header):
        if "google skills profile url" in col.strip().lower():
            url_col_idx = idx
            header_found = True
            break

    # If first row was not a recognized header but contains a URL, consider it data
    rows_to_process = []
    if not header_found:
        first_row_val = header[0].strip() if header else ""
        can, uid = normalize_profile_url(first_row_val)
        if uid:
            rows_to_process.append(header)
        # Else the header was just some custom header without "google skills profile url"

    for r in reader:
        if r and any(cell.strip() for cell in r):
            rows_to_process.append(r)

    results: List[Tuple[str, Optional[str], Optional[str]]] = []
    for row in rows_to_process:
        raw_val = row[url_col_idx].strip() if url_col_idx < len(row) else ""
        canonical, uid = normalize_profile_url(raw_val)
        results.append((raw_val, canonical, uid))

    return results


def format_info_column(status: str, score: int, error_reason: Optional[str] = None) -> str:
    """
    Formats the [INFO] text according to Section 4.
    """
    status_lower = (status or "").lower()
    if status_lower == "complete":
        return "All skill badges completed (10/10)"
    elif status_lower == "partial":
        return f"Some skill badges completed ({score}/10)"
    elif status_lower == "none":
        return "None of the skill badges completed (0/10)"
    elif status_lower == "private":
        return "Account which is now private"
    elif status_lower == "invalid":
        return "Invalid URL"
    elif status_lower == "timeout":
        return "Could not check: timeout"
    elif status_lower == "rate_limited":
        return "Could not check: rate limited"
    elif status_lower.startswith("server_error"):
        if error_reason and "HTTP" in error_reason:
            return f"Could not check: server error ({error_reason})"
        return "Could not check: server error"
    elif status_lower == "network_error":
        return "Could not check: network error"
    elif status_lower == "layout_changed":
        return "Could not check: layout changed"
    elif status_lower == "error":
        if error_reason:
            return f"Could not check: {error_reason}"
        return "Could not check: error"

    # Default fallback
    if score == 10:
        return "All skill badges completed (10/10)"
    elif score > 0:
        return f"Some skill badges completed ({score}/10)"
    return f"None of the skill badges completed (0/10)"


def sanitize_csv_cell(value: str) -> str:
    """
    Guards against CSV / Formula injection:
    Prefix cells starting with = + - @ or tab/cr with a single quote.
    """
    if not value:
        return ""
    if value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def generate_report_csv(
    rows: List[Dict[str, Any]],
    include_score_column: bool = False,
    include_details: bool = False,
) -> bytes:
    """
    Generates report CSV:
    - Header: Google Skills Profile URL,[INFO]
    - One output row per input row in original order
    - UTF-8 with BOM for Excel compatibility
    - Guard against CSV formula injection
    """
    output = io.StringIO()
    # Write UTF-8 BOM is done at byte encoding level (\ufeff)
    
    headers = ["Google Skills Profile URL", "[INFO]"]
    if include_score_column:
        headers.append("Score")
    if include_details:
        headers.extend(["Profile Name", "Missing Badges", "Checked At"])

    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)

    for item in rows:
        url_val = item.get("raw_url") or item.get("url") or ""
        status = item.get("status", "")
        score = item.get("score", 0)
        error_reason = item.get("error_reason")

        info_text = format_info_column(status, score, error_reason)

        row_cells = [
            sanitize_csv_cell(url_val),
            sanitize_csv_cell(info_text),
        ]
        if include_score_column:
            row_cells.append(str(score))
        if include_details:
            name_val = item.get("profile_name") or ""
            missing_val = ", ".join(item.get("missing_badges") or [])
            checked_val = str(item.get("last_checked_at") or "")
            row_cells.extend([
                sanitize_csv_cell(name_val),
                sanitize_csv_cell(missing_val),
                sanitize_csv_cell(checked_val),
            ])

        writer.writerow(row_cells)

    # Return bytes with UTF-8 BOM
    return output.getvalue().encode("utf-8-sig")
