#!/usr/bin/env python3
"""Retraction-risk tier thresholds and the tier functions. Reads no environment variable and no file.
Thresholds: RATE_THRESHOLDS (level, overall retraction rate %, recent retraction rate %) for venues with a positive
total_publications and COUNT_THRESHOLDS (level, total retractions, recent retractions) for the others. MIN_RECENT_PUBLICATIONS
is the number of recent publications from which the recent rate counts in
tier_without_recent_rate_below_min_publications. Used by retraction_risk_tiers.py and identity_check_headline_analyses.py."""
from __future__ import annotations

MIN_RECENT_PUBLICATIONS = 10

RATE_THRESHOLDS = [
    ("critical", 3.0, 4.0), ("high", 1.5, 2.5), ("moderate", 0.8, 1.2), ("low", 0.1, 0.2),
]
COUNT_THRESHOLDS = [
    ("critical", 21, 10), ("high", 11, 5), ("moderate", 6, 3), ("low", 2, 2),
]


def _tier(total_retractions, recent_retractions, total_publications, recent_publications, min_recent_pub: int) -> str:
    """Tier with the recent rate applied only when recent_publications >= min_recent_pub; values may arrive as strings."""
    tr = int(total_retractions) if total_retractions not in (None, "") else 0
    if tr == 0:
        return "none"
    rr = int(recent_retractions) if recent_retractions not in (None, "") else 0
    tp = int(total_publications) if total_publications not in (None, "") else None
    rp = int(recent_publications) if recent_publications not in (None, "") else None

    if tp and tp > 0:
        overall_rate = tr / tp * 100
        recent_rate = 0.0
        if rp and rp >= min_recent_pub:
            recent_rate = rr / rp * 100
        for level, rate_th, recent_th in RATE_THRESHOLDS:
            if overall_rate >= rate_th or recent_rate >= recent_th:
                return level
        return "note"

    for level, count_th, recent_count_th in COUNT_THRESHOLDS:
        if tr >= count_th or rr >= recent_count_th:
            return level
    return "note"


def tier_from_basis(total_retractions, recent_retractions, total_publications, recent_publications) -> str:
    """Tier from the thresholds above; the recent rate applies whenever recent_publications is positive."""
    return _tier(total_retractions, recent_retractions, total_publications, recent_publications, 1)


def tier_without_recent_rate_below_min_publications(total_retractions, recent_retractions, total_publications, recent_publications) -> str:
    """Same as tier_from_basis, but the recent-rate signal is ignored when recent_publications < MIN_RECENT_PUBLICATIONS
    (only the overall rate applies)."""
    return _tier(total_retractions, recent_retractions, total_publications, recent_publications, MIN_RECENT_PUBLICATIONS)
