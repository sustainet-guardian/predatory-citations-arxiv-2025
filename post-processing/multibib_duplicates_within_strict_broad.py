#!/usr/bin/env python3
"""Counts the excess duplicate (month, arxiv_id, entry_key) rows, i.e. the same citation in several .bib files of one
submission, within the strict-predatory and the broad-predatory citations after the identity check, and the strict / broad
rates over all confirmed-cited entries. The strict population is the view entries_confirmed with final_assessment =
'predatory' (the stored verdicts without the strict_exclude keys of the identity check); the broad population is the entries
of entries_confirmed_all with at least one predatory-list hit that are not in broad_drops; the denominator of
multibib_current_strict_rate and multibib_current_broad_rate is all confirmed-cited entries (entries_confirmed_all). The keys
multibib_strict_rate_shift_pp and multibib_broad_rate_shift_pp are the changes of these two rates, in percentage points, when
the excess rows are removed from the numerator and from the denominator (the excess rows of all confirmed-cited entries are
counted with the same query). The excess rows are citation rows, not submissions: for each (month, arxiv_id, entry_key) group
with n_rows > 1 the query adds n_rows - 1.

Reads EVAL_DB_PATH_BBL_TEX and the audit keys under EVAL_OUTPUT_DIR (through
identity_check_table_analyses.get_conn_after_identity_check) and EVAL_OUTPUT_DIR/identity_audit_citation_detail.json (written by
identity_check_headline_analyses.py). Writes
numbers/multibib_duplicates_within_strict_broad.numbers.json (keys multibib_*) and
EVAL_OUTPUT_DIR/output_multibib_duplicates_within_strict_broad/multibib_duplicates_within_strict_broad.json.
"""
from __future__ import annotations

import json

import identity_check_table_analyses as table_analyses
import stats_common
from stats_common import required_path_env

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_multibib_duplicates_within_strict_broad"
stats_common.OUTPUT_DIR = OUTPUT_DIR


DUP_QUERY = """
SELECT COALESCE(SUM(n_rows - 1), 0) FROM (
    SELECT month, arxiv_id, entry_key, COUNT(*) AS n_rows
    FROM ({source})
    GROUP BY month, arxiv_id, entry_key HAVING COUNT(*) > 1
)
"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    n_strict = conn.execute("SELECT COUNT(*) FROM entries_confirmed WHERE final_assessment='predatory'").fetchone()[0]
    print(f"strict basis after the identity check: {n_strict}", flush=True)

    n_broad = conn.execute(
        "SELECT COUNT(*) FROM entries_confirmed_all e WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    ).fetchone()[0]
    print(f"broad basis after the identity check: {n_broad}", flush=True)

    strict_source = "SELECT month, arxiv_id, entry_key FROM entries_confirmed WHERE final_assessment='predatory'"
    broad_source = (
        "SELECT e.month AS month, e.arxiv_id AS arxiv_id, e.entry_key AS entry_key FROM entries_confirmed_all e "
        "WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "  AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    )

    n_strict_excess = conn.execute(DUP_QUERY.format(source=strict_source)).fetchone()[0]
    n_broad_excess = conn.execute(DUP_QUERY.format(source=broad_source)).fetchone()[0]

    corpus_confirmed_cited = conn.execute("SELECT COUNT(*) FROM entries_confirmed_all").fetchone()[0]
    corpus_excess = conn.execute(
        DUP_QUERY.format(source="SELECT month, arxiv_id, entry_key FROM entries_confirmed_all")).fetchone()[0]
    print(f"excess duplicate rows among all confirmed-cited entries: {corpus_excess}", flush=True)

    def shift_pp(n_flagged: int, n_excess: int) -> float:
        """Rate after collapsing the excess rows minus the rate before, in percentage points."""
        return round(
            100.0 * ((n_flagged - n_excess) / (corpus_confirmed_cited - corpus_excess) - n_flagged / corpus_confirmed_cited), 10)

    strict_shift_pp = shift_pp(n_strict, n_strict_excess)
    broad_shift_pp = shift_pp(n_broad, n_broad_excess)

    out = {
        "strict_basis": {
            "n_total": n_strict,
            "n_excess_duplicate_rows": n_strict_excess,
            "pct_of_strict_that_is_excess": round(100.0 * n_strict_excess / n_strict, 3) if n_strict_excess else 0.0,
        },
        "broad_basis": {
            "n_total": n_broad,
            "n_excess_duplicate_rows": n_broad_excess,
            "pct_of_broad_that_is_excess": round(100.0 * n_broad_excess / n_broad, 3) if n_broad_excess else 0.0,
        },
        "all_confirmed_cited": {"n_total": corpus_confirmed_cited, "n_excess_duplicate_rows": corpus_excess},
        "rate_shift_pp_after_collapsing": {"strict": strict_shift_pp, "broad": broad_shift_pp},
    }
    stats_common.write_json("multibib_duplicates_within_strict_broad", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("multibib_duplicates_within_strict_broad", {
        "multibib_strict_n_excess_rows": {"kind": "int", "value": n_strict_excess},
        "multibib_broad_n_excess_rows": {"kind": "int", "value": n_broad_excess},
        "multibib_current_strict_rate": {"kind": "ratio", "num": n_strict, "den": corpus_confirmed_cited, "decimals": 3},
        "multibib_current_broad_rate": {"kind": "ratio", "num": n_broad, "den": corpus_confirmed_cited, "decimals": 3},
        "multibib_strict_rate_shift_pp": {"kind": "float", "value": strict_shift_pp, "decimals": 6},
        "multibib_broad_rate_shift_pp": {"kind": "float", "value": broad_shift_pp, "decimals": 6},
    })


if __name__ == "__main__":
    main()
