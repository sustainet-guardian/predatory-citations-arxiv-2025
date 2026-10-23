#!/usr/bin/env python3
"""Mutates EVAL_DB_PATH_MISSED_KEYS_ROWS, a copy of EVAL_DB_PATH_BBL_TEX (it must exist, differ from EVAL_DB_PATH and already have
citation_status_bbl_tex): adds the column added_missed_key_row and inserts one entries row for every key that (a) is in the
.bbl/.tex signal of EVAL_BBL_JSONL_PATH, (b) is a key of a .bib file that has a citation log (-cited.csv), and (c) is not
listed in that citation log. Every column that build_entry_table.py takes from the results line is derived by the same function
(result_line.result_line_columns) from EVAL_RESULTS_ROOT/<month>__<id>-<n>.jsonl, entry_type and pub_year from the .bib file in
EVAL_BIB_ROOT. Inserted rows have is_cited = '1', cited_count NULL, citation_status NULL,
citation_status_bbl_tex = 'confirmed_cited', citation_status_bbl_tex_changed = '1' and added_missed_key_row = '1'; the column
added_missed_key_row is NULL on every other row. Existing rows are not modified; the script refuses to run twice on the same
database. The files are processed by a pool of os.cpu_count() worker processes. The .bib parser is the one of build_entry_table.py
(it needs the tool and pybtex).
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from build_entry_table import ENTRY_COLUMNS, entry_type_and_year, load_bib_entries
from result_line import result_line_columns
from stats_common import required_path_env

SOURCE_DB = required_path_env("EVAL_DB_PATH")
TARGET_DB = required_path_env("EVAL_DB_PATH_MISSED_KEYS_ROWS")
JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
BIB_ROOT = required_path_env("EVAL_BIB_ROOT")
RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")

# the columns of build_entry_table.py plus four columns of the database of the citation status
INSERTED_COLUMNS = ENTRY_COLUMNS + [
    "citation_status", "citation_status_bbl_tex", "citation_status_bbl_tex_changed", "added_missed_key_row",
]


def load_cited_csv_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("citation_key")
            if key is not None:
                out.add(key)
    return out


def process_file(month: str, name: str, missed_keys: frozenset[str]) -> list[tuple]:
    """Returns INSERTED_COLUMNS-ordered rows for the missed keys of one file: entry_type/pub_year from the .bib, the rest from the
    results JSONL through result_line_columns."""
    arxiv_id, suffix = name.rsplit("-", 1)
    bib_path = BIB_ROOT / month / f"{name}.bib"
    bib_entries = load_bib_entries(bib_path)

    results_path = RESULTS_ROOT / f"{month}__{name}.jsonl"
    rows: list[tuple] = []
    if not results_path.exists():
        return rows

    with results_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            entry_key = rec.get("entry_key")
            if entry_key not in missed_keys:
                continue

            entry = bib_entries.get(entry_key)
            entry_type, pub_year = entry_type_and_year(entry) if entry is not None else (None, None)
            values = result_line_columns(rec)
            values.update(
                month=month, arxiv_id=arxiv_id, suffix=suffix, entry_key=entry_key, entry_type=entry_type, pub_year=pub_year,
                is_cited=1, cited_count=None, has_cited_sidecar=1,  # cited_count is unknown (NULL); the file has a citation log
                citation_status=None, citation_status_bbl_tex="confirmed_cited", citation_status_bbl_tex_changed="1",
                added_missed_key_row="1",
            )
            rows.append(tuple(values[c] for c in INSERTED_COLUMNS))
    return rows


def load_extraction_index() -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            key = (rec["month"], rec["arxiv_id"])
            cited_keys = frozenset(rec.get("bbl_keys") or []) | frozenset(rec.get("tex_cite_keys") or [])
            by_suffix = {bf["suffix"]: frozenset(bf["raw_keys"]) for bf in rec.get("bib_files") or []}
            index[key] = {"cited_keys": cited_keys, "by_suffix": by_suffix}
    return index


def main() -> None:
    target_db = TARGET_DB.resolve()

    if target_db == SOURCE_DB.resolve():
        raise SystemExit(f"refusing to modify EVAL_DB_PATH ({SOURCE_DB})")
    if not target_db.exists():
        raise SystemExit(f"{target_db} does not exist")

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    if "citation_status_bbl_tex" not in cols:
        raise SystemExit(f"{target_db} has no citation_status_bbl_tex column -- run set_citation_status_bbl_tex.py first")
    if "added_missed_key_row" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN added_missed_key_row TEXT")
        conn.commit()

    existing = conn.execute(
        "SELECT COUNT(*) n FROM entries WHERE added_missed_key_row='1'"
    ).fetchone()["n"]
    if existing:
        raise SystemExit(
            f"{target_db} already has {existing} added_missed_key_row rows -- "
            f"refusing to run again and duplicate them"
        )

    print("loading .bbl/.tex extraction index ...", flush=True)
    index = load_extraction_index()

    files = conn.execute(
        "SELECT month, name FROM files WHERE has_cited_sidecar=1 AND category='assessed'"
    ).fetchall()
    print(f"files with a citation log to check: {len(files)}", flush=True)

    tasks = []
    for r in files:
        arxiv_id, suffix_str = r["name"].rsplit("-", 1)
        suffix = int(suffix_str)
        key = (r["month"], arxiv_id)
        rec = index.get(key)
        if rec is None or suffix not in rec["by_suffix"] or not rec["cited_keys"]:
            continue
        bib_keys = rec["by_suffix"][suffix]
        relevant = bib_keys & rec["cited_keys"]
        if not relevant:
            continue
        logged_keys = load_cited_csv_keys(BIB_ROOT / r["month"] / f"{r['name']}-cited.csv")
        missed = relevant - logged_keys
        if missed:
            tasks.append((r["month"], r["name"], frozenset(missed)))

    print(f"files with missed keys to process: {len(tasks)}", flush=True)

    all_rows: list[tuple] = []
    n_done = 0
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        futures = {pool.submit(process_file, m, n, mk): (m, n) for m, n, mk in tasks}
        for fut in as_completed(futures):
            all_rows.extend(fut.result())
            n_done += 1
            if n_done % 2000 == 0:
                print(f"  {n_done}/{len(tasks)} files processed, {len(all_rows)} rows so far ...", flush=True)

    print(f"total rows to insert: {len(all_rows)}", flush=True)

    placeholders = ",".join("?" * len(INSERTED_COLUMNS))
    conn.executemany(
        f"INSERT INTO entries ({', '.join(INSERTED_COLUMNS)}) VALUES ({placeholders})",
        all_rows,
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    n_confirmed_bbl_tex = conn.execute("SELECT COUNT(*) FROM entries_confirmed_bbl_tex").fetchone()[0]
    print(f"\ntotal rows in entries: {total}")
    print(f"rows in entries_confirmed_bbl_tex: {n_confirmed_bbl_tex}")


if __name__ == "__main__":
    main()
