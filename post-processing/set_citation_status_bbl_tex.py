#!/usr/bin/env python3
"""Adds citation_status_bbl_tex and citation_status_bbl_tex_changed to the entries table of EVAL_DB_PATH_BBL_TEX (a copy of
EVAL_DB_PATH that must exist and differ from it) and creates the view entries_confirmed_bbl_tex (rows with
citation_status_bbl_tex = 'confirmed_cited'). The signal is the per-submission union of bbl_keys and tex_cite_keys from
EVAL_BBL_JSONL_PATH (written by extract_citation_keys.py; tar_gz submissions with at least one key). citation_status_bbl_tex is
confirmed_cited for an entry whose key is in the signal of its submission and whose citation_status is unverified_isolated,
excluded_sibling_inferred_uncited or confirmed_cited, and for a confirmed_cited entry of a submission without signal;
excluded_comment_artifact_suspected for a confirmed_cited entry whose submission has a signal that does not contain its key;
otherwise it equals citation_status. citation_status_bbl_tex_changed is '1' for the rows whose citation_status_bbl_tex differs from
citation_status and '0' for all others. --target-db FILE replaces EVAL_DB_PATH_BBL_TEX. The script prints the number of rows
with each status of both columns and the sizes of entries_confirmed and entries_confirmed_bbl_tex.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from stats_common import required_path_env

SOURCE_DB = required_path_env("EVAL_DB_PATH")
DEFAULT_TARGET_DB = required_path_env("EVAL_DB_PATH_BBL_TEX")
JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")


def load_signal_rows() -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Returns (signal_rows, has_signal_rows) for bulk insert into temp tables: every (month, arxiv_id, entry_key) found in
    the .bbl/.tex signal, and every (month, arxiv_id) that has a non-empty signal."""
    signal_rows: list[tuple[str, str, str]] = []
    has_signal_rows: list[tuple[str, str]] = []
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            month, arxiv_id = rec["month"], rec["arxiv_id"]
            signal_keys = set(rec.get("bbl_keys") or []) | set(rec.get("tex_cite_keys") or [])
            if rec["file_type"] != "tar_gz" or not signal_keys:
                continue
            has_signal_rows.append((month, arxiv_id))
            for key in signal_keys:
                signal_rows.append((month, arxiv_id, key))
    return signal_rows, has_signal_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    args = parser.parse_args()
    target_db = args.target_db.resolve()

    if target_db == SOURCE_DB.resolve():
        raise SystemExit(f"refusing to patch the source database ({SOURCE_DB})")
    if not target_db.exists():
        raise SystemExit(
            f"{target_db} does not exist -- copy it first, e.g.:\n"
            f"  cp {SOURCE_DB} {target_db}"
        )

    print("loading .bbl/.tex signal ...", flush=True)
    signal_rows, has_signal_rows = load_signal_rows()
    print(f"  {len(signal_rows)} (month, arxiv_id, entry_key) signal rows, "
          f"{len(has_signal_rows)} submissions with usable signal", flush=True)

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(entries)").fetchall()]
    if "citation_status_bbl_tex" not in cols:
        cur.execute("ALTER TABLE entries ADD COLUMN citation_status_bbl_tex TEXT")
    if "citation_status_bbl_tex_changed" not in cols:
        cur.execute("ALTER TABLE entries ADD COLUMN citation_status_bbl_tex_changed TEXT")

    print("building temporary signal tables ...", flush=True)
    cur.execute("DROP TABLE IF EXISTS temp.bbl_tex_signal")
    cur.execute("CREATE TEMP TABLE bbl_tex_signal (month TEXT, arxiv_id TEXT, entry_key TEXT)")
    cur.executemany("INSERT INTO bbl_tex_signal VALUES (?, ?, ?)", signal_rows)
    cur.execute("CREATE INDEX idx_bts ON bbl_tex_signal(month, arxiv_id, entry_key)")

    cur.execute("DROP TABLE IF EXISTS temp.has_bbl_tex_signal")
    cur.execute("CREATE TEMP TABLE has_bbl_tex_signal (month TEXT, arxiv_id TEXT)")
    cur.executemany("INSERT INTO has_bbl_tex_signal VALUES (?, ?)", has_signal_rows)
    cur.execute("CREATE INDEX idx_hbts ON has_bbl_tex_signal(month, arxiv_id)")
    conn.commit()

    print("setting citation_status_bbl_tex to citation_status ...", flush=True)
    cur.execute("UPDATE entries SET citation_status_bbl_tex = citation_status, citation_status_bbl_tex_changed = '0'")

    print("setting confirmed_cited where citation_status is unverified_isolated and the key is in the signal ...", flush=True)
    cur.execute(
        """
        UPDATE entries SET citation_status_bbl_tex = 'confirmed_cited', citation_status_bbl_tex_changed = '1'
        WHERE citation_status = 'unverified_isolated' AND EXISTS (
            SELECT 1 FROM bbl_tex_signal cs
            WHERE cs.month = entries.month AND cs.arxiv_id = entries.arxiv_id AND cs.entry_key = entries.entry_key
        )
        """
    )
    print("  unverified_isolated rows with the key in the signal:", cur.rowcount)

    print("setting confirmed_cited where citation_status is excluded_sibling_inferred_uncited and the key is in the signal ...", flush=True)
    cur.execute(
        """
        UPDATE entries SET citation_status_bbl_tex = 'confirmed_cited', citation_status_bbl_tex_changed = '1'
        WHERE citation_status = 'excluded_sibling_inferred_uncited' AND EXISTS (
            SELECT 1 FROM bbl_tex_signal cs
            WHERE cs.month = entries.month AND cs.arxiv_id = entries.arxiv_id AND cs.entry_key = entries.entry_key
        )
        """
    )
    print("  excluded_sibling_inferred_uncited rows with the key in the signal:", cur.rowcount)

    print("setting excluded_comment_artifact_suspected where citation_status is confirmed_cited, the submission has a signal and the key is not in it ...", flush=True)
    cur.execute(
        """
        UPDATE entries SET citation_status_bbl_tex = 'excluded_comment_artifact_suspected', citation_status_bbl_tex_changed = '1'
        WHERE citation_status = 'confirmed_cited'
          AND EXISTS (
              SELECT 1 FROM has_bbl_tex_signal hs WHERE hs.month = entries.month AND hs.arxiv_id = entries.arxiv_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM bbl_tex_signal cs
              WHERE cs.month = entries.month AND cs.arxiv_id = entries.arxiv_id AND cs.entry_key = entries.entry_key
          )
        """
    )
    print("  excluded_comment_artifact_suspected rows:", cur.rowcount)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_citation_status_bbl_tex ON entries(citation_status_bbl_tex)")

    cur.execute("DROP VIEW IF EXISTS entries_confirmed_bbl_tex")
    cur.execute("CREATE VIEW entries_confirmed_bbl_tex AS SELECT * FROM entries WHERE citation_status_bbl_tex = 'confirmed_cited'")

    conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    print(f"\ntotal entries: {total}")
    print("\n-- citation_status --")
    for row in cur.execute("SELECT citation_status, COUNT(*) n FROM entries GROUP BY citation_status ORDER BY n DESC"):
        print(f"  {row['citation_status']}: {row['n']} ({100.0*row['n']/total:.3f}%)")
    print("\n-- citation_status_bbl_tex --")
    for row in cur.execute("SELECT citation_status_bbl_tex, COUNT(*) n FROM entries GROUP BY citation_status_bbl_tex ORDER BY n DESC"):
        print(f"  {row['citation_status_bbl_tex']}: {row['n']} ({100.0*row['n']/total:.3f}%)")

    n_confirmed_citation_log = cur.execute("SELECT COUNT(*) FROM entries_confirmed").fetchone()[0]
    n_confirmed_bbl_tex = cur.execute("SELECT COUNT(*) FROM entries_confirmed_bbl_tex").fetchone()[0]
    print(f"\nentries_confirmed:    {n_confirmed_citation_log}")
    print(f"entries_confirmed_bbl_tex: {n_confirmed_bbl_tex}")


if __name__ == "__main__":
    main()
