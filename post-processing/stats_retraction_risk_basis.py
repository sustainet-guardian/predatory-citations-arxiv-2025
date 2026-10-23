#!/usr/bin/env python3
"""Basis of the retraction-risk tier. For every entry in entries_confirmed_bbl_tex (EVAL_DB_PATH_BBL_TEX) with recorded tier
moderate, high or critical, the basis fields (total/recent publications and retractions) are read from the "retraction_watch"
backend result in the per-run JSONL files of EVAL_RESULTS_ROOT. The tier is rate-based when total_publications is positive and
count-based otherwise (the basis column is rate_based, count_based or missing_backend_record). With --write-venue-table it
writes retraction_risk_basis_all_entries.csv directly into EVAL_OUTPUT_DIR (no output_<script> subdirectory; one row per such
entry: venue name and basis fields; read by path by retraction_risk_tiers.py, identity_check_headline_analyses.py and
any_documented_concern_decomposition.py); without the flag it only prints the counts. load_retraction_data() is also used by
retraction_risk_tiers.py. No numbers file, no ratio.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from stats_common import required_path_env, write_csv

RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")
DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")


def jsonl_path(month: str, arxiv_id: str, suffix: str) -> Path:
    return RESULTS_ROOT / f"{month}__{arxiv_id}-{suffix}.jsonl"


def load_retraction_data(targets: list[tuple[str, str, str, str]]) -> dict[tuple, dict]:
    """targets: (month, arxiv_id, suffix, entry_key). Returns
    {(month, arxiv_id, suffix, entry_key): retraction_watch data dict}."""
    by_file: dict[Path, set[str]] = defaultdict(set)
    file_of: dict[Path, tuple[str, str, str]] = {}
    for month, arxiv_id, suffix, entry_key in targets:
        p = jsonl_path(month, arxiv_id, suffix)
        by_file[p].add(entry_key)
        file_of[p] = (month, arxiv_id, suffix)

    out: dict[tuple, dict] = {}
    for path, wanted in by_file.items():
        if not path.exists():
            continue
        month, arxiv_id, suffix = file_of[path]
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ek = rec.get("entry_key")
                if ek not in wanted:
                    continue
                for br in rec.get("backend_results", []):
                    if br.get("backend_name") == "retraction_watch" and br.get("status") == "found":
                        out[(month, arxiv_id, suffix, ek)] = br.get("data", {})
                        break
    return out


def is_rate_based(data: dict) -> bool:
    tp = data.get("total_publications")
    return bool(tp) and tp > 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-venue-table", action="store_true",
                        help="also write retraction_risk_basis_all_entries.csv")
    args = parser.parse_args()
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, retraction_risk_level, "
        "COALESCE(matched_venue_name, venue_raw) AS venue "
        "FROM entries_confirmed_bbl_tex WHERE retraction_risk_level IN ('moderate', 'high', 'critical')"
    ).fetchall()
    print(f"target citations: {len(rows)}")

    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    data_by_key = load_retraction_data(targets)
    print(f"matched retraction_watch records: {len(data_by_key)}")

    by_basis: Counter[str] = Counter()
    all_rows_detail: list[dict] = []

    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        data = data_by_key.get(key)
        if data is None:
            basis = "missing_backend_record"
        else:
            basis = "rate_based" if is_rate_based(data) else "count_based"
        by_basis[basis] += 1
        all_rows_detail.append({
            "venue": r["venue"] or "(unknown)", "basis": basis,
            "total_retractions": data.get("total_retractions") if data else None,
            "recent_retractions": data.get("recent_retractions") if data else None,
            "total_publications": data.get("total_publications") if data else None,
            "recent_publications": data.get("recent_publications") if data else None,
        })

    print(f"basis of the target citations: {dict(by_basis)}")
    if args.write_venue_table:
        write_csv(
            "retraction_risk_basis_all_entries",
            all_rows_detail,
            list(all_rows_detail[0].keys()),
        )


if __name__ == "__main__":
    main()
