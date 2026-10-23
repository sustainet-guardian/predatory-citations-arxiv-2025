#!/usr/bin/env python3
"""Disciplinary composition (Computer Science, Mathematics, Physics, Other) of the included submissions (those with a
usable .bib file, i.e. present in the entries table) and the excluded submissions (all other rows of the categories table),
as counts and shares (denominators: the included and the excluded submissions of the categories table). Categories come from
the snapshot category database EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission of the arXiv metadata snapshot, made by
build_snapshot_categories_db.py), not from the API database EVAL_ARXIV_CATEGORIES_DB (fetch_arxiv_categories.py), which holds
the confirmed-cited submissions only while the excluded submissions have no confirmed-cited entries. Reads also
EVAL_DB_PATH_BBL_TEX. Writes
numbers/disciplinary_composition_included_vs_excluded.numbers.json (keys disccompincl_*, disccompexcl_*; shares of the three
named groups) and disciplinary_composition_included_vs_excluded.json in
EVAL_OUTPUT_DIR/output_disciplinary_composition_included_vs_excluded.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import stats_common
from stats_common import pct, required_path_env, write_json, write_numbers
from disciplinary_classification import classify, snapshot_categories_db

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_disciplinary_composition_included_vs_excluded"
stats_common.OUTPUT_DIR = OUTPUT_DIR

DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    in_scope = {
        (r["month"], r["arxiv_id"])
        for r in conn.execute("SELECT DISTINCT month, arxiv_id FROM entries").fetchall()
    }

    cat_conn = sqlite3.connect(str(snapshot_categories_db()))
    cat_conn.row_factory = sqlite3.Row
    cat_rows = cat_conn.execute("SELECT month, arxiv_id, primary_category FROM categories").fetchall()

    included_counts: Counter = Counter()
    excluded_counts: Counter = Counter()
    n_categories_total = len(cat_rows)
    n_categories_included = 0
    n_categories_excluded = 0
    for r in cat_rows:
        key = (r["month"], r["arxiv_id"])
        group = classify(r["primary_category"])
        if key in in_scope:
            included_counts[group] += 1
            n_categories_included += 1
        else:
            excluded_counts[group] += 1
            n_categories_excluded += 1

    out = {
        "reference_universe_n": n_categories_total,
        "n_included_via_categories_db": n_categories_included,
        "n_excluded_via_categories_db": n_categories_excluded,
        "n_covered_via_entries_db": len(in_scope),
        "included": {g: {"n": n, "pct": pct(n, n_categories_included)} for g, n in included_counts.most_common()},
        "excluded": {g: {"n": n, "pct": pct(n, n_categories_excluded)} for g, n in excluded_counts.most_common()},
    }
    write_json("disciplinary_composition_included_vs_excluded", out)
    print(json.dumps(out, indent=2, default=str))

    GROUP_KEYS = {"Computer Science": "cs", "Mathematics": "math", "Physics": "physics"}
    numbers_payload = {}
    for group, key in GROUP_KEYS.items():
        numbers_payload[f"disccompincl_{key}_pct"] = {
            "kind": "ratio", "num": included_counts.get(group, 0), "den": n_categories_included, "decimals": 1,
        }
        numbers_payload[f"disccompexcl_{key}_pct"] = {
            "kind": "ratio", "num": excluded_counts.get(group, 0), "den": n_categories_excluded, "decimals": 1,
        }
    write_numbers("disciplinary_composition_included_vs_excluded", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
