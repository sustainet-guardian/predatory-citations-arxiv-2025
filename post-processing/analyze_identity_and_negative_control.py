#!/usr/bin/env python3
"""Two summaries of the benchmark mass-evaluation JSONLs under EVAL_OUTPUT_DIR/hijacked_doaj_benchmark. (1) Retraction
Watch set: watchlist overlap, final-assessment distributions and is_hijacked rates for the set built from the hijacked
title and ISSN (mass_eval_output) and for the set built from the journal identity with URL
(mass_eval_output_clone_identity), with the breakdown by ISSN-relationship category from
clone_identity_issn_categories.json (written by build_retraction_watch_clone_identity_bib.py --write-citation-keys).
(2) Negative-control sample of DOAJ journals (a random sample of DOAJ's public journal list), without the records that
overlap a watchlist: per backend, the number of entries found, and the entries found by at least one independent
legitimacy backend, found only by the doaj backend, or found by neither. Writes
output_analyze_identity_and_negative_control/identity_and_negative_control_analysis.json (counts only); no numbers file.
"""
from __future__ import annotations

import json
from collections import Counter

from stats_common import required_path_env
from analyze_hijacked_doaj_benchmark import is_hijacked, load_jsonl, watchlist_overlap

BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_analyze_identity_and_negative_control"

INDEPENDENT_LEGITIMACY_BACKENDS = [
    "crossref_analyzer", "openalex_analyzer", "opencitations_analyzer",
    "scopus", "dblp_venues", "core_journals", "core_conferences", "pubmed_nlm",
]


def assessment_distribution(recs: list[dict]) -> dict:
    dist = Counter(r.get("final_assessment") for r in recs)
    n = len(recs)
    return {k: {"n": v, "pct": round(100.0 * v / n, 3) if n else None} for k, v in dist.most_common()}


def is_hijacked_rate(recs: list[dict]) -> dict:
    n_hijacked = sum(1 for r in recs if is_hijacked(r))
    return {"n": n_hijacked, "pct": round(100.0 * n_hijacked / len(recs), 3) if recs else None}


def summarize_set(records: list[dict], label: str) -> dict:
    overlap = [r for r in records if watchlist_overlap(r)]
    disjoint = [r for r in records if not watchlist_overlap(r)]
    return {
        "label": label,
        "total": len(records),
        "n_overlap_any_of_9_watchlists": len(overlap),
        "pct_overlap": round(100.0 * len(overlap) / len(records), 3) if records else None,
        "n_disjoint": len(disjoint),
        "pct_disjoint": round(100.0 * len(disjoint) / len(records), 3) if records else None,
        "final_assessment_full": assessment_distribution(records),
        "final_assessment_disjoint": assessment_distribution(disjoint),
        "is_hijacked_full": is_hijacked_rate(records),
        "is_hijacked_disjoint": is_hijacked_rate(disjoint),
    }


def identity_set_comparison() -> dict:
    title_set_records = load_jsonl(BASE / "mass_eval_output" / "retraction_watch__retraction_watch.jsonl")
    identity_set_records = load_jsonl(BASE / "mass_eval_output_clone_identity" / "retraction_watch_clone_identity.jsonl")
    category_by_key = json.loads((BASE / "clone_identity_issn_categories.json").read_text(encoding="utf-8"))

    title_set_summary = summarize_set(title_set_records, "hijacked_title_and_issn")
    identity_set_summary = summarize_set(identity_set_records, "identity_set")

    # final assessment by ISSN-relationship category, identity set
    by_key = {r["entry_key"]: r for r in identity_set_records}
    category_breakdown: dict[str, dict] = {}
    for key, cat in category_by_key.items():
        rec = by_key.get(key)
        if rec is None:
            continue
        category_breakdown.setdefault(cat, Counter())
        category_breakdown[cat][rec.get("final_assessment")] += 1

    category_breakdown_out = {cat: dict(counter) for cat, counter in category_breakdown.items()}

    return {
        "hijacked_title_and_issn_set": title_set_summary,
        "identity_set": identity_set_summary,
        "issn_relationship_category_counts": {
            cat: sum(v.values()) for cat, v in category_breakdown.items()
        },
        "final_assessment_by_issn_category_identity_set": category_breakdown_out,
        "difference_title_set_minus_identity_set": {
            "predatory_pct_full": round(
                (title_set_summary["final_assessment_full"].get("predatory", {}).get("pct") or 0)
                - (identity_set_summary["final_assessment_full"].get("predatory", {}).get("pct") or 0), 3
            ),
            "predatory_pct_disjoint": round(
                (title_set_summary["final_assessment_disjoint"].get("predatory", {}).get("pct") or 0)
                - (identity_set_summary["final_assessment_disjoint"].get("predatory", {}).get("pct") or 0), 3
            ),
            "is_hijacked_pct_full": round(
                (title_set_summary["is_hijacked_full"]["pct"] or 0) - (identity_set_summary["is_hijacked_full"]["pct"] or 0), 3
            ),
        },
    }


def found_backend_names(record: dict) -> list[str]:
    return [b["backend_name"] for b in record.get("backend_results", []) if b.get("status") == "found"]


def negative_control_bucket(record: dict) -> str:
    """'beyond_doaj' if an independent legitimacy backend found the record, 'doaj_only' if only the doaj backend did,
    else 'neither'."""
    found_backends = found_backend_names(record)
    if any(b in INDEPENDENT_LEGITIMACY_BACKENDS for b in found_backends):
        return "beyond_doaj"
    if "doaj" in found_backends:
        return "doaj_only"
    return "neither"


def negative_control_corroboration() -> dict:
    neg_records = load_jsonl(BASE / "mass_eval_output" / "negative_control__negative_control.jsonl")
    no_list_hit = [r for r in neg_records if not watchlist_overlap(r)]

    bucket_counts = Counter()
    n_found_by = Counter()
    for r in no_list_hit:
        for b in found_backend_names(r):
            n_found_by[b] += 1
        bucket_counts[negative_control_bucket(r)] += 1

    n_beyond_doaj = bucket_counts["beyond_doaj"]
    n_doaj_only = bucket_counts["doaj_only"]
    n_neither = bucket_counts["neither"]

    return {
        "n_no_list_hit_entries": len(no_list_hit),
        "n_found_beyond_doaj": n_beyond_doaj,
        "pct_found_beyond_doaj": round(100.0 * n_beyond_doaj / len(no_list_hit), 3) if no_list_hit else None,
        "n_found_only_by_doaj_backend": n_doaj_only,
        "pct_found_only_by_doaj_backend": round(100.0 * n_doaj_only / len(no_list_hit), 3) if no_list_hit else None,
        "n_found_by_neither_doaj_nor_independent": n_neither,
        "found_count_per_backend": dict(n_found_by.most_common()),
        "all_independent_backends_checked": INDEPENDENT_LEGITIMACY_BACKENDS,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {"identity_set_comparison": identity_set_comparison(), "negative_control_corroboration": negative_control_corroboration()}
    (OUTPUT_DIR / "identity_and_negative_control_analysis.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
