#!/usr/bin/env python3
"""Identity audit of the strict-predatory references.

For every strict-predatory reference the lists that voted `predatory` (`predatory_list_hits`) are checked against the ISSN of the
reference's own .bib entry: the ISSN on the cache row each list's vote is attached to is compared with the citing ISSN. Votes whose
cache ISSN differs, or whose cache row has no ISSN while another row carries the citing ISSN, are invalid. The remaining vote of the
two lists ugc_care_cloned and ugc_care_cloned_group2 cannot be decided by ISSN. The rules are in
identity_vote_rules.py. For references with an invalid vote the decision rule is applied again without it (second-vote
survivors keep `predatory`). Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT and EVAL_CACHE_DB_PATH; writes
full_corpus_identity_mismatch_audit.json (counts; `confirmed_correct_lists_without_issn` counts the citations classified
confirmed_correct whose winning lists all carry no ISSN of their own, so that the matching cache ISSN comes from another source
on the same cache row and the ISSN check cannot confirm the citation) to EVAL_OUTPUT_DIR/output_identity_mismatch_audit/. With the flag
--write-citation-keys it also writes full_corpus_identity_mismatch_audit_keys.json there (keys of the citations the audit
concerns; read by the scripts that use the audit).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

import stats_common
from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import (
    WATCHLIST_ISSN_CAPABLE,
    build_any_row_has_issn_index,
    classify_votes,
    invalid_backends,
    normalize_issn,
)
from leave_one_out_helpers import load_records
from stats_common import pct, required_path_env, write_json

DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")
# Downstream scripts read this directory back by name.
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit"
CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")

stats_common.DB_PATH = DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR


def get_conn_bbl_tex() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed AS SELECT * FROM entries_confirmed_bbl_tex")
    return conn


stats_common.get_conn = get_conn_bbl_tex
# bound after the assignment above: importers of get_conn from this module get the connection with the view
from stats_common import get_conn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-citation-keys", action="store_true",
                        help="also write full_corpus_identity_mismatch_audit_keys.json (keys of the citations the audit concerns)")
    args = parser.parse_args()
    conn = get_conn()
    cache_conn = sqlite3.connect(str(CACHE_DB_PATH))

    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits, "
        "COALESCE(matched_venue_name, venue_raw) AS venue "
        "FROM entries_confirmed WHERE final_assessment='predatory'"
    ).fetchall()
    print(f"strict-predatory citations to audit: {len(rows)}", flush=True)

    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    print("loading backend_results for all citations from the per-run JSONL results ...", flush=True)
    records = load_records(targets)
    print(f"  recovered {len(records)} records", flush=True)

    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    verdicts = []
    n_missing_record = 0
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        citing_issn = normalize_issn(r["issn"])
        winners = (r["predatory_list_hits"] or "").split(",")
        winners = [w for w in winners if w]

        if citing_issn is None:
            verdicts.append({"key": key, "venue": r["venue"], "citing_issn": None,
                              "winners": winners, "verdict": "unverifiable_no_citing_issn"})
            continue

        rec = records.get(key)
        if rec is None:
            n_missing_record += 1
            verdicts.append({"key": key, "venue": r["venue"], "citing_issn": citing_issn,
                              "winners": winners, "verdict": "missing_backend_record"})
            continue

        per_backend, verdict = classify_votes(rec, winners, citing_issn, any_row_has_issn)

        # True iff every winning list is one whose loader extracts no ISSN: a matching cache ISSN then comes from
        # another source on the same cache row, not from the list itself.
        no_winner_extracts_issn = bool(winners) and all(
            not WATCHLIST_ISSN_CAPABLE.get(b, True) for b in winners
        )

        verdicts.append({
            "key": key, "venue": r["venue"], "citing_issn": citing_issn,
            "winners": winners, "per_backend": per_backend, "verdict": verdict,
            "confirmed_correct_lists_without_issn": verdict == "confirmed_correct" and no_winner_extracts_issn,
        })

    print(f"missing backend records: {n_missing_record}", flush=True)

    dist = Counter(v["verdict"] for v in verdicts)
    total = len(verdicts)
    print("verdict distribution:", dict(dist), flush=True)

    misattributed = [v for v in verdicts if v["verdict"] == "confirmed_misattribution"]
    n_misattributed_venues = len({v["venue"] for v in misattributed})

    misattributed_keys = {v["key"] for v in misattributed}

    # Broad count: a citation drops out when every list hit it had is invalid.
    # Strict count: for each misattributed citation the invalid backends are removed and the decision rule is applied again
    # (recompute_assessment_excluding); a citation that still resolves to 'predatory' is a second-vote survivor and stays strict.
    broad_also_drops = 0
    broad_survives = 0
    broad_drop_keys: set[tuple] = set()
    broad_survives_keys: set[tuple] = set()
    second_vote_survivors: list[tuple] = []
    for v in misattributed:
        invalid = invalid_backends(v["per_backend"])
        remaining_hits = set(v["winners"]) - invalid
        if remaining_hits:
            broad_survives += 1
            broad_survives_keys.add(v["key"])
        else:
            broad_also_drops += 1
            broad_drop_keys.add(v["key"])

        rec = records[v["key"]]
        backend_results = rec.get("backend_results", [])
        valid_vote_verdict = recompute_assessment_excluding(backend_results, invalid)
        if valid_vote_verdict == "predatory":
            second_vote_survivors.append(v["key"])

    second_vote_survivor_keys = set(second_vote_survivors)
    strict_exclude_keys = misattributed_keys - second_vote_survivor_keys
    strict_total_after_identity_check = total - len(strict_exclude_keys)

    broad_total_before_identity_check = conn.execute(
        "SELECT COUNT(*) FROM entries_confirmed WHERE CAST(n_predatory_list_hits AS INTEGER) > 0"
    ).fetchone()[0]
    broad_total_after_identity_check = broad_total_before_identity_check - broad_also_drops

    # Per-submission recompute.
    per_submission_counts = conn.execute(
        """
        SELECT month, arxiv_id,
               SUM(CASE WHEN final_assessment='predatory' THEN 1 ELSE 0 END) n_strict,
               SUM(CASE WHEN CAST(n_predatory_list_hits AS INTEGER) > 0 THEN 1 ELSE 0 END) n_broad
        FROM entries_confirmed GROUP BY month, arxiv_id
        """
    ).fetchall()
    strict_drop_by_sub = Counter()
    broad_drop_by_sub = Counter()
    for v in misattributed:
        if v["key"] not in strict_exclude_keys:
            continue  # second-vote survivor: not dropped from strict
        m, a = v["key"][0], v["key"][1]
        strict_drop_by_sub[(m, a)] += 1
    for v in misattributed:
        remaining_hits = set(v["winners"]) - invalid_backends(v["per_backend"])
        if not remaining_hits:
            m, a = v["key"][0], v["key"][1]
            broad_drop_by_sub[(m, a)] += 1

    n_papers = len(per_submission_counts)
    n_strict_before_identity_check = sum(1 for p in per_submission_counts if p["n_strict"] > 0)
    n_broad_before_identity_check = sum(1 for p in per_submission_counts if p["n_broad"] > 0)
    n_strict_after_identity_check = 0
    n_broad_after_identity_check = 0
    n_strict_flag_dropped = 0
    n_broad_flag_dropped = 0
    for p in per_submission_counts:
        key = (p["month"], p["arxiv_id"])
        strict_count_after = p["n_strict"] - strict_drop_by_sub.get(key, 0)
        broad_count_after = p["n_broad"] - broad_drop_by_sub.get(key, 0)
        if strict_count_after > 0:
            n_strict_after_identity_check += 1
        if broad_count_after > 0:
            n_broad_after_identity_check += 1
        if p["n_strict"] > 0 and strict_count_after == 0:
            n_strict_flag_dropped += 1
        if p["n_broad"] > 0 and broad_count_after == 0:
            n_broad_flag_dropped += 1

    lists_without_issn = [v for v in verdicts if v.get("confirmed_correct_lists_without_issn")]
    n_lists_without_issn_venues = len({v["venue"] for v in lists_without_issn})

    out = {
        "n_strict_predatory_total": total,
        "verdict_distribution": {k: {"n": v, "pct": pct(v, total)} for k, v in dist.items()},
        "n_confirmed_misattribution_citations": len(misattributed_keys),
        "n_confirmed_misattribution_venues": n_misattributed_venues,
        "second_vote_survivors": {"n": len(second_vote_survivors)},
        "counts_after_identity_check": {
            "strict_predatory": {
                "n_after_identity_check": strict_total_after_identity_check, "delta": -(len(strict_exclude_keys)),
                "n_confirmed_misattribution_citations": len(misattributed_keys),
                "n_second_vote_survivors_not_excluded": len(second_vote_survivors),
            },
            "broad_predatory": {
                "n_before_identity_check": broad_total_before_identity_check,
                "n_after_identity_check": broad_total_after_identity_check,
                "n_misattributed_citations_that_also_drop_from_broad": broad_also_drops,
                "n_misattributed_citations_broad_survives_via_other_watchlist": broad_survives,
            },
            "per_submission": {
                "n_papers": n_papers,
                "strict": {"n_before_identity_check": n_strict_before_identity_check, "n_after_identity_check": n_strict_after_identity_check,
                           "n_flag_dropped_entirely": n_strict_flag_dropped},
                "broad": {"n_before_identity_check": n_broad_before_identity_check, "n_after_identity_check": n_broad_after_identity_check,
                          "n_flag_dropped_entirely": n_broad_flag_dropped},
            },
        },
        "unverifiable_fraction": {
            "n": dist.get("unverifiable_no_citing_issn", 0),
            "pct_of_strict_total": pct(dist.get("unverifiable_no_citing_issn", 0), total),
        },
        "confirmed_correct_lists_without_issn": {
            "n_citations": len(lists_without_issn),
            "pct_of_strict_total": pct(len(lists_without_issn), total),
            "pct_of_confirmed_correct": pct(len(lists_without_issn), dist.get("confirmed_correct", 0)),
            "n_distinct_venues": n_lists_without_issn_venues,
        },
    }
    write_json("full_corpus_identity_mismatch_audit", out)

    if args.write_citation_keys:
        write_json(
            "full_corpus_identity_mismatch_audit_keys",
            {
                "confirmed_misattribution_keys": sorted(list(k) for k in misattributed_keys),
                "confirmed_correct_lists_without_issn_keys": sorted(list(v["key"]) for v in lists_without_issn),
                "broad_drop_keys": sorted(list(k) for k in broad_drop_keys),
                "broad_survives_keys": sorted(list(k) for k in broad_survives_keys),
                "second_vote_survivor_keys": sorted(list(k) for k in second_vote_survivor_keys),
                "strict_exclude_keys": sorted(list(k) for k in strict_exclude_keys),
            },
        )
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
