#!/usr/bin/env python3
"""Writes cited_entry_counts.json, the per-file recount of the cited entries of the files that have a citation log, from the
compiled-bibliography and TeX signal.

For every file with a citation log (files.has_cited_sidecar=1) the cited count is |bib keys (EVAL_BBL_JSONL_PATH) intersect
(bbl_keys | tex_cite_keys)|; files without usable signal keep files.cited_entry_count (EVAL_DB_PATH). The file holds one key,
recounted_cited_sum, the sum over the files; it is written directly into EVAL_OUTPUT_DIR (no output_<script> subdirectory) and
read by path by corpus_size_partition.py. No numbers file.
"""
from __future__ import annotations

import json
import sqlite3

from stats_common import required_path_env, submission_of

DB_PATH = required_path_env("EVAL_DB_PATH")
JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR")


def load_index() -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            key = (rec["month"], rec["arxiv_id"])
            signal_keys = frozenset(rec.get("bbl_keys") or []) | frozenset(rec.get("tex_cite_keys") or [])
            has_signal = rec["file_type"] == "tar_gz" and bool(signal_keys)
            by_suffix = {bf["suffix"]: frozenset(bf["raw_keys"]) for bf in rec.get("bib_files") or []}
            index[key] = {"signal_keys": signal_keys, "has_signal": has_signal, "by_suffix": by_suffix}
    return index


def main() -> None:
    print("loading extraction index ...", flush=True)
    index = load_index()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT month, name, cited_entry_count FROM files WHERE has_cited_sidecar=1"
    ).fetchall()
    print(f"{len(rows)} files with a citation log", flush=True)

    recounted_cited_sum = 0

    for r in rows:
        arxiv_id = submission_of(r["name"])
        suffix = int(r["name"][len(arxiv_id) + 1:])
        rec = index.get((r["month"], arxiv_id))

        if rec is None or not rec["has_signal"] or suffix not in rec["by_suffix"]:
            recounted_cited_sum += r["cited_entry_count"]  # no signal: the file's cited_entry_count is kept
            continue

        recounted_cited_sum += len(rec["by_suffix"][suffix] & rec["signal_keys"])

    out = {"recounted_cited_sum": recounted_cited_sum}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "cited_entry_counts.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"wrote {path}")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
