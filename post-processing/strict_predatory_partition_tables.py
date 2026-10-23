#!/usr/bin/env python3
"""Writes the two partitions of the strict-predatory citations from the output of full_corpus_identity_mismatch_audit.py.
Before the identity check (keys audit_*): mismatch, confirmed correct, no own ISSN, structurally undecidable; the parts
sum to the strict total before the identity check (audit_original_strict_total, the denominator of
audit_no_issn_pct_of_raw). After the identity check (keys strict_*): identity-verified, identity-unverified (confirmed
correct and listed by the audit under confirmed_correct_lists_without_issn), no ISSN, and undecided (second-vote
survivors plus structurally undecidable citations; their remaining vote cannot be decided by ISSN); the parts sum to strict_count, the strict total after the identity check (the denominator of strict_no_issn_pct).
Reads full_corpus_identity_mismatch_audit.json and its _keys.json under EVAL_OUTPUT_DIR/output_identity_mismatch_audit.
Writes numbers/strict_predatory_partition_tables.numbers.json."""
from __future__ import annotations

import json

import stats_common
from stats_common import write_numbers

AUDIT_OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit"
AUDIT_JSON = AUDIT_OUTPUT_DIR / "full_corpus_identity_mismatch_audit.json"
AUDIT_KEYS_JSON = AUDIT_OUTPUT_DIR / "full_corpus_identity_mismatch_audit_keys.json"


def main() -> None:
    audit = json.loads(AUDIT_JSON.read_text())
    keys_data = json.loads(AUDIT_KEYS_JSON.read_text())
    dist = audit["verdict_distribution"]

    # partition 1: strict-predatory citations before the identity check
    audit_total = audit["n_strict_predatory_total"]
    n_mismatch = dist["confirmed_misattribution"]["n"]
    n_confirmed_correct = dist["confirmed_correct"]["n"]
    n_no_issn = dist["unverifiable_no_citing_issn"]["n"]
    n_structurally_undecidable = dist["structurally_undecidable"]["n"]
    assert n_mismatch + n_confirmed_correct + n_no_issn + n_structurally_undecidable == audit_total

    # partition 2: strict-predatory citations after the identity check
    n_second_vote_survivors = audit["second_vote_survivors"]["n"]
    n_strict_exclude = len(keys_data["strict_exclude_keys"])
    assert n_strict_exclude == n_mismatch - n_second_vote_survivors

    n_identity_unverified = audit["confirmed_correct_lists_without_issn"]["n_citations"]
    n_identity_verified = n_confirmed_correct - n_identity_unverified
    n_undecided = n_second_vote_survivors + n_structurally_undecidable

    strict_count = audit["counts_after_identity_check"]["strict_predatory"]["n_after_identity_check"]
    broad_count = audit["counts_after_identity_check"]["broad_predatory"]["n_after_identity_check"]
    assert strict_count == audit_total - n_strict_exclude
    assert n_identity_verified + n_identity_unverified + n_no_issn + n_undecided == strict_count, (
        f"{n_identity_verified} + {n_identity_unverified} + {n_no_issn} + {n_undecided} != {strict_count}"
    )

    write_numbers("strict_predatory_partition_tables", {
        "audit_original_strict_total": {"kind": "int", "value": audit_total},
        "audit_mismatch": {"kind": "int", "value": n_mismatch},
        "audit_confirmed_correct": {"kind": "int", "value": n_confirmed_correct},
        "audit_no_issn": {"kind": "int", "value": n_no_issn},
        "audit_structurally_undecidable": {"kind": "int", "value": n_structurally_undecidable},
        "strict_count": {"kind": "int", "value": strict_count},
        "broad_count": {"kind": "int", "value": broad_count},
        "strict_n_second_vote_survivors": {"kind": "int", "value": n_second_vote_survivors},
        "strict_identity_verified": {"kind": "int", "value": n_identity_verified},
        "strict_identity_unverified": {"kind": "int", "value": n_identity_unverified},
        "strict_no_issn": {"kind": "int", "value": n_no_issn},
        "strict_undecided": {"kind": "int", "value": n_undecided},
        "strict_no_issn_pct": {
            "kind": "ratio", "num": n_no_issn, "den": strict_count, "decimals": 1,
        },
        "audit_no_issn_pct_of_raw": {
            "kind": "ratio", "num": n_no_issn, "den": audit_total, "decimals": 1,
        },
        "audit_checkable_with_issn": {
            "kind": "int", "value": audit_total - n_no_issn,
        },
        "audit_mismatch_rate_of_checkable": {
            "kind": "ratio", "num": n_mismatch, "den": audit_total - n_no_issn, "decimals": 1,
        },
    })


if __name__ == "__main__":
    main()
