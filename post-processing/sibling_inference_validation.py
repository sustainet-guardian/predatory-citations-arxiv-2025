#!/usr/bin/env python3
"""Overlap check for every file with citation_status_bbl_tex 'excluded_sibling_inferred_uncited': the file's .bib entry keys are
compared with the pooled cited keys (-cited.csv) of the siblings with a citation log in the same submission; the overlap is what a
submission-level pooled-key match would additionally credit as cited. Reads EVAL_DB_PATH_BBL_TEX and EVAL_BIB_ROOT (importing
build_entry_table.py for load_bib_entries and load_cited_csv also needs EVAL_DB_PATH, EVAL_RESULTS_ROOT and EVAL_OUTPUT_DIR to be
set). One process-pool task per file; MAX_WORKERS is a worker count, os.cpu_count(). Writes sibling_inference_validation.json
(counts only) to EVAL_OUTPUT_DIR/output_sibling_inference_validation/ and numbers/sibling_inference_validation.numbers.json (key
siblingexhaustive_n_files_checked, the number of files checked).
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

import stats_common
from build_entry_table import load_bib_entries, load_cited_csv
from stats_common import get_conn, pct, required_path_env, write_json, write_numbers

stats_common.DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_sibling_inference_validation"

BIB_ROOT = required_path_env("EVAL_BIB_ROOT")
MAX_WORKERS = os.cpu_count()


def process_file(month: str, arxiv_id: str, suffix: str, sibling_suffixes: list[str]) -> dict:
    """Compares one excluded file's entry keys with the pooled cited keys of its sibling files (filesystem reads only)."""
    excluded_bib = BIB_ROOT / month / f"{arxiv_id}-{suffix}.bib"
    excluded_keys = set(load_bib_entries(excluded_bib))

    pooled_sibling_cited: set[str] = set()
    for sib_suffix in sibling_suffixes:
        pooled_sibling_cited |= set(load_cited_csv(BIB_ROOT / month / f"{arxiv_id}-{sib_suffix}-cited.csv"))

    return {
        "n_excluded_keys": len(excluded_keys),
        "n_overlap_with_sibling_cited_keys": len(excluded_keys & pooled_sibling_cited),
    }


def main() -> None:
    conn = get_conn()

    excluded_files = conn.execute(
        "SELECT DISTINCT month, arxiv_id, suffix FROM entries WHERE citation_status_bbl_tex = 'excluded_sibling_inferred_uncited'"
    ).fetchall()
    assert excluded_files, "no entries with citation_status_bbl_tex = 'excluded_sibling_inferred_uncited'"

    sibling_map: dict[tuple[str, str], list[str]] = {}
    for r in conn.execute("SELECT month, name FROM files WHERE has_cited_sidecar=1").fetchall():
        month = r["month"]
        arxiv_id, suffix = r["name"].rsplit("-", 1)
        sibling_map.setdefault((month, arxiv_id), []).append(suffix)

    targets = [(r["month"], r["arxiv_id"], r["suffix"]) for r in excluded_files]
    print(f"files to check: {len(targets)}", flush=True)

    file_results = []
    n_done = 0
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_file, month, arxiv_id, suffix, sibling_map.get((month, arxiv_id), [])):
                (month, arxiv_id, suffix)
            for month, arxiv_id, suffix in targets
        }
        for fut in as_completed(futures):
            file_results.append(fut.result())
            n_done += 1
            if n_done % 1000 == 0 or n_done == len(targets):
                print(f"  {n_done}/{len(targets)} files processed ...", flush=True)

    total_excluded_keys = sum(r["n_excluded_keys"] for r in file_results)
    total_overlap = sum(r["n_overlap_with_sibling_cited_keys"] for r in file_results)
    files_with_any_overlap = sum(1 for r in file_results if r["n_overlap_with_sibling_cited_keys"] > 0)

    out = {
        "n_files_checked": len(file_results),
        "n_files_with_any_overlap": files_with_any_overlap,
        "pct_files_with_any_overlap": pct(files_with_any_overlap, len(file_results)),
        "total_excluded_keys_checked": total_excluded_keys,
        "total_overlap_keys": total_overlap,
        "overall_overlap_pct": pct(total_overlap, total_excluded_keys),
    }
    write_json("sibling_inference_validation", out)
    print(json.dumps(out, indent=2))

    numbers_payload = {
        "siblingexhaustive_n_files_checked": {"kind": "int", "value": len(file_results)},
    }
    write_numbers("sibling_inference_validation", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
