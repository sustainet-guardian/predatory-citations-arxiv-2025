#!/usr/bin/env python3
"""On the broad-predatory citations that remain after the identity check, counts those flagged by exactly one watchlist, and
computes the Mathematics citation-level broad rate and its multiple of the corpus-wide broad rate.
Mathematics is assigned with classify() of disciplinary_classification.py from the API category database
EVAL_ARXIV_CATEGORIES_DB (primary categories reported by the arXiv API for the confirmed-cited submissions, made by
fetch_arxiv_categories.py). The snapshot database EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission, made by
build_snapshot_categories_db.py) is used by the disciplinary tables, which need the submissions without confirmed-cited entries;
here only confirmed-cited entries are counted. The Mathematics rate is over all confirmed-cited entries of the Mathematics
group (view entries_confirmed_all). Reads EVAL_DB_PATH_BBL_TEX and, in EVAL_OUTPUT_DIR, the audit keys (through
identity_check_headline_analyses.py) and identity_audit_citation_detail.json. Writes watchlist_split_and_math_broad_rate.json
to EVAL_OUTPUT_DIR/output_watchlist_split_and_math_broad_rate and
numbers/watchlist_split_and_math_broad_rate.numbers.json (keys backends_split_*, math_*)."""
from __future__ import annotations

import json
import sqlite3

import identity_check_headline_analyses as headline_analyses
import stats_common
from disciplinary_classification import categories_db, classify

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_watchlist_split_and_math_broad_rate"
CITATION_DETAIL_PATH = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "identity_audit_citation_detail.json"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def get_conn_with_broad_drops():
    """Connection of identity_check_headline_analyses with the temp table broad_drops (broad flags dropped by the identity check)."""
    conn, _ = headline_analyses.get_conn_with_exclusions()
    per_citation = json.loads(CITATION_DETAIL_PATH.read_text())
    headline_analyses.setup_broad_drops_table(conn, per_citation)
    return conn


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn_with_broad_drops()

    # Broad-predatory citations that do not drop after the identity check.
    broad_rows = conn.execute(
        "SELECT e.month AS month, e.arxiv_id AS arxiv_id, e.predatory_list_hits AS predatory_list_hits "
        "FROM entries_confirmed_all e "
        "WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "  AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    ).fetchall()
    n_broad_total = len(broad_rows)
    print(f"broad citations after the identity check: {n_broad_total}", flush=True)

    # Citations flagged by exactly one distinct watchlist.
    n_exactly_one = 0
    for r in broad_rows:
        hits = [h for h in (r["predatory_list_hits"] or "").split(",") if h]
        if len(set(hits)) == 1:
            n_exactly_one += 1

    split_summary = {
        "n_broad_total_after_identity_check": n_broad_total,
        "n_flagged_by_exactly_one_watchlist": n_exactly_one,
        "pct_flagged_by_exactly_one_watchlist": round(100.0 * n_exactly_one / n_broad_total, 4),
    }

    # Mathematics citation-level broad rate.
    cat_conn = sqlite3.connect(str(categories_db()))
    cat_conn.row_factory = sqlite3.Row
    cat_rows = cat_conn.execute("SELECT month, arxiv_id, primary_category, found FROM categories").fetchall()
    groups_by_submission = {
        (r["month"], r["arxiv_id"]): classify(r["primary_category"] if r["found"] else None)
        for r in cat_rows
    }

    all_confirmed_rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key FROM entries_confirmed_all"
    ).fetchall()
    n_math_entries = 0
    for r in all_confirmed_rows:
        key = (r["month"], r["arxiv_id"])
        if groups_by_submission.get(key) == "Mathematics":
            n_math_entries += 1

    n_math_broad_after_check = sum(
        1 for r in broad_rows if groups_by_submission.get((r["month"], r["arxiv_id"])) == "Mathematics"
    )

    math_broad_rate_after_check = 100.0 * n_math_broad_after_check / n_math_entries
    corpus_broad_rate_after_check = 100.0 * n_broad_total / conn.execute(
        "SELECT COUNT(*) FROM entries_confirmed_all"
    ).fetchone()[0]
    print(f"corpus-wide broad rate after the identity check: {round(corpus_broad_rate_after_check, 5)}%", flush=True)
    multiple_after_check = math_broad_rate_after_check / corpus_broad_rate_after_check

    math_summary = {
        "n_mathematics_confirmed_cited_entries": n_math_entries,
        "n_mathematics_broad_flagged_after_identity_check": n_math_broad_after_check,
        "mathematics_broad_rate_after_identity_check_pct": round(math_broad_rate_after_check, 5),
        "corpus_wide_broad_rate_after_identity_check_pct": round(corpus_broad_rate_after_check, 5),
        "multiple_after_identity_check": round(multiple_after_check, 2),
    }

    out = {"watchlist_corroboration_split": split_summary, "mathematics_broad_rate": math_summary}
    stats_common.write_json("watchlist_split_and_math_broad_rate", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("watchlist_split_and_math_broad_rate", {
        "backends_split_n_broad_total": {"kind": "int", "value": n_broad_total},
        "backends_split_pct_exactly_one": {"kind": "ratio", "num": n_exactly_one, "den": n_broad_total, "decimals": 1},
        "math_n_broad_corrected": {"kind": "int", "value": n_math_broad_after_check},
        "math_broad_multiple_corrected": {"kind": "float", "value": round(multiple_after_check, 1), "decimals": 1},
    })


if __name__ == "__main__":
    main()
