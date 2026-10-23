#!/usr/bin/env python3
"""Strict and broad predatory counts and rates of the population without and with the missed-keys rows (rows flagged
added_missed_key_row, inserted by insert_missed_keys_rows.py). Reads EVAL_DB_PATH_MISSED_KEYS_ROWS (the database that
contains the missed-keys rows; the population without them is the set of rows that are not flagged), EVAL_CACHE_DB_PATH and
EVAL_RESULTS_ROOT. The strict-predatory missed-keys rows are classified with the ISSN identity check of
full_corpus_identity_mismatch_audit.py (identity_vote_rules.classify_votes); a strict row with an invalid vote is evaluated
again without it, and a row that still resolves to 'predatory' stays strict (the audit's second-vote step).
The counts of the population without the missed-keys rows are derived: strict_count and broad_count of
numbers/strict_predatory_partition_tables.numbers.json are the counts after the identity check of the population with the
missed-keys rows, in which the citations included again by the ISSN-L check are still excluded; the counts of the
missed-keys rows after the identity check (numbers/missed_keys_flag_counts.numbers.json, strict and broad) are taken off and
the citations included again (restored_citation_keys of issn_l_family_check.json, written with --write-citation-keys) are
added; the script checks that each citation included again is in the database exactly once, has a predatory-list hit and is
not a missed-keys row. The strict count of the missed-keys rows from the classification of this script is checked against that file. The
counts with the missed-keys rows are strict_count and broad_count plus the citations included again. The rates divide by
all entries of the population in question (the rows of entries_confirmed_bbl_tex, with or without the flagged rows).
Writes rates_with_missed_keys_rows.json (population sizes, classification counts, counts and rates without and with the
missed-keys rows) to EVAL_OUTPUT_DIR/output_rates_with_missed_keys_rows.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import build_any_row_has_issn_index, classify_votes, invalid_backends, normalize_issn
from leave_one_out_helpers import load_records
import stats_common
from stats_common import pct, read_number, required_path_env, write_json

MISSED_KEYS_DB_PATH = required_path_env("EVAL_DB_PATH_MISSED_KEYS_ROWS")
CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_rates_with_missed_keys_rows"
ISSN_L_CHECK_JSON = required_path_env("EVAL_OUTPUT_DIR") / "output_issn_l_family_check" / "issn_l_family_check.json"

stats_common.DB_PATH = MISSED_KEYS_DB_PATH
stats_common.OUTPUT_DIR = OUTPUT_DIR


def classify_missed_keys_rows(cache_conn, rows: list[sqlite3.Row], records: dict) -> list[dict]:
    """Applies the ISSN identity classification (identity_vote_rules.classify_votes) to predatory-flagged rows."""
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    out = []
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        citing_issn = normalize_issn(r["issn"])
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]
        rec = records.get(key)

        if citing_issn is None:
            out.append({"key": key, "verdict": "unverifiable_no_citing_issn", "winners": winners})
            continue
        if rec is None:
            out.append({"key": key, "verdict": "missing_backend_record", "winners": winners})
            continue

        per_backend, verdict = classify_votes(rec, winners, citing_issn, any_row_has_issn)
        out.append({"key": key, "winners": winners, "per_backend": per_backend, "verdict": verdict})
    return out


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(MISSED_KEYS_DB_PATH))
    conn.row_factory = sqlite3.Row
    cache_conn = sqlite3.connect(str(CACHE_DB_PATH))

    n_with_missed_keys_rows = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_bbl_tex").fetchone()["n"]
    n_missed_keys_rows = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_bbl_tex WHERE added_missed_key_row='1'").fetchone()["n"]
    n_without_missed_keys_rows = n_with_missed_keys_rows - n_missed_keys_rows
    assert n_missed_keys_rows > 0 and n_without_missed_keys_rows > 0

    if not ISSN_L_CHECK_JSON.exists():
        raise SystemExit(f"{ISSN_L_CHECK_JSON} is missing; run issn_l_family_check.py --write-citation-keys first")
    issn_l_check = json.loads(ISSN_L_CHECK_JSON.read_text())
    if "restored_citation_keys" not in issn_l_check:
        raise SystemExit(f"{ISSN_L_CHECK_JSON} has no restored_citation_keys; run issn_l_family_check.py with --write-citation-keys")
    included_again_keys = [tuple(k) for k in issn_l_check["restored_citation_keys"]]
    n_included_again = len(included_again_keys)
    # The counts below add the citations included again to the broad counts: each must be a broad citation (at least one
    # predatory-list hit) and none may be a missed-keys row, whose broad count is taken off separately.
    for month, arxiv_id, suffix, entry_key in included_again_keys:
        found = conn.execute(
            "SELECT n_predatory_list_hits, added_missed_key_row FROM entries_confirmed_bbl_tex "
            "WHERE month=? AND arxiv_id=? AND suffix=? AND entry_key=?", (month, arxiv_id, suffix, entry_key)).fetchall()
        assert len(found) == 1, ("citation included again not found exactly once", month, arxiv_id, suffix, entry_key, len(found))
        assert int(found[0]["n_predatory_list_hits"] or 0) > 0, ("citation included again without a predatory-list hit", month, arxiv_id, suffix, entry_key)
        assert found[0]["added_missed_key_row"] != "1", ("citation included again is a missed-keys row", month, arxiv_id, suffix, entry_key)
    strict_count = read_number("strict_predatory_partition_tables", "strict_count")
    broad_count = read_number("strict_predatory_partition_tables", "broad_count")
    missed_strict_after_check = read_number("missed_keys_flag_counts", "missedkeys_current_strict_after_n")
    missed_broad_after_check = read_number("missed_keys_flag_counts", "missedkeys_current_broad_after_n")

    # Strict and broad counts of the population without the missed-keys rows after the identity check, with the
    # second-vote survivors and the citations included again by the ISSN-L check kept.
    strict_without_rows = strict_count - missed_strict_after_check + n_included_again
    broad_without_rows = broad_count - missed_broad_after_check + n_included_again
    broad_with_rows = broad_count + n_included_again

    missed_strict_rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits "
        "FROM entries_confirmed_bbl_tex WHERE added_missed_key_row='1' AND final_assessment='predatory'"
    ).fetchall()
    print(f"missed-keys strict-predatory rows to classify: {len(missed_strict_rows)}", flush=True)

    records = load_records([(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in missed_strict_rows])
    strict_classified = classify_missed_keys_rows(cache_conn, missed_strict_rows, records)
    strict_dist = Counter(c["verdict"] for c in strict_classified)
    missed_strict_misattributed = {c["key"] for c in strict_classified if c["verdict"] == "confirmed_misattribution"}
    # A row with an invalid vote stays strict when the decision rule, applied again without that vote, still gives 'predatory'.
    missed_strict_survivors = {
        c["key"] for c in strict_classified
        if c["verdict"] == "confirmed_misattribution"
        and recompute_assessment_excluding(records[c["key"]].get("backend_results", []), invalid_backends(c["per_backend"])) == "predatory"
    }
    n_missed_strict_after_check = len(missed_strict_rows) - len(missed_strict_misattributed - missed_strict_survivors)
    print(f"missed-keys strict rows that stay strict although a vote is invalid: {len(missed_strict_survivors)}", flush=True)

    assert n_missed_strict_after_check == missed_strict_after_check, (n_missed_strict_after_check, missed_strict_after_check)

    strict_with_rows = strict_without_rows + n_missed_strict_after_check
    assert missed_strict_after_check <= missed_broad_after_check <= broad_count, (missed_strict_after_check, missed_broad_after_check, broad_count)

    strict_pct_without_rows = pct(strict_without_rows, n_without_missed_keys_rows)
    broad_pct_without_rows = pct(broad_without_rows, n_without_missed_keys_rows)
    strict_pct_with_rows = pct(strict_with_rows, n_with_missed_keys_rows)
    broad_pct_with_rows = pct(broad_with_rows, n_with_missed_keys_rows)

    out = {
        "population": {
            "n_without_missed_keys_rows": n_without_missed_keys_rows,
            "n_with_missed_keys_rows": n_with_missed_keys_rows,
            "n_missed_keys_rows_added": n_missed_keys_rows,
        },
        "missed_keys_rows_identity_classification": {
            "n_strict_rows_classified": len(missed_strict_rows),
            "strict_verdict_distribution": dict(strict_dist),
            "n_strict_confirmed_misattribution": len(missed_strict_misattributed),
            "n_strict_second_vote_survivors": len(missed_strict_survivors),
            "n_strict_after_identity_check": n_missed_strict_after_check,
        },
        "rates_without_and_with_missed_keys_rows": {
            "strict": {
                "n_without_missed_keys_rows": strict_without_rows, "pct_without_missed_keys_rows": strict_pct_without_rows,
                "n_with_missed_keys_rows": strict_with_rows, "pct_with_missed_keys_rows": strict_pct_with_rows,
                "delta_pct_points": round(strict_pct_with_rows - strict_pct_without_rows, 5),
            },
            "broad": {
                "n_without_missed_keys_rows": broad_without_rows, "pct_without_missed_keys_rows": broad_pct_without_rows,
                "n_with_missed_keys_rows": broad_with_rows, "pct_with_missed_keys_rows": broad_pct_with_rows,
                "delta_pct_points": round(broad_pct_with_rows - broad_pct_without_rows, 5),
            },
        },
        "unrounded_pct": {
            "strict_without_missed_keys_rows": round(100.0 * strict_without_rows / n_without_missed_keys_rows, 6),
            "strict_with_missed_keys_rows": round(100.0 * strict_with_rows / n_with_missed_keys_rows, 6),
            "broad_without_missed_keys_rows": round(100.0 * broad_without_rows / n_without_missed_keys_rows, 6),
            "broad_with_missed_keys_rows": round(100.0 * broad_with_rows / n_with_missed_keys_rows, 6),
        },
    }
    write_json("rates_with_missed_keys_rows", out)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
