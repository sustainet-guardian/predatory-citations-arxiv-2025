#!/usr/bin/env python3
"""Counts the strict- and broad-predatory citations among the missed-keys rows (citations of the .bbl/.tex that the
citation log of the .bib file does not list), i.e. the entries_confirmed_all rows with added_missed_key_row = 1 (all
confirmed-cited), before and after the identity check. Strict: final_assessment 'predatory'; broad:
n_predatory_list_hits > 0. After the check, the strict set is the entries_confirmed view (cross-checked with the
entries_confirmed_valid_votes view) and the broad set excludes the per-citation broad drops. Also records the strict and
broad totals of the whole population after the identity check.
Reads EVAL_DB_PATH_BBL_TEX. Writes EVAL_OUTPUT_DIR/output_missed_keys_flag_counts/missed_keys_flag_counts.json and
numbers/missed_keys_flag_counts.numbers.json with
  missedkeys_current_n_population                  number of missed-keys rows;
  missedkeys_current_strict_before_n, _after_n     strict citations among them before / after the check;
  missedkeys_current_broad_before_n, _after_n      broad citations among them before / after the check.
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_missed_keys_flag_counts"
stats_common.OUTPUT_DIR = OUTPUT_DIR

MISSED = "e.added_missed_key_row = 1"
STRICT = "e.final_assessment = 'predatory'"
BROAD = "CAST(e.n_predatory_list_hits AS INTEGER) > 0"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    def n(table: str, where: str) -> int:
        return conn.execute(f"SELECT COUNT(*) n FROM {table} e WHERE {where}").fetchone()["n"]

    n_missed = n("entries_confirmed_all", MISSED)
    strict_before = n("entries_confirmed_all", f"{MISSED} AND {STRICT}")
    strict_after = n("entries_confirmed", f"{MISSED} AND {STRICT}")
    broad_before = n("entries_confirmed_all", f"{MISSED} AND {BROAD}")
    broad_after = n("entries_confirmed_all", f"{MISSED} AND {BROAD} AND {table_analyses.BROAD_NOT_DROPPED}")
    strict_after_valid_votes = n("entries_confirmed_valid_votes", f"{MISSED} AND e.final_assessment_valid_votes = 'predatory'")
    strict_total_after_check = n("entries_confirmed", STRICT)
    broad_total_after_check = n("entries_confirmed_all", f"{BROAD} AND {table_analyses.BROAD_NOT_DROPPED}")

    out = {
        "n_missed_keys": n_missed,
        "strict": {"before": strict_before, "after": strict_after, "after_via_valid_votes_view": strict_after_valid_votes},
        "broad": {"before": broad_before, "after": broad_after},
        "totals_after_identity_check": {"strict": strict_total_after_check, "broad": broad_total_after_check},
    }
    stats_common.write_json("missed_keys_flag_counts", out)
    print(json.dumps(out, indent=2))

    assert n_missed > 0, "no missed-keys rows"
    assert strict_after == strict_after_valid_votes, (strict_after, strict_after_valid_votes)
    assert strict_after <= strict_before and broad_after <= broad_before
    assert strict_after <= strict_total_after_check and broad_after <= broad_total_after_check

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    stats_common.write_numbers("missed_keys_flag_counts", {
        "missedkeys_current_n_population": i(n_missed),
        "missedkeys_current_strict_before_n": i(strict_before),
        "missedkeys_current_strict_after_n": i(strict_after),
        "missedkeys_current_broad_before_n": i(broad_before),
        "missedkeys_current_broad_after_n": i(broad_after),
    })


if __name__ == "__main__":
    main()
