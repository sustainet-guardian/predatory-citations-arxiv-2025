#!/usr/bin/env python3
"""Extracts the subject categories of every 2025 submission (ids starting with 25) from the metadata snapshot
EVAL_ARXIV_SNAPSHOT_JSON (one JSON record per line, field "categories": primary category first, then cross-lists,
space-separated). The snapshot is arXiv's metadata dataset ("arXiv Dataset"), whose metadata licence arXiv states as CC0.
Writes EVAL_CATEGORIES_CSV_PATH with columns month, arxiv_id, full_id, primary_category, all_categories (comma-separated)
and found (1 if the record has categories, else 0). Creates no database; the CSV is read only by
build_snapshot_categories_db.py, which writes the snapshot database EVAL_SNAPSHOT_CATEGORIES_DB (table categories, without full_id).
"""
from __future__ import annotations

import csv
import json

from stats_common import required_path_env

SNAPSHOT_PATH = required_path_env("EVAL_ARXIV_SNAPSHOT_JSON")
OUT_PATH = required_path_env("EVAL_CATEGORIES_CSV_PATH")


def main() -> None:
    n_total_2025 = 0
    n_written = 0
    rows = []

    with SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            full_id = rec.get("id", "")
            if not full_id.startswith("25") or "." not in full_id:
                continue
            month, arxiv_id = full_id.split(".", 1)
            if len(month) != 4 or not month.isdigit():
                continue

            n_total_2025 += 1

            categories_field = rec.get("categories") or ""
            cats = categories_field.split()
            if not cats:
                rows.append((month, arxiv_id, full_id, None, None, 0))
                continue

            primary_category = cats[0]
            all_categories = ",".join(cats)
            rows.append((month, arxiv_id, full_id, primary_category, all_categories, 1))
            n_written += 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["month", "arxiv_id", "full_id", "primary_category", "all_categories", "found"])
        writer.writerows(rows)

    print(f"2025 records seen in snapshot: {n_total_2025}")
    print(f"records with resolvable category data: {n_written}")
    print(f"records with no category data: {n_total_2025 - n_written}")
    print(f"wrote: {OUT_PATH}")


if __name__ == "__main__":
    main()
