#!/usr/bin/env python3
"""Counts the venue-name variants that the acronym table of the tool's cache database holds for the acronym AAAI (tables
venue_acronyms and venue_acronym_variants, all entity types of the acronym, case-insensitive). Reads EVAL_CACHE_DB_PATH
read-only. Writes no output file besides numbers/acronym_table_variant_counts.numbers.json (key acronym_table_aaai_variants).
"""
from __future__ import annotations

import sqlite3

from stats_common import required_path_env, write_numbers

CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")


def main() -> None:
    conn = sqlite3.connect(f"file:{CACHE_DB_PATH}?mode=ro", uri=True)
    n_variants = conn.execute(
        "SELECT COUNT(DISTINCT LOWER(v.variant)) FROM venue_acronym_variants v "
        "JOIN venue_acronyms a ON a.id = v.venue_acronym_id WHERE a.acronym = 'AAAI' COLLATE NOCASE"
    ).fetchone()[0]
    print(f"variants of the acronym AAAI: {n_variants}")
    write_numbers("acronym_table_variant_counts", {"acronym_table_aaai_variants": {"kind": "int", "value": n_variants}})


if __name__ == "__main__":
    main()
