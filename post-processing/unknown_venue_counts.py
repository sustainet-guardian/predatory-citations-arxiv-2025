#!/usr/bin/env python3
"""Counts the confirmed-cited entries whose final assessment is "unknown", the number of distinct unknown venues, and the
share of those entries held by the 10 and the 100 most frequent unknown venues (the denominator of both shares is the number of
entries with final_assessment_valid_votes = 'unknown'). The verdict is the one on the valid votes, so the count differs from
the number of stored 'unknown' verdicts by the 2 audited citations whose verdict recomputed without the invalid votes is
'unknown'. Uses the connection of identity_check_table_analyses.py (EVAL_DB_PATH_BBL_TEX, view entries_confirmed_valid_votes). Writes
numbers/unknown_venue_counts.numbers.json (keys unknownvenues_*) and unknown_venue_counts.json in
EVAL_OUTPUT_DIR/output_unknown_venue_counts.
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_unknown_venue_counts"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    n_unknown_total = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE final_assessment_valid_votes='unknown'"
    ).fetchone()["n"]

    venue_counts = conn.execute(
        "SELECT COALESCE(matched_venue_name, venue_raw) AS venue, COUNT(*) n "
        "FROM entries_confirmed_valid_votes WHERE final_assessment_valid_votes='unknown' "
        "GROUP BY venue ORDER BY n DESC"
    ).fetchall()
    n_distinct_venues = len(venue_counts)

    top10_n = sum(r["n"] for r in venue_counts[:10])
    top100_n = sum(r["n"] for r in venue_counts[:100])

    out = {
        "n_unknown_total": n_unknown_total,
        "n_distinct_unknown_venues": n_distinct_venues,
        "top10_n": top10_n,
        "top10_pct": round(100.0 * top10_n / n_unknown_total, 1),
        "top100_n": top100_n,
        "top100_pct": round(100.0 * top100_n / n_unknown_total, 1),
    }
    stats_common.write_json("unknown_venue_counts", out)
    print(json.dumps(out, indent=2, default=str))

    # Word labels (topten/tophundred): digits are stripped from macro names.
    stats_common.write_numbers("unknown_venue_counts", {
        "unknownvenues_n_total": {"kind": "int", "value": n_unknown_total},
        "unknownvenues_n_distinct": {"kind": "int", "value": n_distinct_venues},
        "unknownvenues_topten_pct": {"kind": "ratio", "num": top10_n, "den": n_unknown_total, "decimals": 1},
        "unknownvenues_tophundred_pct": {"kind": "ratio", "num": top100_n, "den": n_unknown_total, "decimals": 1},
    })


if __name__ == "__main__":
    main()
