#!/usr/bin/env python3
"""Retraction-risk tiers of the confirmed-cited entries. The tier is rate-based when the venue's total_publications is
positive (overall and recent retraction rate against thresholds) and count-based otherwise (total and recent retraction
counts). The script recomputes the recorded tier from the venue basis (retraction_risk_basis_all_entries.csv, written by
stats_retraction_risk_basis.py with --write-venue-table; per-run JSONL for venues whose citations carry differing basis
values), checks it against the recorded tier, and recomputes the tier with the recent rate ignored when
recent_publications < MIN_RECENT_PUBLICATIONS (thresholds, minimum and tier functions: retraction_tiers.py).
Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT, and retraction_risk_basis_all_entries.csv in EVAL_OUTPUT_DIR. Writes
retraction_risk_tiers.json (counts only) in EVAL_OUTPUT_DIR/output_retraction_risk_tiers, and
numbers/retraction_risk_tiers.numbers.json (keys retraction_*).
Denominators of the ratios: all confirmed-cited entries (the entries_confirmed view of EVAL_DB_PATH_BBL_TEX) for
retraction_naive_ever_retracted_pct and retraction_moderate_or_higher_pct; the entries with any recorded tier for
retraction_note_tier_pct_of_any_history; the citations whose recomputed tier is moderate or higher for
retraction_moderate_or_higher_rate_based_pct; the publication count of one venue for retraction_tiny_denominator_example_rate_pct.
retraction_tiny_denominator_n_venues is the number of venues whose tier changes when the recent rate is ignored below the
minimum (among the citations with recorded tier moderate or higher); retraction_min_recent_publications is that minimum.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stats_common  # noqa: E402

DB_PATH_BBL_TEX = stats_common.required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_retraction_risk_tiers"
BASIS_CSV = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "retraction_risk_basis_all_entries.csv"

stats_common.DB_PATH = DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR


def get_conn_bbl_tex() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed AS SELECT * FROM entries_confirmed_bbl_tex")
    return conn


stats_common.get_conn = get_conn_bbl_tex

from stats_common import get_conn, write_json  # noqa: E402
from retraction_tiers import MIN_RECENT_PUBLICATIONS, tier_from_basis, tier_without_recent_rate_below_min_publications  # noqa: E402
from stats_retraction_risk_basis import load_retraction_data  # noqa: E402

FLAGGED_TIERS = {"moderate", "high", "critical"}


def load_venue_basis() -> tuple[dict[str, dict], set[str]]:
    """Returns (venue -> basis dict, set of venues whose citations carry differing basis values); for the latter the
    basis is read per citation from the per-run results."""
    rows = list(csv.DictReader(BASIS_CSV.open()))
    by_venue: dict[str, set] = {}
    for r in rows:
        key = (r["basis"], r["total_retractions"], r["recent_retractions"], r["total_publications"], r["recent_publications"])
        by_venue.setdefault(r["venue"], set()).add(key)

    venue_basis = {}
    inconsistent_venues = set()
    for venue, keys in by_venue.items():
        if len(keys) == 1:
            basis, tr, rr, tp, rp = next(iter(keys))
            venue_basis[venue] = {"total_retractions": tr, "recent_retractions": rr, "total_publications": tp, "recent_publications": rp}
        else:
            inconsistent_venues.add(venue)
    return venue_basis, inconsistent_venues


def main() -> None:
    conn = get_conn()

    flagged = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, retraction_risk_level, "
        "COALESCE(matched_venue_name, venue_raw) AS venue "
        "FROM entries_confirmed WHERE retraction_risk_level IN ('moderate','high','critical')"
    ).fetchall()
    print(f"citations with recorded tier moderate or higher: {len(flagged)}", flush=True)

    venue_basis, inconsistent_venues = load_venue_basis()
    print(f"consistent-basis venues: {len(venue_basis)}, inconsistent (per-citation fallback needed): {len(inconsistent_venues)}", flush=True)

    fallback_targets = [
        (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        for r in flagged if r["venue"] in inconsistent_venues
    ]
    print(f"reading the backend results of {len(fallback_targets)} fallback citations ...", flush=True)
    fallback_data = load_retraction_data(fallback_targets)
    print(f"  recovered {len(fallback_data)} records", flush=True)

    # Recompute the tier from the basis fields and compare it with the recorded tier.
    n_match, n_mismatch = 0, 0
    per_citation = []
    for r in flagged:
        venue = r["venue"]
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        if venue in inconsistent_venues:
            data = fallback_data.get(key)
            if data is None:
                continue
            basis = {"total_retractions": data.get("total_retractions"), "recent_retractions": data.get("recent_retractions"),
                      "total_publications": data.get("total_publications"), "recent_publications": data.get("recent_publications")}
        else:
            basis = venue_basis.get(venue)
            if basis is None:
                continue

        recomputed = tier_from_basis(**basis)
        if recomputed == r["retraction_risk_level"]:
            n_match += 1
        else:
            n_mismatch += 1

        per_citation.append({
            "month": r["month"], "arxiv_id": r["arxiv_id"], "venue": venue,
            "recorded_tier": r["retraction_risk_level"], "basis": basis,
        })

    print(f"recomputed tier equals recorded tier: {n_match} match, {n_mismatch} mismatch (of {n_match + n_mismatch})", flush=True)

    # Recompute the tier of every citation with recorded tier moderate or higher, with the minimum-denominator rule.
    recorded_tier_counts = Counter()
    recomputed_tier_counts = Counter()
    changed = []
    for pc in per_citation:
        b = pc["basis"]
        recomputed_t = tier_without_recent_rate_below_min_publications(
            b["total_retractions"], b["recent_retractions"], b["total_publications"], b["recent_publications"])
        recorded_tier_counts[pc["recorded_tier"]] += 1
        recomputed_tier_counts[recomputed_t] += 1
        pc["recomputed_tier"] = recomputed_t
        if recomputed_t != pc["recorded_tier"]:
            changed.append(pc)

    changed_venues = {}
    for c in changed:
        changed_venues.setdefault(c["venue"], {"recorded_tier": c["recorded_tier"], "recomputed_tier": c["recomputed_tier"], "n_citations": 0})
        changed_venues[c["venue"]]["n_citations"] += 1

    n_flagged_recomputed = sum(1 for pc in per_citation if pc["recomputed_tier"] in FLAGGED_TIERS)

    # Venues and citations on the count-based path.
    count_based_venues = sorted({
        pc["venue"] for pc in per_citation
        if not (pc["basis"]["total_publications"] and int(pc["basis"]["total_publications"]) > 0)
    })
    count_based_citations = sum(
        1 for pc in per_citation
        if not (pc["basis"]["total_publications"] and int(pc["basis"]["total_publications"]) > 0)
    )

    # Path split of the citations whose recomputed tier is moderate or higher: rate path when the venue basis has
    # total_publications > 0, count path otherwise.
    def on_rate_path(pc) -> bool:
        return bool(pc["basis"]["total_publications"] and int(pc["basis"]["total_publications"]) > 0)

    flagged_recomputed = [pc for pc in per_citation if pc["recomputed_tier"] in FLAGGED_TIERS]
    n_flagged_rate_path = sum(1 for pc in flagged_recomputed if on_rate_path(pc))
    n_flagged_count_path = len(flagged_recomputed) - n_flagged_rate_path
    assert n_flagged_rate_path + n_flagged_count_path == n_flagged_recomputed

    out = {
        "recomputed_vs_recorded_tier": {"n_match": n_match, "n_mismatch": n_mismatch},
        "min_recent_publications": MIN_RECENT_PUBLICATIONS,
        "recorded_tier_distribution": dict(recorded_tier_counts),
        "recomputed_tier_distribution": dict(recomputed_tier_counts),
        "n_citations_tier_changed": len(changed),
        "n_venues_tier_changed": len(changed_venues),
        "n_flagged_citations_recomputed_tier": n_flagged_recomputed,
        "count_based_path": {
            "n_venues": len(count_based_venues), "n_citations": count_based_citations,
        },
    }
    write_json("retraction_risk_tiers", out)
    print(json.dumps(out, indent=2, default=str))

    # Share of entries with any retraction history, note-tier share, and rate-based-path share.
    n_total = conn.execute("SELECT COUNT(*) n FROM entries_confirmed").fetchone()["n"]
    n_any_retraction_history = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed WHERE is_retracted='1'"
    ).fetchone()["n"]
    risk_rows = conn.execute(
        "SELECT retraction_risk_level, COUNT(*) n FROM entries_confirmed WHERE retraction_risk_level IS NOT NULL "
        "GROUP BY retraction_risk_level"
    ).fetchall()
    risk_dist = {r["retraction_risk_level"]: r["n"] for r in risk_rows}
    n_with_any_tier = sum(risk_dist.values())
    n_note_tier = risk_dist.get("note", 0)
    # Citations with recorded tier moderate or higher that are on the rate-based path (total_publications > 0).
    n_rate_based_of_flagged = len(per_citation) - count_based_citations

    # Publication and retraction counts of the venue named below (rate numerator and denominator) and its number of
    # tier-changed citations.
    jpa_basis = next(
        (pc["basis"] for pc in per_citation
         if pc["venue"] == "Journal of Physics A Mathematical and General"
         and pc["basis"]["total_publications"]),
        None,
    )
    jpa_pub = int(jpa_basis["total_publications"]) if jpa_basis else 0
    jpa_retractions = int(jpa_basis["total_retractions"] or 0) if jpa_basis else 0
    jpa_n_citations = changed_venues.get("Journal of Physics A Mathematical and General", {}).get("n_citations", 0)

    numbers_payload = {
        "retraction_naive_ever_retracted_pct": {"kind": "ratio", "num": n_any_retraction_history, "den": n_total, "decimals": 2},
        "retraction_note_tier_pct_of_any_history": {"kind": "ratio", "num": n_note_tier, "den": n_with_any_tier, "decimals": 1},
        "retraction_moderate_or_higher_n": {"kind": "int", "value": n_flagged_recomputed},
        "retraction_moderate_or_higher_pct": {"kind": "ratio", "num": n_flagged_recomputed, "den": n_total, "decimals": 3},
        "retraction_count_fallback_n_citations": {"kind": "int", "value": count_based_citations},
        "retraction_count_fallback_n_venues": {"kind": "int", "value": len(count_based_venues)},
        "retraction_rate_based_n_citations": {"kind": "int", "value": n_rate_based_of_flagged},
        # retraction_rate_based_* refer to the citations with recorded tier moderate or higher;
        # retraction_moderate_or_higher_* refer to the recomputed tier.
        "retraction_originally_flagged_n_citations": {"kind": "int", "value": len(per_citation)},
        "retraction_moderate_or_higher_rate_based_n": {"kind": "int", "value": n_flagged_rate_path},
        "retraction_moderate_or_higher_count_based_n": {"kind": "int", "value": n_flagged_count_path},
        "retraction_moderate_or_higher_rate_based_pct": {
            "kind": "ratio", "num": n_flagged_rate_path, "den": n_flagged_recomputed, "decimals": 1,
        },
        "retraction_tiny_denominator_example_n_citations": {
            "kind": "int",
            "value": jpa_n_citations,
        },
        "retraction_tiny_denominator_n_venues": {"kind": "int", "value": len(changed_venues)},
        "retraction_min_recent_publications": {"kind": "int", "value": MIN_RECENT_PUBLICATIONS},
        "retraction_tiny_denominator_example_n_publications": {"kind": "int", "value": jpa_pub},
        "retraction_tiny_denominator_example_rate_pct": {
            "kind": "ratio", "num": jpa_retractions, "den": jpa_pub, "decimals": 4,
        },
    }
    stats_common.write_numbers("retraction_risk_tiers", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
