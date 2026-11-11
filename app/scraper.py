import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set
from urllib.parse import urlparse
from selectolax.lexbor import LexborHTMLParser
import httpx

from app import config
from app.models import BadgeConfig, ProfileScrapeResult


def normalize_title(title: str) -> str:
    """
    Rule 3: Normalize titles before matching:
    - Unicode NFKC
    - lowercase
    - collapse whitespace
    - unify dash variants (-, –, —) to standard hyphen (-)
    - strip trailing punctuation
    """
    if not title:
        return ""
    # Unicode NFKC
    norm = unicodedata.normalize("NFKC", title)
    # lowercase
    norm = norm.lower()
    # unify dash variants
    norm = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", norm)
    # collapse whitespace
    norm = re.sub(r"\s+", " ", norm).strip()
    # strip trailing punctuation
    norm = re.sub(r"[.,;:!?'\"\-_]+$", "", norm).strip()
    return norm


def load_required_badges() -> BadgeConfig:
    path = Path(config.REQUIRED_BADGES_PATH)
    if not path.is_file():
        # Fallback default
        return BadgeConfig(
            campaign_name="Learn With Communities",
            required_total=10,
            badges=[],
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return BadgeConfig(
        campaign_name=data.get("campaign_name", "Learn With Communities"),
        required_total=data.get("required_total", 10),
        badges=data.get("badges", []),
    )


def parse_profile_html(
    html: str,
    target_uuid: str,
    required_badges: BadgeConfig,
) -> ProfileScrapeResult:
    """
    Parses Google Skills profile HTML according to Section 3.
    """
    target_uuid_lower = target_uuid.lower()
    tree = LexborHTMLParser(html)

    # 1. Check for layout changes vs empty vs valid profile
    title_el = tree.css_first("title")
    title_text = title_el.text().strip() if title_el else ""
    h1_el = tree.css_first("h1")
    h1_text = h1_el.text().strip() if h1_el else ""

    # Known private / marketing landing markers:
    # When a private profile URL is requested, Google redirects (302) to https://www.skills.google/
    # whose title is "Google Skills" and H1 is "Build AI skills for tomorrow, today" or "Google Cloud Skills Boost"
    is_marketing_landing = (
        ("Build AI skills for tomorrow, today" in h1_text)
        or (h1_text == "Google Cloud Skills Boost")
        or (title_text == "Google Skills" and "Build AI skills" in (tree.css_first("body").text() if tree.css_first("body") else ""))
    )
    if is_marketing_landing:
        return ProfileScrapeResult(
            uuid=target_uuid_lower,
            status="private",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(required_badges.badges),
            error_reason="Profile is private or redirected to landing page",
        )

    # Layout change detection (Rule 7):
    # A genuine profile has either:
    # - a profile header / user name in H1 or title containing "| Google Skills"
    # - or badge container elements like `.profile-badge` or `#badges` or `.public-profile-badges`
    has_profile_heading = bool(h1_text) and ("Google Skills" in title_text or "Skills Boost" in title_text)
    has_badge_container = bool(tree.css(".profile-badge") or tree.css("a[href*='/badges/']"))

    # Also check if text explicitly mentions private
    body_text = tree.css_first("body").text().lower() if tree.css_first("body") else ""
    if "this profile is private" in body_text or "profile is not public" in body_text or "user has not made their profile public" in body_text:
        return ProfileScrapeResult(
            uuid=target_uuid_lower,
            status="private",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(required_badges.badges),
            error_reason="Account which is now private",
        )

    if not has_profile_heading and not has_badge_container:
        return ProfileScrapeResult(
            uuid=target_uuid_lower,
            status="layout_changed",
            score=0,
            verified_badges=[],
            other_badges=[],
            missing_badges=list(required_badges.badges),
            error_reason="Layout changed (could not parse profile)",
        )

    profile_name = h1_text if h1_text else None

    # 2. Extract badges
    # Map required normalized title to original required badge title
    norm_to_required: Dict[str, str] = {
        normalize_title(b): b for b in required_badges.badges
    }

    verified_badges: List[Dict[str, str]] = []
    other_badges: List[Dict[str, str]] = []
    seen_badge_ids: Set[str] = set()
    seen_norm_titles: Set[str] = set()

    # Rule 1 & 2: A badge is verified only if:
    # (a) link points to /public_profiles/<this profile's uuid>/badges/<id>
    # (b) non-empty title
    # (c) has an "Earned" date
    # (d) normalized title matches one of required badges
    badge_divs = tree.css(".profile-badge")
    if not badge_divs:
        # Fallback to finding badge links directly
        badge_links = tree.css(f"a[href*='/public_profiles/{target_uuid_lower}/badges/']")
        # If no badge divs, we iterate on badge links
        for bl in badge_links:
            href = bl.attributes.get("href", "")
            parent = bl.parent
            if not parent:
                continue
            title_node = parent.css_first(".ql-title-medium") or parent.css_first("span, h3, h4")
            earned_node = parent.css_first(".ql-body-medium") or parent.css_first("span, p")
            # process similarly
            _process_badge_node(
                href=href,
                title_node=title_node,
                earned_node=earned_node,
                target_uuid_lower=target_uuid_lower,
                norm_to_required=norm_to_required,
                seen_badge_ids=seen_badge_ids,
                seen_norm_titles=seen_norm_titles,
                verified_badges=verified_badges,
                other_badges=other_badges,
            )
    else:
        for b_div in badge_divs:
            link_el = b_div.css_first(f"a[href*='/public_profiles/{target_uuid_lower}/badges/']")
            if not link_el:
                # Could be relative link or link without domain
                link_el = b_div.css_first("a[href*='/badges/']")
                if link_el:
                    href = link_el.attributes.get("href", "")
                    # Check if href contains target_uuid_lower
                    if f"/public_profiles/{target_uuid_lower}/badges/" not in href.lower():
                        continue
                else:
                    continue

            href = link_el.attributes.get("href", "")
            title_el = b_div.css_first(".ql-title-medium")
            earned_el = b_div.css_first(".ql-body-medium")
            _process_badge_node(
                href=href,
                title_node=title_el,
                earned_node=earned_el,
                target_uuid_lower=target_uuid_lower,
                norm_to_required=norm_to_required,
                seen_badge_ids=seen_badge_ids,
                seen_norm_titles=seen_norm_titles,
                verified_badges=verified_badges,
                other_badges=other_badges,
            )

    # Calculate missing badges
    verified_titles_set = {b["title"] for b in verified_badges}
    missing_badges = [
        b for b in required_badges.badges if b not in verified_titles_set
    ]

    score = len(verified_badges)
    if score == required_badges.required_total:
        status = "complete"
    elif score > 0:
        status = "partial"
    else:
        status = "none"

    return ProfileScrapeResult(
        uuid=target_uuid_lower,
        status=status,
        score=score,
        verified_badges=verified_badges,
        other_badges=other_badges,
        missing_badges=missing_badges,
        profile_name=profile_name,
        error_reason=None,
    )


def _process_badge_node(
    href: str,
    title_node,
    earned_node,
    target_uuid_lower: str,
    norm_to_required: Dict[str, str],
    seen_badge_ids: Set[str],
    seen_norm_titles: Set[str],
    verified_badges: List[Dict[str, str]],
    other_badges: List[Dict[str, str]],
) -> None:
    # Extract badge numeric ID
    # Pattern: /public_profiles/<uuid>/badges/<numeric-id>
    m = re.search(r"/public_profiles/([^/]+)/badges/(\d+)", href, re.IGNORECASE)
    if not m:
        return
    badge_uuid = m.group(1).lower()
    badge_id = m.group(2)

    # Condition 2(a): its link points to a /public_profiles/<this profile's uuid>/badges/<id> path
    if badge_uuid != target_uuid_lower:
        return

    # Condition 2(b): non-empty title
    title = title_node.text().strip() if title_node else ""
    if not title:
        return

    # Condition 2(c): Earned date
    earned_text = earned_node.text().strip() if earned_node else ""
    if not earned_text or "earned" not in earned_text.lower():
        return

    # Check dedupe by badge id (Rule 4)
    if badge_id in seen_badge_ids:
        return
    seen_badge_ids.add(badge_id)

    # Normalize title
    norm_t = normalize_title(title)
    if not norm_t:
        return

    badge_data = {
        "badge_id": badge_id,
        "title": title,
        "earned_date": earned_text,
        "url": href,
    }

    # Condition 2(d): normalized title matches one of the required badges
    if norm_t in norm_to_required:
        # Check dedupe by normalized title (Rule 4)
        if norm_t in seen_norm_titles:
            return
        seen_norm_titles.add(norm_t)
        # Store using the canonical required badge name
        canonical_title = norm_to_required[norm_t]
        badge_data["title"] = canonical_title
        verified_badges.append(badge_data)
    else:
        # Rule 5: non-required recorded as other_badges
        other_badges.append(badge_data)
