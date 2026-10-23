#!/usr/bin/env python3
"""Computes, for the Mathematics group, the share of broad-flagged cited references that belong to "Journal of Scientific
Computing" (JSC) and the citation-level broad rate with JSC excluded (the JSC citations are left out of the numerator and of
the denominator). Uses the connection of
watchlist_split_and_math_broad_rate.py; Mathematics is assigned with classify() and categories_db() of
disciplinary_classification.py from the API category database EVAL_ARXIV_CATEGORIES_DB (primary categories reported by the arXiv
API for the confirmed-cited submissions, made by fetch_arxiv_categories.py), not from the snapshot database
EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission, made by build_snapshot_categories_db.py): only confirmed-cited entries are
counted here, whereas the snapshot database also covers the submissions without confirmed-cited entries. The rates are over all
confirmed-cited entries of the Mathematics group (view entries_confirmed_all); the rate with JSC excluded is over the entries
of the group whose venue is not JSC. The Mathematics broad-flagged count is asserted equal to the one in
numbers/watchlist_split_and_math_broad_rate.numbers.json. Writes numbers/math_jsc_broad_rate.numbers.json (keys mathjsc_*) and math_jsc_broad_rate.json in
EVAL_OUTPUT_DIR/output_math_jsc_broad_rate.
"""
from __future__ import annotations

import json
import sqlite3

import stats_common
import watchlist_split_and_math_broad_rate as watchlist_split
from disciplinary_classification import categories_db, classify

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_math_jsc_broad_rate"
stats_common.OUTPUT_DIR = OUTPUT_DIR

CROSS_CHECK_PATH = stats_common.NUMBERS_DIR / "watchlist_split_and_math_broad_rate.numbers.json"
JSC_VENUE = "Journal of Scientific Computing"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = watchlist_split.get_conn_with_broad_drops()

    cat_conn = sqlite3.connect(str(categories_db()))
    cat_conn.row_factory = sqlite3.Row
    groups_by_submission = {
        (r["month"], r["arxiv_id"]): classify(r["primary_category"] if r["found"] else None)
        for r in cat_conn.execute("SELECT month, arxiv_id, primary_category, found FROM categories").fetchall()
    }

    # Broad-flagged confirmed-cited entries (list hits > 0) that are not among the broad flags dropped by the identity check.
    broad_rows = conn.execute(
        "SELECT e.month AS month, e.arxiv_id AS arxiv_id, "
        "COALESCE(e.matched_venue_name, e.venue_raw) AS venue "
        "FROM entries_confirmed_all e "
        "WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "  AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    ).fetchall()
    math_broad_rows = [r for r in broad_rows if groups_by_submission.get((r["month"], r["arxiv_id"])) == "Mathematics"]
    n_math_broad = len(math_broad_rows)

    cross_check = json.loads(CROSS_CHECK_PATH.read_text())
    n_math_broad_expected = cross_check["math_n_broad_corrected"]["value"]
    assert n_math_broad == n_math_broad_expected, (
        f"Mathematics broad-flagged count {n_math_broad} differs from the count {n_math_broad_expected} in "
        "watchlist_split_and_math_broad_rate.numbers.json."
    )

    n_math_jsc = sum(1 for r in math_broad_rows if r["venue"] == JSC_VENUE)
    n_math_broad_excl_jsc = n_math_broad - n_math_jsc

    math_entry_venues = [
        r["venue"] for r in conn.execute(
            "SELECT month, arxiv_id, COALESCE(matched_venue_name, venue_raw) AS venue FROM entries_confirmed_all"
        ).fetchall()
        if groups_by_submission.get((r["month"], r["arxiv_id"])) == "Mathematics"
    ]
    n_math_entries = len(math_entry_venues)
    n_math_entries_jsc = sum(1 for v in math_entry_venues if v == JSC_VENUE)
    n_math_entries_excl_jsc = n_math_entries - n_math_entries_jsc
    assert n_math_entries_jsc >= n_math_jsc
    math_broad_rate_incl_jsc = stats_common.pct(n_math_broad, n_math_entries)
    math_broad_rate_excl_jsc = stats_common.pct(n_math_broad_excl_jsc, n_math_entries_excl_jsc)

    corpus_broad_rate = json.loads(
        (watchlist_split.OUTPUT_DIR / "watchlist_split_and_math_broad_rate.json").read_text()
    )["mathematics_broad_rate"]["corpus_wide_broad_rate_after_identity_check_pct"]

    out = {
        "n_mathematics_broad_flagged_total": n_math_broad,
        "n_mathematics_broad_flagged_jsc": n_math_jsc,
        "pct_mathematics_broad_flagged_jsc": stats_common.pct(n_math_jsc, n_math_broad),
        "n_mathematics_broad_flagged_excluding_jsc": n_math_broad_excl_jsc,
        "n_mathematics_confirmed_cited_entries": n_math_entries,
        "n_mathematics_confirmed_cited_entries_jsc": n_math_entries_jsc,
        "n_mathematics_confirmed_cited_entries_excluding_jsc": n_math_entries_excl_jsc,
        "mathematics_broad_rate_including_jsc_pct": math_broad_rate_incl_jsc,
        "mathematics_broad_rate_excluding_jsc_pct": math_broad_rate_excl_jsc,
        "corpus_wide_broad_rate_pct": corpus_broad_rate,
        "excluding_jsc_below_corpus_baseline": math_broad_rate_excl_jsc < corpus_broad_rate,
    }
    stats_common.write_json("math_jsc_broad_rate", out)
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "mathjsc_pct_jsc": {"kind": "ratio", "num": n_math_jsc, "den": n_math_broad, "decimals": 1},
        "mathjsc_rate_excl_jsc_pct": {
            "kind": "ratio", "num": n_math_broad_excl_jsc, "den": n_math_entries_excl_jsc, "decimals": 3,
        },
    }
    stats_common.write_numbers("math_jsc_broad_rate", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
