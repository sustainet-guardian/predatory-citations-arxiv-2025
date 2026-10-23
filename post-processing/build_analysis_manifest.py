#!/usr/bin/env python3
"""Writes EVAL_OUTPUT_DIR/analysis_manifest.csv, the input of build_entry_table.py: one row per .bib file
EVAL_BIB_ROOT/<YYMM>/<NNNNN>-<k>.bib (as written by pre-check/arxiv_bib_extractor.py) with the columns month, name
(<NNNNN>-<k>), category, entry_count, bib_size_bytes (file size) and result_file. A file is "assessed" when
EVAL_RESULTS_ROOT/<month>__<name>.jsonl exists; its entry_count is the number of non-blank lines of that file (one line per
entry the tool assessed) and result_file its name. Any other file has entry_count 0, no result_file and the category
"string_comment_preamble_only" (every @type in the file is string, comment or preamble), "empty_or_no_bibtex_syntax" (no @type at
all) or "unassessed_with_entries" (another @type; none is expected). The .bib and result files are only read; the manifest is
written directly into EVAL_OUTPUT_DIR because build_entry_table.py reads it by that path. --workers N sets the size of the process
pool (default: the number of CPUs of the machine, 4 if that is unknown).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
from pathlib import Path

from stats_common import required_path_env

FIELDNAMES = ["month", "name", "category", "entry_count", "bib_size_bytes", "result_file"]
ENTRY_TYPE_RE = re.compile(r"^@([a-zA-Z]+)", re.M)
NON_ENTRY_TYPES = {"string", "comment", "preamble"}


def classify_unassessed(bib_path: Path) -> str:
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    types = {t.lower() for t in ENTRY_TYPE_RE.findall(text)}
    if not types:
        return "empty_or_no_bibtex_syntax"
    if types - NON_ENTRY_TYPES:
        return "unassessed_with_entries"
    return "string_comment_preamble_only"


def count_result_lines(result_path: Path) -> int:
    with result_path.open("rb") as f:
        return sum(1 for line in f if line.strip())


def manifest_row(args: tuple[Path, Path]) -> dict:
    bib_path, results_root = args
    month = bib_path.parent.name
    name = bib_path.stem
    result_path = results_root / f"{month}__{name}.jsonl"
    if result_path.exists():
        category, entry_count, result_file = "assessed", count_result_lines(result_path), result_path.name
    else:
        category, entry_count, result_file = classify_unassessed(bib_path), 0, ""
    return {
        "month": month, "name": name, "category": category, "entry_count": entry_count,
        "bib_size_bytes": bib_path.stat().st_size, "result_file": result_file,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4, help="process pool size; defaults to the local CPU count")
    args = parser.parse_args()

    bib_root = required_path_env("EVAL_BIB_ROOT")
    results_root = required_path_env("EVAL_RESULTS_ROOT")
    out_path = required_path_env("EVAL_OUTPUT_DIR") / "analysis_manifest.csv"

    bib_paths = sorted(bib_root.glob("*/*.bib"))
    print(f"{len(bib_paths)} .bib files under {bib_root}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(manifest_row, ((p, results_root) for p in bib_paths), chunksize=64))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    for category, n in sorted(Counter(r["category"] for r in rows).items()):
        print(f"  {category}: {n}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
