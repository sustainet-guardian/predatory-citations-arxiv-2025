#!/usr/bin/env python3
"""Builds the SQLite database EVAL_SNAPSHOT_CATEGORIES_DB from the CSV EVAL_CATEGORIES_CSV_PATH written by
extract_arxiv_categories_from_local_snapshot.py. It creates one table, `categories` (columns month, arxiv_id, primary_category,
all_categories (comma-separated), found (1 if the snapshot record has categories, else 0); no primary key; index
idx_month_arxiv on (month, arxiv_id)). The columns are those of the table written by fetch_arxiv_categories.py
(EVAL_ARXIV_CATEGORIES_DB) except full_id, which this table does not have. An existing file at EVAL_SNAPSHOT_CATEGORIES_DB is replaced.
This snapshot database holds every 2025 submission of the arXiv metadata snapshot (the API database holds the confirmed-cited
submissions only, so the submissions without a confirmed-cited entry exist only here). It is read by
analyze_excluded_submissions, citation_level_disciplinary_rates_by_group, disciplinary_composition_included_vs_excluded,
disciplinary_table_and_venue_concentration, identity_check_headline_analyses and second_vote_survivors_by_discipline; the two
Mathematics broad-rate scripts read the API database.
"""
from __future__ import annotations

import csv
import sqlite3

from stats_common import required_path_env

CSV_PATH = required_path_env("EVAL_CATEGORIES_CSV_PATH")
DB_PATH = required_path_env("EVAL_SNAPSHOT_CATEGORIES_DB")


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE categories (month TEXT, arxiv_id TEXT, primary_category TEXT, all_categories TEXT, found INTEGER)"
    )
    rows = []
    with CSV_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append((row["month"], row["arxiv_id"], row["primary_category"], row["all_categories"], int(row["found"])))
    conn.executemany("INSERT INTO categories VALUES (?, ?, ?, ?, ?)", rows)
    conn.execute("CREATE INDEX idx_month_arxiv ON categories(month, arxiv_id)")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    print(f"wrote {n} rows to {DB_PATH}")


if __name__ == "__main__":
    main()
