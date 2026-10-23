#!/usr/bin/env python3
"""Summarizes the mass-evaluation JSONL results of three benchmark sets under
EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/mass_eval_output (Retraction Watch Hijacked Journal Checker, the withdrawn DOAJ
journals (the `doaj_best_practice` set), negative-control sample of DOAJ journals): overlap with the nine predatory-list
watchlists (identity_vote_rules.WATCHLIST_BACKENDS), final-assessment distributions, and the share of records with the
is_hijacked flag. Overlap means the record's predatory_list_hits contain one of the nine watchlists. The
kscien_hijacked_journals backend (assessment "hijacked") is not one of the nine and does not appear in
predatory_list_hits of these records; it only sets the is_hijacked flag and is counted separately
(n_kscien_hijacked_journals_match). Writes output_analyze_hijacked_doaj_benchmark/hijacked_doaj_benchmark_analysis.json
(counts only) and numbers/analyze_hijacked_doaj_benchmark.numbers.json (keys hijackedbench_*: the sizes of the
Retraction Watch set and of the `doaj_best_practice` set). That set has one record per row of the change log of
best-practice withdrawals (rows without ISSN included, no deduplication by ISSN). The functions
analyze_benchmark and load_jsonl are imported by add_one_new_sources_impact.py; is_hijacked, load_jsonl and
watchlist_overlap by analyze_identity_and_negative_control.py; watchlist_overlap by negative_control_disposition.py.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from identity_vote_rules import WATCHLIST_BACKENDS
from result_line import has_hijacked_vote
from stats_common import required_path_env, write_numbers

BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
MASS_EVAL_OUTPUT = BASE / "mass_eval_output"
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_analyze_hijacked_doaj_benchmark"


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def backend_found(record: dict, backend_name: str) -> dict | None:
    for r in record.get("backend_results", []):
        if r.get("backend_name") == backend_name and r.get("status") == "found":
            return r
    return None


def is_hijacked(record: dict) -> bool:
    return has_hijacked_vote(record)


def watchlist_overlap(record: dict) -> list[str]:
    """The nine watchlists among the record's predatory_list_hits (predatory votes of predatory-list backends)."""
    return [b for b in (record.get("predatory_list_hits") or []) if b in WATCHLIST_BACKENDS]


def analyze_benchmark(records: list[dict], label: str) -> dict:
    total = len(records)
    per_watchlist = Counter()
    for r in records:
        for b in watchlist_overlap(r):
            per_watchlist[b] += 1

    overlap_records = [r for r in records if watchlist_overlap(r)]
    disjoint_records = [r for r in records if not watchlist_overlap(r)]

    def assessment_distribution(recs: list[dict]) -> dict:
        dist = Counter(r.get("final_assessment") for r in recs)
        return {k: {"n": v, "pct": round(100.0 * v / len(recs), 3) if recs else None} for k, v in dist.most_common()}

    n_kscien_hijacked = sum(1 for r in records if backend_found(r, "kscien_hijacked_journals"))
    n_is_hijacked_flag = sum(1 for r in records if is_hijacked(r))

    return {
        "label": label,
        "total_size": total,
        "n_overlap_any_of_9_watchlists": len(overlap_records),
        "pct_overlap_any_of_9_watchlists": round(100.0 * len(overlap_records) / total, 3) if total else None,
        "per_watchlist_overlap_count": dict(per_watchlist.most_common()),
        "n_disjoint_subset": len(disjoint_records),
        "pct_disjoint_subset": round(100.0 * len(disjoint_records) / total, 3) if total else None,
        "n_kscien_hijacked_journals_match": n_kscien_hijacked,
        "n_is_hijacked_flag_set": n_is_hijacked_flag,
        "final_assessment_distribution_full_set": assessment_distribution(records),
        "final_assessment_distribution_disjoint_only": assessment_distribution(disjoint_records),
        "is_hijacked_flag_pct_full_set": round(100.0 * n_is_hijacked_flag / total, 3) if total else None,
    }


def count_doaj_backend_matches(records: list[dict]) -> dict:
    """Records that the tool's 'doaj' backend (a legitimate-list backend on DOAJ's listing) finds."""
    found_by_doaj_backend = [r for r in records if backend_found(r, "doaj")]
    return {
        "n_total": len(records),
        "n_found_by_doaj_backend": len(found_by_doaj_backend),
        "pct_found_by_doaj_backend": round(100.0 * len(found_by_doaj_backend) / len(records), 3) if records else None,
    }


def analyze_negative_control(records: list[dict]) -> dict:
    total = len(records)
    watchlist_hits = [r for r in records if watchlist_overlap(r)]
    no_list_hit = [r for r in records if not watchlist_overlap(r)]

    def assessment_distribution(recs: list[dict]) -> dict:
        dist = Counter(r.get("final_assessment") for r in recs)
        return {k: {"n": v, "pct": round(100.0 * v / len(recs), 3) if recs else None} for k, v in dist.most_common()}

    # journals of the DOAJ sample without watchlist overlap that the tool assesses as predatory or suspicious
    assessed_flagged_labels = {"predatory", "suspicious"}
    flagged = [r for r in no_list_hit if r.get("final_assessment") in assessed_flagged_labels]

    return {
        "total_drawn": total,
        "n_excluded_hit_a_watchlist": len(watchlist_hits),
        "n_no_list_hit": len(no_list_hit),
        "final_assessment_distribution_no_list_hit": assessment_distribution(no_list_hit),
        "n_assessed_predatory_or_suspicious": len(flagged),
        "pct_assessed_predatory_or_suspicious_of_no_list_hit": (
            round(100.0 * len(flagged) / len(no_list_hit), 3) if no_list_hit else None
        ),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rw_records = load_jsonl(MASS_EVAL_OUTPUT / "retraction_watch__retraction_watch.jsonl")
    doaj_records = load_jsonl(MASS_EVAL_OUTPUT / "doaj_best_practice__doaj_best_practice.jsonl")
    neg_records = load_jsonl(MASS_EVAL_OUTPUT / "negative_control__negative_control.jsonl")

    out = {
        "retraction_watch": analyze_benchmark(rw_records, "retraction_watch_hijacked_checker"),
        "doaj_best_practice": analyze_benchmark(doaj_records, "doaj_best_practice"),
        "doaj_backend_matches": count_doaj_backend_matches(doaj_records),
        "negative_control": analyze_negative_control(neg_records),
        "watchlist_backends_checked": WATCHLIST_BACKENDS,
    }

    (OUTPUT_DIR / "hijacked_doaj_benchmark_analysis.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    rw = out["retraction_watch"]
    doaj = out["doaj_best_practice"]
    numbers_payload = {
        "hijackedbench_rw_total": {"kind": "int", "value": rw["total_size"]},
        "hijackedbench_doaj_total": {"kind": "int", "value": doaj["total_size"]},
    }
    write_numbers("analyze_hijacked_doaj_benchmark", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
