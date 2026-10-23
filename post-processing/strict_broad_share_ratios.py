#!/usr/bin/env python3
"""Turns counts that other scripts have written into entries of numbers/strict_broad_share_ratios.numbers.json.
`pct_unverified_of_original` is the share of the strict citations before the identity check (audit_original_strict_total: the
rows of entries_confirmed with final_assessment = 'predatory' that full_corpus_identity_mismatch_audit.py audits) that the check
removed (confirmed misattributions without the second-vote survivors) or whose identity it cannot confirm (the winning lists carry
no ISSN of their own); `n_unverified_of_original` is its numerator. `algerian_broad_share_pct` is the share of the broad flags that come from the
Algerian ministry list; `multibib_*_pct_of_population` the share of excess multi-.bib duplicate rows in the strict and in the broad
population after the identity check (strict_count: final_assessment = 'predatory' minus strict_exclude; broad_count). Reads the numbers files of strict_predatory_partition_tables.py, identity_check_table_analyses.py and
multibib_duplicates_within_strict_broad.py, so those run first.
"""
from __future__ import annotations

from stats_common import read_number, write_numbers

PARTITION = "strict_predatory_partition_tables"


def main() -> None:
    confirmed_misattributions = read_number(PARTITION, "audit_mismatch") - read_number(PARTITION, "strict_n_second_vote_survivors")
    identity_unverified = read_number(PARTITION, "strict_identity_unverified")
    unverified_of_original = confirmed_misattributions + identity_unverified
    strict_before_identity_check = read_number(PARTITION, "audit_original_strict_total")
    broad_count = read_number(PARTITION, "broad_count")
    strict_count = read_number(PARTITION, "strict_count")
    algerian_broad_flags = read_number("identity_check_table_analyses", "reliability_algerian_ministry_flags")
    multibib_strict_excess = read_number("multibib_duplicates_within_strict_broad", "multibib_strict_n_excess_rows")
    multibib_broad_excess = read_number("multibib_duplicates_within_strict_broad", "multibib_broad_n_excess_rows")
    write_numbers("strict_broad_share_ratios", {
        "pct_unverified_of_original": {
            "kind": "ratio", "num": unverified_of_original, "den": strict_before_identity_check, "decimals": 1,
        },
        "n_unverified_of_original": {"kind": "int", "value": unverified_of_original},
        "algerian_broad_share_pct": {
            "kind": "ratio", "num": algerian_broad_flags, "den": broad_count, "decimals": 1,
        },
        "multibib_strict_pct_of_population": {
            "kind": "ratio", "num": multibib_strict_excess, "den": strict_count, "decimals": 2,
        },
        "multibib_broad_pct_of_population": {
            "kind": "ratio", "num": multibib_broad_excess, "den": broad_count, "decimals": 2,
        },
    })


if __name__ == "__main__":
    main()
