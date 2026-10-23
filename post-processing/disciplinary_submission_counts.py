#!/usr/bin/env python3
"""Counts the submissions with at least one strict-predatory citation and with at least one broad-predatory citation, and the
number of submissions. Strict is counted in two ways (excluding the identity-check keys; from the valid-vote verdict view)
that must agree; broad is counted once (n_predatory_list_hits > 0 minus the broad drops). Uses the connection of
identity_check_table_analyses.py (EVAL_DB_PATH_BBL_TEX). Writes numbers/disciplinary_submission_counts.numbers.json
(keys discsubcount_*) and disciplinary_submission_counts.json in EVAL_OUTPUT_DIR/output_disciplinary_submission_counts
(read by disciplinary_table_and_venue_concentration.py). The strict count is over the strict population after the identity
check (view entries_confirmed: final_assessment = 'predatory' without the strict_exclude keys); the total is the number of
submissions of the view entries_confirmed_all.
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_disciplinary_submission_counts"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    n_submissions_total = conn.execute(
        "SELECT COUNT(DISTINCT month || '/' || arxiv_id) n FROM entries_confirmed_all"
    ).fetchone()["n"]

    # Counts with the exclusion tables: entries_confirmed leaves out the strict_exclude keys, broad_drops are subtracted.
    n_strict_by_exclusion = conn.execute(
        "SELECT COUNT(DISTINCT month || '/' || arxiv_id) n FROM entries_confirmed WHERE final_assessment='predatory'"
    ).fetchone()["n"]
    n_broad_by_exclusion = conn.execute(
        f"SELECT COUNT(DISTINCT month || '/' || arxiv_id) n FROM entries_confirmed_all e "
        f"WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED}"
    ).fetchone()["n"]

    # Strict counted a second time from the valid-vote verdict view (strict only).
    n_strict_by_valid_votes = conn.execute(
        "SELECT COUNT(DISTINCT month || '/' || arxiv_id) n FROM entries_confirmed_valid_votes "
        "WHERE final_assessment_valid_votes='predatory'"
    ).fetchone()["n"]

    out = {
        "n_submissions_total": n_submissions_total,
        "by_exclusion_tables": {"n_strict": n_strict_by_exclusion, "n_broad": n_broad_by_exclusion},
        "by_valid_vote_verdict_strict_only": {"n_strict": n_strict_by_valid_votes},
        "counts_agree_on_strict": n_strict_by_exclusion == n_strict_by_valid_votes,
        "n_strict": n_strict_by_exclusion,
        "n_broad": n_broad_by_exclusion,
    }
    stats_common.write_json("disciplinary_submission_counts", out)
    print(json.dumps(out, indent=2, default=str))
    assert n_strict_by_exclusion == n_strict_by_valid_votes, (
        f"The exclusion-table count ({n_strict_by_exclusion}) and the valid-vote-verdict count ({n_strict_by_valid_votes}) "
        f"differ for strict-predatory submissions."
    )

    stats_common.write_numbers("disciplinary_submission_counts", {
        "discsubcount_n_total": {"kind": "int", "value": n_submissions_total},
        "discsubcount_n_strict": {"kind": "int", "value": n_strict_by_exclusion},
        "discsubcount_n_broad": {"kind": "int", "value": n_broad_by_exclusion},
    })


if __name__ == "__main__":
    main()
