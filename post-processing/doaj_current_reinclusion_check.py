#!/usr/bin/env python3
"""Counts how many of the distinct journals (by ISSN) of the withdrawn DOAJ journals (the `doaj_best_practice` set) are
in DOAJ's current journal list, overall and split by whether the tool's final assessment of the withdrawn journal is
'legitimate'. A withdrawn journal's ISSN is matched against both the print-ISSN and the e-ISSN column of the current
list. Reads doaj_best_practice__doaj_best_practice.jsonl under
EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/mass_eval_output and EVAL_DOAJ_CURRENT_LISTING_CSV (DOAJ's public journal list
as CSV). Writes
output_doaj_current_reinclusion_check/doaj_current_reinclusion_check.json (counts only) and
numbers/doaj_current_reinclusion_check.numbers.json (keys doaj_reinclusion_*). Population of this script: the withdrawn
journals of the set that have an ISSN, one per normalized ISSN (the first record of an ISSN represents the journal, so
its assessment decides the 'legitimate' split); records without ISSN cannot be matched and are left out.
"""
from __future__ import annotations

import csv
import json

import stats_common
from identity_vote_rules import normalize_issn
from stats_common import required_path_env, write_json, write_numbers

DOAJ_BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
DOAJ_JSONL = DOAJ_BASE / "mass_eval_output" / "doaj_best_practice__doaj_best_practice.jsonl"
DOAJ_CURRENT_LISTING_CSV = required_path_env("EVAL_DOAJ_CURRENT_LISTING_CSV")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_doaj_current_reinclusion_check"

stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # distinct withdrawn journals, deduplicated by normalized ISSN
    recs = [json.loads(l) for l in DOAJ_JSONL.open(encoding="utf-8") if l.strip()]
    by_issn: dict[str, dict] = {}
    for r in recs:
        n = normalize_issn(r.get("issn"))
        if n:
            by_issn.setdefault(n, r)  # the first record of an ISSN represents the journal

    # current listing: the set of print ISSNs and e-ISSNs
    current_rows = list(csv.DictReader(DOAJ_CURRENT_LISTING_CSV.open(encoding="utf-8")))
    current_issns: set[str] = set()
    for r in current_rows:
        for col in ("Journal ISSN (print version)", "Journal EISSN (online version)"):
            n = normalize_issn(r.get(col))
            if n:
                current_issns.add(n)

    # a withdrawn journal is in the current list if its ISSN equals the print ISSN or the e-ISSN of a current entry
    n_in_current_listing = sum(1 for issn in by_issn if issn in current_issns)
    n_checkable = len(by_issn)
    pct_in_current_listing = round(100.0 * n_in_current_listing / n_checkable, 3) if n_checkable else None

    # the same count restricted to journals whose representative record has final_assessment 'legitimate'
    legit_in_current_listing = sum(
        1 for issn, r in by_issn.items() if r.get("final_assessment") == "legitimate" and issn in current_issns
    )
    legit_total = sum(1 for r in by_issn.values() if r.get("final_assessment") == "legitimate")
    pct_legit_not_in_current_listing = (
        round(100.0 * (legit_total - legit_in_current_listing) / legit_total, 3) if legit_total else None
    )

    out = {
        "current_listing": {
            "n_rows": len(current_rows),
            "max_last_updated_date_in_data": max((r["Last updated Date"] for r in current_rows if r.get("Last updated Date")), default=None),
        },
        "n_distinct_withdrawn_journals_checked": n_checkable,
        "n_in_current_listing": n_in_current_listing,
        "pct_in_current_listing": pct_in_current_listing,
        "journals_assessed_legitimate": {
            "n": legit_total,
            "n_in_current_listing": legit_in_current_listing,
            "pct_not_in_current_listing": pct_legit_not_in_current_listing,
        },
        "method": (
            "Each withdrawn journal's recorded ISSN is compared with both the 'Journal ISSN (print version)' and "
            "the 'Journal EISSN (online version)' column of every current DOAJ entry."
        ),
    }
    write_json("doaj_current_reinclusion_check", out)
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "doaj_reinclusion_n_checkable": {"kind": "int", "value": n_checkable},
        "doaj_reinclusion_pct_reinstated": {
            "kind": "ratio", "num": n_in_current_listing, "den": n_checkable, "decimals": 2,
        },
        "doaj_reinclusion_legitresolving_pct_still_removed": {
            "kind": "ratio", "num": legit_total - legit_in_current_listing, "den": legit_total, "decimals": 1,
        },
    }
    write_numbers("doaj_current_reinclusion_check", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
