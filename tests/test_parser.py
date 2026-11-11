import pytest
from pathlib import Path
from app.scraper import parse_profile_html, load_required_badges
from app.models import BadgeConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def badge_cfg():
    return load_required_badges()


def test_parse_complete_profile(badge_cfg):
    html = (FIXTURES_DIR / "sample_complete.html").read_text(encoding="utf-8")
    target_uuid = "be556f7b-ab4b-4be7-82fb-9be45202ddd7"
    res = parse_profile_html(html, target_uuid, badge_cfg)

    assert res.status == "complete"
    assert res.score == 10
    assert len(res.verified_badges) == 10
    assert len(res.missing_badges) == 0
    assert res.profile_name == "Litmus Dev"
    assert res.error_reason is None


def test_parse_partial_profile(badge_cfg):
    html = (FIXTURES_DIR / "sample_partial_6of10.html").read_text(encoding="utf-8")
    target_uuid = "8f0d00c3-7674-4042-ba15-d26a3fae84f9"
    res = parse_profile_html(html, target_uuid, badge_cfg)

    assert res.status == "partial"
    assert res.score == 6
    assert len(res.verified_badges) == 6
    assert len(res.missing_badges) == 4
    assert res.profile_name == "Anima Singh"


def test_parse_zero_badges_profile(badge_cfg):
    html = (FIXTURES_DIR / "sample_zero.html").read_text(encoding="utf-8")
    target_uuid = "2fafe410-5b6c-463b-9a1d-61dbfb43e9ac"
    res = parse_profile_html(html, target_uuid, badge_cfg)

    assert res.status == "none"
    assert res.score == 0
    assert len(res.verified_badges) == 0
    assert len(res.missing_badges) == 10
    assert res.profile_name == "Harin Faldu"


def test_parse_private_profile(badge_cfg):
    html = (FIXTURES_DIR / "sample_private.html").read_text(encoding="utf-8")
    target_uuid = "172773ae-1097-499b-a064-0be32bd06b81"
    res = parse_profile_html(html, target_uuid, badge_cfg)

    assert res.status == "private"
    assert res.score == 0
    assert "private" in res.error_reason.lower()


def test_parse_layout_changed(badge_cfg):
    html = (FIXTURES_DIR / "sample_layout_changed.html").read_text(encoding="utf-8")
    target_uuid = "some-uuid-1234"
    res = parse_profile_html(html, target_uuid, badge_cfg)

    assert res.status == "layout_changed"
    assert res.score == 0
    assert "Layout changed" in res.error_reason


def test_other_non_required_badges_not_counted(badge_cfg):
    target_uuid = "test-uuid-1111"
    custom_html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Test User | Google Skills</title></head>
    <body>
      <h1>Test User</h1>
      <div class="profile-badge">
        <a href="https://www.skills.google/public_profiles/{target_uuid}/badges/100"></a>
        <span class="ql-title-medium">Some Unrelated Badge</span>
        <span class="ql-body-medium">Earned Nov 1, 2026</span>
      </div>
      <div class="profile-badge">
        <a href="https://www.skills.google/public_profiles/{target_uuid}/badges/101"></a>
        <span class="ql-title-medium">Build a Website on Google Cloud</span>
        <span class="ql-body-medium">Earned Nov 2, 2026</span>
      </div>
    </body>
    </html>
    """
    res = parse_profile_html(custom_html, target_uuid, badge_cfg)
    assert res.score == 1
    assert len(res.verified_badges) == 1
    assert res.verified_badges[0]["title"] == "Build a Website on Google Cloud"
    assert len(res.other_badges) == 1
    assert res.other_badges[0]["title"] == "Some Unrelated Badge"


def test_foreign_profile_badge_links_ignored(badge_cfg):
    target_uuid = "my-profile-uuid"
    foreign_uuid = "someone-else-uuid"
    custom_html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Test User | Google Skills</title></head>
    <body>
      <h1>Test User</h1>
      <div class="profile-badge">
        <a href="https://www.skills.google/public_profiles/{foreign_uuid}/badges/999"></a>
        <span class="ql-title-medium">Build a Website on Google Cloud</span>
        <span class="ql-body-medium">Earned Nov 1, 2026</span>
      </div>
    </body>
    </html>
    """
    res = parse_profile_html(custom_html, target_uuid, badge_cfg)
    # Foreign link should not be counted
    assert res.score == 0
    assert len(res.verified_badges) == 0


def test_duplicate_badge_links_count_once(badge_cfg):
    target_uuid = "my-profile-uuid"
    custom_html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Test User | Google Skills</title></head>
    <body>
      <h1>Test User</h1>
      <div class="profile-badge">
        <a href="https://www.skills.google/public_profiles/{target_uuid}/badges/101"></a>
        <span class="ql-title-medium">Build a Website on Google Cloud</span>
        <span class="ql-body-medium">Earned Nov 1, 2026</span>
      </div>
      <div class="profile-badge">
        <a href="https://www.skills.google/public_profiles/{target_uuid}/badges/101"></a>
        <span class="ql-title-medium">Build a Website on Google Cloud</span>
        <span class="ql-body-medium">Earned Nov 1, 2026</span>
      </div>
    </body>
    </html>
    """
    res = parse_profile_html(custom_html, target_uuid, badge_cfg)
    assert res.score == 1
    assert len(res.verified_badges) == 1
