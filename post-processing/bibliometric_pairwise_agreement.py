#!/usr/bin/env python3
"""Pairwise agreement of the bibliometric backends openalex_analyzer, crossref_analyzer and opencitations_analyzer over all
assessed files with entry_count < LARGE_FILE_ENTRIES (1000): per pair, the number of entries for which both backends have
status "found" and the number among them with equal assessment. Reads EVAL_DB_PATH (files table) and the per-run JSONL files
in EVAL_RESULTS_ROOT. Writes bibliometric_pairwise_agreement.json to
EVAL_OUTPUT_DIR/output_bibliometric_pairwise_agreement and numbers/bibliometric_pairwise_agreement.numbers.json
(keys pairwise_*; pairwise_n_bloated_files is the number of large files, i.e. files with at least LARGE_FILE_ENTRIES entries,
which are left out; each pairwise_*_rate is the share of equal assessments among the entries for which both backends have status
"found").
"""
from __future__ import annotations

import json

import stats_common
from stats_common import LARGE_FILE_ENTRIES, get_conn, pct

RESULTS_ROOT = stats_common.required_path_env("EVAL_RESULTS_ROOT")
OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_bibliometric_pairwise_agreement"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()

    all_files = conn.execute(
        "SELECT month, name, entry_count FROM files WHERE category='assessed'"
    ).fetchall()
    n_total_assessed = len(all_files)

    small_files = [r for r in all_files if r["entry_count"] < LARGE_FILE_ENTRIES]
    large_files = [r for r in all_files if r["entry_count"] >= LARGE_FILE_ENTRIES]
    n_small_files = len(small_files)
    n_large_files = len(large_files)
    assert n_small_files + n_large_files == n_total_assessed

    n_entries_small = sum(r["entry_count"] for r in small_files)
    n_entries_large = sum(r["entry_count"] for r in large_files)

    pairs = [
        ("openalex_analyzer", "crossref_analyzer"),
        ("openalex_analyzer", "opencitations_analyzer"),
        ("crossref_analyzer", "opencitations_analyzer"),
    ]
    pair_stats = {f"{a}_vs_{b}": {"both_found": 0, "agree": 0} for a, b in pairs}

    n_files_processed = 0
    n_files_missing = 0
    n_entries_scanned = 0

    for r in small_files:
        name = f"{r['month']}__{r['name']}"
        path = RESULTS_ROOT / f"{name}.jsonl"
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n_entries_scanned += 1
                    by_name = {
                        b.get("backend_name"): b
                        for b in (rec.get("backend_results") or [])
                        if isinstance(b, dict)
                    }
                    for a, b in pairs:
                        ba, bb = by_name.get(a), by_name.get(b)
                        if ba and bb and ba.get("status") == "found" and bb.get("status") == "found":
                            key = f"{a}_vs_{b}"
                            pair_stats[key]["both_found"] += 1
                            if ba.get("assessment") == bb.get("assessment"):
                                pair_stats[key]["agree"] += 1
            n_files_processed += 1
        except FileNotFoundError:
            n_files_missing += 1
            continue

    pairwise_out = {}
    for key, s in pair_stats.items():
        pairwise_out[key] = {
            "both_found_n": s["both_found"],
            "agree_n": s["agree"],
            "agree_pct": pct(s["agree"], s["both_found"]),
        }

    out = {
        "file_inclusion": {
            "n_total_assessed_files": n_total_assessed,
            "n_small_files_included": n_small_files,
            "n_large_files_excluded": n_large_files,
            "large_file_entry_count_gte": LARGE_FILE_ENTRIES,
            "n_entries_in_included_small_files": n_entries_small,
            "n_entries_in_excluded_large_files": n_entries_large,
            "n_files_processed": n_files_processed,
            "n_files_missing_on_disk": n_files_missing,
            "n_entries_scanned": n_entries_scanned,
        },
        "pairwise_agreement": pairwise_out,
    }
    stats_common.write_json("bibliometric_pairwise_agreement", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("bibliometric_pairwise_agreement", {
        "pairwise_n_normal_files": {"kind": "int", "value": n_small_files},
        "pairwise_n_bloated_files": {"kind": "int", "value": n_large_files},
        "pairwise_oa_oc_both_found": {"kind": "int", "value": pair_stats["openalex_analyzer_vs_opencitations_analyzer"]["both_found"]},
        "pairwise_oa_oc_rate": {
            "kind": "ratio",
            "num": pair_stats["openalex_analyzer_vs_opencitations_analyzer"]["agree"],
            "den": pair_stats["openalex_analyzer_vs_opencitations_analyzer"]["both_found"],
            "decimals": 1,
        },
        "pairwise_cr_oc_both_found": {"kind": "int", "value": pair_stats["crossref_analyzer_vs_opencitations_analyzer"]["both_found"]},
        "pairwise_cr_oc_rate": {
            "kind": "ratio",
            "num": pair_stats["crossref_analyzer_vs_opencitations_analyzer"]["agree"],
            "den": pair_stats["crossref_analyzer_vs_opencitations_analyzer"]["both_found"],
            "decimals": 1,
        },
        "pairwise_oa_cr_both_found": {"kind": "int", "value": pair_stats["openalex_analyzer_vs_crossref_analyzer"]["both_found"]},
        "pairwise_oa_cr_rate": {
            "kind": "ratio",
            "num": pair_stats["openalex_analyzer_vs_crossref_analyzer"]["agree"],
            "den": pair_stats["openalex_analyzer_vs_crossref_analyzer"]["both_found"],
            "decimals": 1,
        },
    })


if __name__ == "__main__":
    main()
