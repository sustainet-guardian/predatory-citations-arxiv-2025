#!/usr/bin/env python3
"""Exhaustive leave-one-backend-out sweep over all has_conflict='1' confirmed-cited entries: every backend
that casts a found legitimate-list or heuristic vote on such an entry is removed in turn, the verdict
category is recomputed (leave_one_out_helpers.recompute_assessment), and flips against the stored verdict are
counted per backend. One process-pool task per results file; MAX_WORKERS is a worker count, os.cpu_count(). Reads
EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT and EVAL_OUTPUT_DIR. Writes leave_one_out_flip_rates.json and leave_one_out_flip_rates_per_backend.csv to
EVAL_OUTPUT_DIR/output_leave_one_out_flip_rates/ and numbers/leave_one_out_flip_rates.numbers.json (keys
looexhaustive_*: the population size, the flip percentage of four backends and the number of entries considered
for dblp_venues).
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import stats_common
from stats_common import pct, write_csv, write_json, write_numbers
from leave_one_out_helpers import get_conn, jsonl_path, recompute_assessment

stats_common.DB_PATH = stats_common.required_path_env("EVAL_DB_PATH_BBL_TEX")
stats_common.OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_leave_one_out_flip_rates"

MAX_WORKERS = os.cpu_count()
FLIP_PCT_BACKENDS = ("crossref_analyzer", "dblp_venues", "openalex_analyzer", "opencitations_analyzer")


def process_file(month: str, arxiv_id: str, suffix: str, wanted_keys: list[str]) -> dict:
    """Reads one jsonl file; for every wanted record and every backend with a found legitimate-list or
    heuristic vote on it, recomputes the assessment without that backend and checks for a flip."""
    path = jsonl_path(month, arxiv_id, suffix)
    if not path.exists():
        return {}
    wanted = set(wanted_keys)
    per_backend: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [n_considered, n_flipped]
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("entry_key") not in wanted:
                continue
            backend_results = rec.get("backend_results", [])
            stored = rec.get("final_assessment")
            found_backends = {
                r["backend_name"] for r in backend_results
                if r.get("status") == "found" and r.get("evidence_type") in ("legitimate_list", "heuristic")
            }
            for backend in found_backends:
                assessment_without_backend, _ = recompute_assessment(backend_results, exclude_backend=backend)
                counts = per_backend[backend]
                counts[0] += 1
                if assessment_without_backend != stored:
                    counts[1] += 1
    return dict(per_backend)


def main() -> None:
    conn = get_conn()
    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key FROM entries_confirmed WHERE has_conflict='1'"
    ).fetchall()
    print(f"population size (has_conflict='1'): {len(rows)}", flush=True)

    by_file: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for r in rows:
        by_file[(r["month"], r["arxiv_id"], r["suffix"])].append(r["entry_key"])
    targets = list(by_file.items())
    print(f"distinct files to read: {len(targets)}", flush=True)

    merged: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    n_done = 0
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_file, month, arxiv_id, suffix, keys): (month, arxiv_id, suffix)
            for (month, arxiv_id, suffix), keys in targets
        }
        for fut in as_completed(futures):
            per_backend = fut.result()
            for backend, (n_considered, n_flipped) in per_backend.items():
                merged[backend][0] += n_considered
                merged[backend][1] += n_flipped
            n_done += 1
            if n_done % 5000 == 0 or n_done == len(targets):
                elapsed = time.monotonic() - start
                print(f"  {n_done}/{len(targets)} files processed ({elapsed:.0f}s elapsed)", flush=True)

    per_backend_out = [
        {
            "backend": backend,
            "n_considered": counts[0],
            "n_flipped": counts[1],
            "flip_pct_of_considered": pct(counts[1], counts[0]),
        }
        for backend, counts in merged.items()
    ]
    per_backend_out.sort(key=lambda d: d["n_flipped"], reverse=True)

    out = {
        "method": "All has_conflict='1' confirmed-cited entries: every backend with a legitimate-list or "
                   "heuristic vote on an entry is left out in turn and the assessment recomputed.",
        "population_size_has_conflict": len(rows),
        "n_distinct_files": len(targets),
        "per_backend": per_backend_out,
    }
    write_json("leave_one_out_flip_rates", out)
    write_csv("leave_one_out_flip_rates_per_backend", per_backend_out, list(per_backend_out[0].keys()))
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "looexhaustive_population_size": {"kind": "int", "value": len(rows)},
    }
    for d in per_backend_out:
        backend = d["backend"]
        if backend in FLIP_PCT_BACKENDS:
            numbers_payload[f"looexhaustive_{backend}_flip_pct"] = {
                "kind": "ratio", "num": d["n_flipped"], "den": d["n_considered"], "decimals": 1,
            }
        if backend == "dblp_venues":
            numbers_payload["looexhaustive_dblp_venues_n_considered"] = {"kind": "int", "value": d["n_considered"]}
    write_numbers("leave_one_out_flip_rates", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
