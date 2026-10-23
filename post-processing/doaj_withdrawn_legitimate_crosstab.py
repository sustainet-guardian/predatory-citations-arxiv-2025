#!/usr/bin/env python3
"""Cross-tabulates the withdrawn DOAJ journals (the `doaj_best_practice` set) that are assessed as 'legitimate' against
being in DOAJ's current journal list.
Reads doaj_best_practice__doaj_best_practice.jsonl under EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/mass_eval_output and
EVAL_DOAJ_CURRENT_LISTING_CSV (DOAJ's public journal list as CSV; its print ISSNs and e-ISSNs are the ISSNs in the
current list). Writes output_doaj_withdrawn_legitimate_crosstab/doaj_withdrawn_legitimate_crosstab.json and
numbers/doaj_withdrawn_legitimate_crosstab.numbers.json (keys doajxtab_*). Population of this script: the withdrawn
journals of the set that have an ISSN, one per normalized ISSN, keeping only ISSNs none of whose records overlaps a
watchlist; a journal counts as 'legitimate' if any of its records is assessed so. doajxtab_legit_pct_of_disjoint is the
share of these journals that count as 'legitimate'; doajxtab_records_legit_pct_of_disjoint is the same share over the
records with an ISSN (before the deduplication by ISSN), a record being in the population when it has no hit of a watchlist
backend.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict

from identity_vote_rules import WATCHLIST_BACKENDS, normalize_issn
from stats_common import required_path_env, write_numbers

DOAJ_BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
DOAJ_JSONL = DOAJ_BASE / "mass_eval_output" / "doaj_best_practice__doaj_best_practice.jsonl"
DOAJ_CURRENT_LISTING_CSV = required_path_env("EVAL_DOAJ_CURRENT_LISTING_CSV")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_doaj_withdrawn_legitimate_crosstab"


def watchlist_overlap(r: dict) -> list[str]:
    return [b for b in (r.get("predatory_list_hits") or []) if b in WATCHLIST_BACKENDS]


def main() -> None:
    recs = [json.loads(l) for l in DOAJ_JSONL.open(encoding="utf-8") if l.strip()]
    by_issn: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        n = normalize_issn(r.get("issn"))
        if n:
            by_issn[n].append(r)

    def group_is_disjoint(group: list[dict]) -> bool:
        return not any(watchlist_overlap(r) for r in group)

    def group_is_legitimate(group: list[dict]) -> bool:
        return any(r.get("final_assessment") == "legitimate" for r in group)

    disjoint_issns = {issn for issn, g in by_issn.items() if group_is_disjoint(g)}
    legit_disjoint_issns = {issn for issn in disjoint_issns if group_is_legitimate(by_issn[issn])}
    nonlegit_disjoint_issns = disjoint_issns - legit_disjoint_issns

    records_disjoint = [r for g in by_issn.values() for r in g if not watchlist_overlap(r)]
    records_legit = [r for r in records_disjoint if r.get("final_assessment") == "legitimate"]

    current_issns: set[str] = set()
    for row in csv.DictReader(DOAJ_CURRENT_LISTING_CSV.open(encoding="utf-8")):
        for col in ("Journal ISSN (print version)", "Journal EISSN (online version)"):
            n = normalize_issn(row.get(col))
            if n:
                current_issns.add(n)
    issns_in_current_listing = set(by_issn) & current_issns

    n_legit_in_current_listing = len(legit_disjoint_issns & issns_in_current_listing)
    n_legit_not_in_current_listing = len(legit_disjoint_issns) - n_legit_in_current_listing
    n_nonlegit_in_current_listing = len(nonlegit_disjoint_issns & issns_in_current_listing)
    n_nonlegit_not_in_current_listing = len(nonlegit_disjoint_issns) - n_nonlegit_in_current_listing

    pct_legit_in_current_listing = (
        round(100.0 * n_legit_in_current_listing / len(legit_disjoint_issns), 3) if legit_disjoint_issns else None
    )
    pct_nonlegit_in_current_listing = (
        round(100.0 * n_nonlegit_in_current_listing / len(nonlegit_disjoint_issns), 3) if nonlegit_disjoint_issns else None
    )

    out = {
        "population": (
            "Withdrawn journals with an ISSN and without watchlist overlap, deduplicated by ISSN; records without "
            "ISSN are not part of this join."
        ),
        "n_disjoint": len(disjoint_issns),
        "assessed_legitimate": {
            "n": len(legit_disjoint_issns),
            "n_in_current_listing": n_legit_in_current_listing,
            "pct_in_current_listing": pct_legit_in_current_listing,
            "n_not_in_current_listing": n_legit_not_in_current_listing,
            "pct_not_in_current_listing": (
                round(100.0 - pct_legit_in_current_listing, 3) if pct_legit_in_current_listing is not None else None
            ),
        },
        "records_with_issn_without_watchlist_overlap": {"n": len(records_disjoint), "n_assessed_legitimate": len(records_legit)},
        "not_assessed_legitimate": {
            "n": len(nonlegit_disjoint_issns),
            "n_in_current_listing": n_nonlegit_in_current_listing,
            "pct_in_current_listing": pct_nonlegit_in_current_listing,
            "n_not_in_current_listing": n_nonlegit_not_in_current_listing,
            "pct_not_in_current_listing": (
                round(100.0 - pct_nonlegit_in_current_listing, 3) if pct_nonlegit_in_current_listing is not None else None
            ),
        },
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "doaj_withdrawn_legitimate_crosstab.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(json.dumps(out, indent=2))

    write_numbers("doaj_withdrawn_legitimate_crosstab", {
        "doajxtab_n_disjoint": {"kind": "int", "value": len(disjoint_issns)},
        "doajxtab_legit_n": {"kind": "int", "value": len(legit_disjoint_issns)},
        "doajxtab_legit_n_reinstated": {"kind": "int", "value": n_legit_in_current_listing},
        "doajxtab_legit_pct_reinstated": {
            "kind": "ratio", "num": n_legit_in_current_listing, "den": len(legit_disjoint_issns), "decimals": 1,
        },
        "doajxtab_legit_pct_of_disjoint": {
            "kind": "ratio", "num": len(legit_disjoint_issns), "den": len(disjoint_issns), "decimals": 1,
        },
        "doajxtab_records_legit_pct_of_disjoint": {
            "kind": "ratio", "num": len(records_legit), "den": len(records_disjoint), "decimals": 1,
        },
        "doajxtab_nonlegit_n": {"kind": "int", "value": len(nonlegit_disjoint_issns)},
        "doajxtab_nonlegit_n_reinstated": {"kind": "int", "value": n_nonlegit_in_current_listing},
        "doajxtab_nonlegit_pct_reinstated": {
            "kind": "ratio", "num": n_nonlegit_in_current_listing, "den": len(nonlegit_disjoint_issns), "decimals": 1,
        },
    })


if __name__ == "__main__":
    main()
