import pytest
from app.csv_io import (
    normalize_profile_url,
    parse_csv_upload,
    format_info_column,
    sanitize_csv_cell,
    generate_report_csv,
)


def test_normalize_profile_url_valid_variants():
    uuid = "be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    expected = f"https://www.skills.google/public_profiles/{uuid}"

    # standard https
    can, uid = normalize_profile_url(f"https://www.skills.google/public_profiles/{uuid}")
    assert can == expected and uid == uuid

    # http and no www
    can, uid = normalize_profile_url(f"http://skills.google/public_profiles/{uuid}")
    assert can == expected and uid == uuid

    # trailing slash, query params, hash fragment
    can, uid = normalize_profile_url(f"https://www.skills.google/public_profiles/{uuid}/?locale=en#badges")
    assert can == expected and uid == uuid

    # old cloudskillsboost.google domain
    can, uid = normalize_profile_url(f"https://www.cloudskillsboost.google/public_profiles/{uuid}")
    assert can == expected and uid == uuid

    # uppercase UUID
    can, uid = normalize_profile_url(f"https://www.skills.google/public_profiles/{uuid.upper()}")
    assert can == expected and uid == uuid


def test_normalize_profile_url_invalid():
    can, uid = normalize_profile_url("https://example.com/not-a-profile")
    assert can is None and uid is None

    can, uid = normalize_profile_url("not a url at all")
    assert can is None and uid is None

    can, uid = normalize_profile_url("")
    assert can is None and uid is None


def test_parse_csv_upload_with_bom_and_crlf():
    raw_csv = (
        "\ufeffGoogle Skills Profile URL,[INFO]\r\n"
        "https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7,All skill badges completed\r\n"
        "   \r\n"  # blank line
        "invalid-url-here,Invalid hint\r\n"
    ).encode("utf-8")

    rows = parse_csv_upload(raw_csv)
    assert len(rows) == 2
    # Row 1: valid
    assert rows[0][1] == "https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    assert rows[0][2] == "be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    # Row 2: invalid kept
    assert rows[1][0] == "invalid-url-here"
    assert rows[1][1] is None
    assert rows[1][2] is None


def test_format_info_column():
    assert format_info_column("complete", 10) == "All skill badges completed (10/10)"
    assert format_info_column("partial", 6) == "Some skill badges completed (6/10)"
    assert format_info_column("none", 0) == "None of the skill badges completed (0/10)"
    assert format_info_column("private", 0) == "Account which is now private"
    assert format_info_column("invalid", 0) == "Invalid URL"
    assert format_info_column("timeout", 0) == "Could not check: timeout"
    assert format_info_column("rate_limited", 0) == "Could not check: rate limited"
    assert format_info_column("server_error", 0, "HTTP 502") == "Could not check: server error (HTTP 502)"
    assert format_info_column("layout_changed", 0) == "Could not check: layout changed"


def test_formula_injection_guard():
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+cmd") == "'+cmd"
    assert sanitize_csv_cell("-2") == "'-2"
    assert sanitize_csv_cell("@test") == "'@test"
    assert sanitize_csv_cell("Normal Text") == "Normal Text"


def test_generate_report_csv_preserves_order():
    rows = [
        {"raw_url": "url-1", "status": "complete", "score": 10},
        {"raw_url": "url-2", "status": "partial", "score": 4},
        {"raw_url": "invalid-url", "status": "invalid", "score": 0},
    ]
    report_bytes = generate_report_csv(rows, include_score_column=True)
    report_text = report_bytes.decode("utf-8-sig")
    lines = report_text.strip().splitlines()

    assert lines[0] == "Google Skills Profile URL,[INFO],Score"
    assert "url-1,All skill badges completed (10/10),10" in lines[1]
    assert "url-2,Some skill badges completed (4/10),4" in lines[2]
    assert "invalid-url,Invalid URL,0" in lines[3]
