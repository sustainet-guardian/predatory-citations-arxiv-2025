#!/usr/bin/env python3
"""Assessment distribution over the distinct venues (venue_raw) of the largest ACL Anthology .bib file (most entries among
the large files, i.e. assessed files with at least LARGE_FILE_ENTRIES entries, that have an ACL Anthology URL) and of the largest cryptobib file (same, with an
eprint.iacr.org URL and no ACL Anthology URL), from the per-entry records in EVAL_RESULTS_ROOT and the files table of
EVAL_DB_PATH (.bib files in EVAL_BIB_ROOT). Writes bulk_database_assessment.json (per file: record count, distinct venues,
assessment distribution, number of venues with inconsistent assessment, number of flagged venues: predatory/suspicious
assessment, hijacked vote or list hit) and bulk_database_venues_acl.csv (all ACL venues, column venue; read by
add_one_new_sources_impact.py) into EVAL_OUTPUT_DIR, and numbers/analyze_bulk_database_assessment.numbers.json (keys bulk_*:
per family the distinct venues, the flagged venues, the flagged venues with a suspicious assessment, the venues with a
predatory assessment, a hijacked vote or a list hit, the venues with a Retraction Watch record, and the venues
whose Retraction Watch vote is suspicious or predatory). The vote and retraction columns are taken from the first record of
each venue, as the assessment is.
"""
from __future__ import annotations

import json
from collections import Counter

from result_line import has_hijacked_vote
from stats_common import LARGE_FILE_ENTRIES, get_conn, required_path_env, write_csv, write_json, write_numbers

RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")
BIB_ROOT = required_path_env("EVAL_BIB_ROOT")


def analyze_file(month: str, name: str, label: str) -> dict:
    path = RESULTS_ROOT / f"{month}__{name}.jsonl"
    by_venue: dict[str, dict] = {}
    n_records = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n_records += 1
            venue = rec.get("venue_raw") or "(empty)"
            if venue not in by_venue:
                by_venue[venue] = {
                    "venue": venue,
                    "final_assessment": rec.get("final_assessment"),
                    "predatory_list_hits": rec.get("predatory_list_hits") or [],
                    "assessment_ever_differs": False,
                    "has_hijacked_vote": has_hijacked_vote(rec),
                    "has_retraction_record": any(
                        b.get("backend_name") == "retraction_watch" and b.get("status") == "found"
                        and bool((b.get("data") or {}).get("total_retractions"))
                        for b in rec.get("backend_results") or []
                    ),
                    # Retraction tiers low and above vote suspicious or predatory; tier note votes legitimate.
                    "has_adverse_retraction_signal": any(
                        b.get("backend_name") == "retraction_watch" and b.get("status") == "found"
                        and b.get("assessment") in ("suspicious", "predatory")
                        for b in rec.get("backend_results") or []
                    ),
                }
            entry = by_venue[venue]
            if rec.get("final_assessment") != entry["final_assessment"]:
                entry["assessment_ever_differs"] = True

    assessment_counts = Counter(v["final_assessment"] for v in by_venue.values())
    flagged = [
        v for v in by_venue.values()
        if v["final_assessment"] in ("predatory", "suspicious")
        or v["has_hijacked_vote"]
        or v["predatory_list_hits"]
    ]

    return {
        "label": label,
        "n_flagged_suspicious": sum(1 for v in flagged if v["final_assessment"] == "suspicious"),
        "n_predatory_hijacked_or_listed": sum(
            1 for v in by_venue.values()
            if v["final_assessment"] == "predatory" or v["has_hijacked_vote"] or v["predatory_list_hits"]
        ),
        "n_with_retraction_record": sum(1 for v in by_venue.values() if v["has_retraction_record"]),
        "n_with_adverse_retraction_signal": sum(1 for v in by_venue.values() if v["has_adverse_retraction_signal"]),
        "n_records": n_records,
        "n_distinct_venues": len(by_venue),
        "assessment_distribution": dict(assessment_counts),
        "n_venues_with_inconsistent_assessment_across_entries": sum(
            1 for v in by_venue.values() if v["assessment_ever_differs"]
        ),
        "n_flagged_venues": len(flagged),
        "all_venues": list(by_venue.values()),
    }


def main() -> None:
    conn = get_conn()
    # representatives: the largest file (entry_count) with an ACL Anthology URL, and the largest with an eprint.iacr.org URL
    # and no ACL Anthology URL (ties between ACL files: first by month, name).
    rows = conn.execute(
        "SELECT month, name, entry_count FROM files WHERE category='assessed' AND entry_count >= ?", (LARGE_FILE_ENTRIES,)
    ).fetchall()
    best_acl = None
    best_cryptobib = None
    for r in rows:
        p = BIB_ROOT / r["month"] / f"{r['name']}.bib"
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "aclanthology.org" in text or "aclweb.org/anthology" in text:
            if best_acl is None or (-r["entry_count"], r["month"], r["name"]) < (
                    -best_acl["entry_count"], best_acl["month"], best_acl["name"]):
                best_acl = r
        elif "eprint.iacr.org" in text:
            if best_cryptobib is None or r["entry_count"] > best_cryptobib["entry_count"]:
                best_cryptobib = r
    print(f"largest ACL Anthology file: {best_acl['entry_count']} entries")
    print(f"largest cryptobib file: {best_cryptobib['entry_count']} entries")

    results = []
    results.append(analyze_file(best_acl["month"], best_acl["name"], "ACL Anthology (largest snapshot)"))
    results.append(analyze_file(best_cryptobib["month"], best_cryptobib["name"], "cryptobib / IACR ePrint (largest snapshot)"))

    for r in results:
        print(f"\n=== {r['label']} ===")
        print(f"records: {r['n_records']}, distinct venues: {r['n_distinct_venues']}")
        print(f"assessment distribution: {r['assessment_distribution']}")
        print(f"flagged venues: {r['n_flagged_venues']}")

    write_json("bulk_database_assessment", {
        r["label"]: {k: v for k, v in r.items() if k != "all_venues"} for r in results
    })
    acl = next(r for r in results if 'ACL' in r['label'])
    cryptobib = next(r for r in results if 'cryptobib' in r['label'])
    numbers = {}
    for family, r in (("acl", acl), ("cryptobib", cryptobib)):
        numbers[f"bulk_{family}_distinct_venues"] = {"kind": "int", "value": r["n_distinct_venues"]}
        numbers[f"bulk_{family}_flagged_venues"] = {"kind": "int", "value": r["n_flagged_venues"]}
        numbers[f"bulk_{family}_flagged_suspicious_venues"] = {"kind": "int", "value": r["n_flagged_suspicious"]}
        numbers[f"bulk_{family}_predatory_hijacked_or_listed_venues"] = {"kind": "int", "value": r["n_predatory_hijacked_or_listed"]}
        numbers[f"bulk_{family}_retraction_record_venues"] = {"kind": "int", "value": r["n_with_retraction_record"]}
        numbers[f"bulk_{family}_adverse_retraction_signal_venues"] = {"kind": "int", "value": r["n_with_adverse_retraction_signal"]}
    write_numbers("analyze_bulk_database_assessment", numbers)
    write_csv("bulk_database_venues_acl", [{"venue": v["venue"]} for v in acl["all_venues"]], ["venue"])


if __name__ == "__main__":
    main()
