#!/usr/bin/env python3
"""Helper module (no main, no output) for the scripts that group submissions by discipline. Provides classify(), which maps a
primary arXiv category to Computer Science (cs.*), Physics (physics., astro-ph, hep-, cond-mat., gr-qc, nucl-, quant-ph),
Mathematics (math.*) or Other (any other category, and a submission without a known category), and the paths of the two
category databases, read from the environment when the function is called: snapshot_categories_db() returns
EVAL_SNAPSHOT_CATEGORIES_DB (snapshot database: every 2025 submission of the arXiv metadata snapshot, made by
build_snapshot_categories_db.py) and categories_db() returns EVAL_ARXIV_CATEGORIES_DB (API database: the primary categories
reported by the arXiv API for the confirmed-cited submissions only, made by fetch_arxiv_categories.py). Two databases exist
because the snapshot database covers every submission, which is needed for those without confirmed-cited entries, while the
API database holds only the confirmed-cited ones. Read by analyze_excluded_submissions,
citation_level_disciplinary_rates_by_group, disciplinary_composition_included_vs_excluded,
disciplinary_table_and_venue_concentration, identity_check_headline_analyses and second_vote_survivors_by_discipline (snapshot
database), and by math_jsc_broad_rate and watchlist_split_and_math_broad_rate (API database).
"""
from __future__ import annotations

from pathlib import Path

from stats_common import required_path_env

PHYSICS_PREFIXES = ("physics.", "astro-ph", "hep-", "cond-mat.", "gr-qc", "nucl-", "quant-ph")


def snapshot_categories_db() -> Path:
    return required_path_env("EVAL_SNAPSHOT_CATEGORIES_DB")


def categories_db() -> Path:
    return required_path_env("EVAL_ARXIV_CATEGORIES_DB")


def classify(primary_category: str | None) -> str:
    if not primary_category:
        return "Other"
    if primary_category.startswith("cs."):
        return "Computer Science"
    if primary_category.startswith("math."):
        return "Mathematics"
    if any(primary_category == p or primary_category.startswith(p) for p in PHYSICS_PREFIXES):
        return "Physics"
    return "Other"
