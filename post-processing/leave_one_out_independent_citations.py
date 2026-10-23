#!/usr/bin/env python3
"""Leave-one-out of ugc_care_cloned_group2 on the strict label: finds the citations that lose their strict
`predatory` verdict when that backend is excluded (each citation's own invalid votes are removed as well),
separates those that are confirmed-misattribution citations from those that are not, and characterises the
latter by their remaining predatory-list votes (sole vote, confidence tie between individually decisive votes,
opposing vote of the list ugc_care_included_from_clone_group2). Only counts are written. Reads EVAL_DB_PATH_BBL_TEX,
EVAL_CACHE_DB_PATH, EVAL_RESULTS_ROOT and the identity-audit files under EVAL_OUTPUT_DIR; uses
leave_one_out_backend_sweep.py for the shared database access and identity_vote_rules.py for the invalid-vote map. Writes
leave_one_out_independent_citations.json to EVAL_OUTPUT_DIR/output_leave_one_out_independent_citations/ and
numbers/leave_one_out_independent_citations.numbers.json (keys l1o69_*).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import leave_one_out_backend_sweep as loo_sweep
import leave_one_out_helpers as loo
import stats_common
from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import build_invalid_backends_map
from stats_common import required_path_env

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_leave_one_out_independent_citations"
stats_common.OUTPUT_DIR = OUTPUT_DIR

SWEPT_BACKEND = "ugc_care_cloned_group2"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = loo_sweep.get_conn()
    cache_conn = sqlite3.connect(str(loo_sweep.CACHE_DB_PATH))

    misattributed_keys = {tuple(k) for k in json.loads(loo_sweep.AUDIT_KEYS_JSON.read_text())["confirmed_misattribution_keys"]}
    per_citation = json.loads(loo_sweep.CITATION_DETAIL_PATH.read_text())
    broad_drop_keys = {(c["month"], c["arxiv_id"], c["suffix"], c["entry_key"]) for c in per_citation if not c["broad_survives"]}

    invalid_backends_map = build_invalid_backends_map(conn, cache_conn, misattributed_keys)

    conn.execute("CREATE TEMP TABLE broad_drop_keys (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO broad_drop_keys VALUES (?, ?, ?, ?)", sorted(broad_drop_keys))

    # Broad-candidate population after the identity check. A citation is strict-predatory when the verdict
    # recomputed with only its own invalid votes removed is 'predatory'.
    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key "
        "FROM entries_confirmed_all "
        "WHERE CAST(n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM broad_drop_keys b WHERE b.month=entries_confirmed_all.month AND b.arxiv_id=entries_confirmed_all.arxiv_id "
        "  AND b.suffix=entries_confirmed_all.suffix AND b.entry_key=entries_confirmed_all.entry_key)"
    ).fetchall()
    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    print(f"broad candidate population: {len(targets)}", flush=True)
    records = loo.load_records(targets)

    baseline_predatory = []
    flipped = []
    for key in targets:
        rec = records.get(key)
        if rec is None:
            continue
        own_invalid = invalid_backends_map.get(key, set())
        baseline_assessment = recompute_assessment_excluding(rec.get("backend_results", []), own_invalid)
        if baseline_assessment != "predatory":
            continue
        baseline_predatory.append(key)
        exclude_set = {SWEPT_BACKEND} | own_invalid
        assessment_without_swept_backend = recompute_assessment_excluding(rec.get("backend_results", []), exclude_set)
        if assessment_without_swept_backend != "predatory":
            flipped.append(key)

    print(f"strict-predatory citations: {len(baseline_predatory)}", flush=True)
    print(f"flipped when sweeping {SWEPT_BACKEND}: {len(flipped)}", flush=True)

    flipped_set = set(flipped)
    independent_flipped = flipped_set - misattributed_keys
    print(f"of those, independent of the confirmed-misattribution citations: {len(independent_flipped)}", flush=True)

    # For each independent citation: the backends with a found predatory-list vote in backend_results.
    pattern_counts: Counter = Counter()
    sole_vote_is_swept_backend = 0
    n_confidence_tie = 0
    n_has_opposing_included_list_vote = 0
    for key in sorted(independent_flipped):
        rec = records[key]
        own_invalid = invalid_backends_map.get(key, set())
        backend_results = rec.get("backend_results", [])
        predatory_list_votes = [
            b["backend_name"] for b in backend_results
            if b.get("status") == "found" and b.get("evidence_type") == "predatory_list"
            and b.get("assessment") == "predatory"
        ]
        surviving_after_own_invalid_stripped = [b for b in predatory_list_votes if b not in own_invalid]
        pattern_counts[tuple(sorted(surviving_after_own_invalid_stripped))] += 1
        is_sole_vote = surviving_after_own_invalid_stripped == [SWEPT_BACKEND]
        if is_sole_vote:
            sole_vote_is_swept_backend += 1

        # Surviving votes whose removal alone (own invalid votes still removed) turns the verdict away from
        # predatory; two or more such votes form a confidence tie.
        individually_decisive = [
            b for b in surviving_after_own_invalid_stripped
            if recompute_assessment_excluding(backend_results, own_invalid | {b}) != "predatory"
        ]
        is_confidence_tie = len(individually_decisive) >= 2
        if is_confidence_tie:
            n_confidence_tie += 1
        has_opposing_included_list_vote = any(
            b.get("backend_name") == "ugc_care_included_from_clone_group2" and b.get("status") == "found"
            for b in backend_results
        )
        if has_opposing_included_list_vote:
            n_has_opposing_included_list_vote += 1

    out = {
        "swept_backend": SWEPT_BACKEND,
        "n_strict_basis": len(baseline_predatory),
        "n_flipped_total": len(flipped),
        "n_independent_citations": len(independent_flipped),
        "n_independent_where_swept_backend_is_sole_surviving_predatory_list_vote": sole_vote_is_swept_backend,
        "n_independent_confidence_tie_multiple_individually_decisive_votes": n_confidence_tie,
        "n_independent_with_opposing_ugc_care_included_from_clone_group2_vote": n_has_opposing_included_list_vote,
        "pattern_counts_of_surviving_predatory_list_votes": {
            (str(k) if k else "()"): v for k, v in pattern_counts.items()
        },
    }
    stats_common.write_json("leave_one_out_independent_citations", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("leave_one_out_independent_citations", {
        "l1o69_n_strict_basis": {"kind": "int", "value": len(baseline_predatory)},
        "l1o69_n_flipped_total": {"kind": "int", "value": len(flipped)},
        "l1o69_n_independent": {"kind": "int", "value": len(independent_flipped)},
        "l1o69_n_sole_surviving_vote": {"kind": "int", "value": sole_vote_is_swept_backend},
        "l1o69_n_razor_thin_tie": {"kind": "int", "value": n_confidence_tie},
    })


if __name__ == "__main__":
    main()
