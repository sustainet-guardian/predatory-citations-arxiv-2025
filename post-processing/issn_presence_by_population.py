#!/usr/bin/env python3
"""Share of citations carrying a non-empty ISSN in two populations, plus the gap in percentage points. Strict
population after the identity check (denominator of issnpresence_postcorr_pct): entries_confirmed with the stored
final_assessment 'predatory', without the citations in strict_exclude. Legitimate population, valid-vote verdict
(denominator of issnpresence_legit_pct): entries_confirmed_valid_votes with final_assessment_valid_votes 'legitimate',
i.e. the verdict recomputed without the invalid votes. Reads EVAL_DB_PATH_BBL_TEX through
identity_check_table_analyses.get_conn_after_identity_check(). Writes numbers/issn_presence_by_population.numbers.json
(keys issnpresence_*) and a JSON file in EVAL_OUTPUT_DIR/output_issn_presence_by_population.
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_issn_presence_by_population"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def compute_slice(conn, table: str, where_clause: str) -> dict:
    """Number of entries in the given view matching where_clause, and how many of them have a non-empty issn."""
    total = conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {where_clause}").fetchone()["n"]
    with_issn = conn.execute(
        f"SELECT COUNT(*) n FROM {table} WHERE {where_clause} AND issn IS NOT NULL AND issn != ''"
    ).fetchone()["n"]
    return {"n": total, "n_with_issn": with_issn, "issn_pct": stats_common.pct(with_issn, total)}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    strict_after_check = compute_slice(conn, "entries_confirmed", "final_assessment='predatory'")
    legitimate = compute_slice(conn, "entries_confirmed_valid_votes", "final_assessment_valid_votes='legitimate'")

    assert strict_after_check["n"] > 0 and legitimate["n"] > 0, "empty population"

    gap_after_check = round(legitimate["issn_pct"] - strict_after_check["issn_pct"], 1)

    out = {
        "legitimate": legitimate,
        "strict_predatory_after_identity_check": strict_after_check,
        "gap_legit_minus_strict_after_identity_check_pct_points": gap_after_check,
    }
    stats_common.write_json("issn_presence_by_population", out)
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "issnpresence_legit_pct": {"kind": "ratio", "num": legitimate["n_with_issn"], "den": legitimate["n"], "decimals": 1},
        "issnpresence_postcorr_with_issn_n": {"kind": "int", "value": strict_after_check["n_with_issn"]},
        "issnpresence_postcorr_pct": {
            "kind": "ratio", "num": strict_after_check["n_with_issn"], "den": strict_after_check["n"], "decimals": 1,
        },
        "issnpresence_gap_postcorr": {"kind": "float", "value": gap_after_check, "decimals": 1},
    }
    stats_common.write_numbers("issn_presence_by_population", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
