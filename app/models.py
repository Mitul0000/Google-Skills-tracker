from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class BadgeConfig:
    campaign_name: str
    required_total: int
    badges: List[str]


@dataclass
class VerifiedBadge:
    badge_id: str
    title: str
    earned_date: str
    url: str


@dataclass
class ProfileScrapeResult:
    uuid: str
    status: str
    score: int
    verified_badges: List[Dict[str, str]]
    other_badges: List[Dict[str, str]]
    missing_badges: List[str]
    profile_name: Optional[str] = None
    error_reason: Optional[str] = None
    from_cache: bool = False


@dataclass
class ScanProgress:
    done: int
    total: int
    percent: float
    counts: Dict[str, int]
    current_rate: float
    eta_seconds: Optional[float]
    recent_errors: List[Dict[str, str]]
    state: str
