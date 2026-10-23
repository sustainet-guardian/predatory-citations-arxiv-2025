#!/usr/bin/env python3
"""Distribution of the number of broad-predatory citations per submission (1, 2, 3, 4 or more) over the submissions with at
least one. A citation is broad-predatory when n_predatory_list_hits > 0 and it is not among the broad drops of the identity
check. Counts over entries_confirmed_all (all confirmed-cited entries) through the connection of
identity_check_table_analyses.py (EVAL_DB_PATH_BBL_TEX). subbroadhist_n_fourplus counts submissions (groups of month and arxiv_id)
with four or more such citations, not citations. Writes numbers/per_submission_broad_count_distribution.numbers.json
(keys subbroadhist_*) and per_submission_broad_count_distribution.json in
EVAL_OUTPUT_DIR/output_per_submission_broad_count_distribution.
"""
from __future__ import annotations

import json
from collections import Counter

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_per_submission_broad_count_distribution"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    rows = conn.execute(
        f"SELECT e.month AS month, e.arxiv_id AS arxiv_id, "
        f"SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED} "
        f"THEN 1 ELSE 0 END) n_broad "
        f"FROM entries_confirmed_all e GROUP BY e.month, e.arxiv_id"
    ).fetchall()
    broad_counts = [r["n_broad"] for r in rows if r["n_broad"] > 0]
    n_submissions_with_broad = len(broad_counts)
    assert n_submissions_with_broad > 0, "no submission with a broad-predatory citation"

    exact_hist = Counter(broad_counts)
    n_exactly_one = exact_hist.get(1, 0)
    n_four_or_more = sum(n for count, n in exact_hist.items() if count >= 4)

    total_broad_citations = sum(broad_counts)
    n_broad_citations = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e "
        f"WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED}"
    ).fetchone()["n"]
    assert total_broad_citations == n_broad_citations, (
        f"per-submission counts sum to {total_broad_citations}, but {n_broad_citations} citations are broad-predatory"
    )

    out = {
        "n_submissions_with_at_least_one_broad_predatory_citation": n_submissions_with_broad,
        "total_broad_predatory_citations_check": total_broad_citations,
        "n_exactly_one": n_exactly_one,
        "n_four_or_more": n_four_or_more,
        "full_histogram_1_2_3_4plus": {
            "1": exact_hist.get(1, 0),
            "2": exact_hist.get(2, 0),
            "3": exact_hist.get(3, 0),
            "4+": n_four_or_more,
        },
    }
    stats_common.write_json("per_submission_broad_count_distribution", out)
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "subbroadhist_n_one": {"kind": "int", "value": n_exactly_one},
        "subbroadhist_n_fourplus": {"kind": "int", "value": n_four_or_more},
    }
    stats_common.write_numbers("per_submission_broad_count_distribution", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
