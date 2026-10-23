#!/usr/bin/env python3
"""Turns counts that other scripts have written into ratio entries of numbers/corpus_share_ratios.numbers.json: the strict and
broad shares of submissions, and the shares of the "any documented concern" submissions that are retraction-only, broad (total and
only) and hijacked (total and only). Reads the numbers files of disciplinary_submission_counts.py and
any_documented_concern_decomposition.py, so those run first.
"""
from __future__ import annotations

from stats_common import read_number, write_numbers


def main() -> None:
    concern = "any_documented_concern_decomposition"
    # Submissions with any documented concern, and how many of them are retraction-only, broad (total / only) and hijacked
    # (total / only).
    concern_any_flag = read_number(concern, "concern_n_any_flag")
    concern_retraction_only = read_number(concern, "concern_n_retraction_only")
    concern_broad_total = read_number(concern, "concern_n_broad_total_incl_overlaps")
    concern_broad_only = read_number(concern, "concern_n_broad_only")
    concern_hijacked_total = read_number(concern, "concern_n_hijacked_total_incl_overlaps")
    concern_hijacked_only = read_number(concern, "concern_n_hijacked_only")
    # Submissions with at least one strict / broad predatory citation after the identity check, and the number of submissions.
    submission_strict_count = read_number("disciplinary_submission_counts", "discsubcount_n_strict")
    submission_broad_count = read_number("disciplinary_submission_counts", "discsubcount_n_broad")
    submission_total = read_number("disciplinary_submission_counts", "discsubcount_n_total")
    write_numbers("corpus_share_ratios", {
        "submission_strict_pct": {
            "kind": "ratio", "num": submission_strict_count, "den": submission_total, "decimals": 2,
        },
        "submission_broad_pct": {
            "kind": "ratio", "num": submission_broad_count, "den": submission_total, "decimals": 2,
        },
        "concern_retraction_only_pct": {
            "kind": "ratio", "num": concern_retraction_only, "den": concern_any_flag, "decimals": 1,
        },
        "concern_broad_total_pct": {
            "kind": "ratio", "num": concern_broad_total, "den": concern_any_flag, "decimals": 1,
        },
        "concern_broad_only_pct": {
            "kind": "ratio", "num": concern_broad_only, "den": concern_any_flag, "decimals": 1,
        },
        "concern_hijacked_total_pct": {
            "kind": "ratio", "num": concern_hijacked_total, "den": concern_any_flag, "decimals": 1,
        },
        "concern_hijacked_only_pct": {
            "kind": "ratio", "num": concern_hijacked_only, "den": concern_any_flag, "decimals": 1,
        },
    })


if __name__ == "__main__":
    main()
