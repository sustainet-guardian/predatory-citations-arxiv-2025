#!/usr/bin/env python3
"""Recall and precision of the citation logs (-cited.csv) against the .bbl/.tex citation signal, over all assessed files that
have a citation log.

Per file: relevant = .bib entry keys that are in the submission's pooled bbl_keys | tex_cite_keys; missed = relevant minus the
citation-log keys. Recall = 1 - |missed| / |relevant|; precision = share of the citation-log keys that the .bbl/.tex signal also
confirms. Files whose submission has no .bbl/.tex signal are skipped. Reads EVAL_DB_PATH (files table), EVAL_BIB_ROOT (the
citation logs) and EVAL_BBL_JSONL_PATH. Writes bbl_recall_full_corpus.json into EVAL_OUTPUT_DIR/output_stats_bbl_recall_full_corpus/
and numbers/stats_bbl_recall_full_corpus.numbers.json (keys bblaccuracy_*).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import stats_common
from stats_common import get_conn, pct, required_path_env, submission_of, write_json, write_numbers

BIB_ROOT = required_path_env("EVAL_BIB_ROOT")
JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_stats_bbl_recall_full_corpus"


def load_cited_csv(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("citation_key")
            if key is not None:
                out.add(key)
    return out


def load_extraction_index() -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            key = (rec["month"], rec["arxiv_id"])
            signal_keys = frozenset(rec.get("bbl_keys") or []) | frozenset(rec.get("tex_cite_keys") or [])
            by_suffix = {bf["suffix"]: frozenset(bf["raw_keys"]) for bf in rec.get("bib_files") or []}
            index[key] = {"signal_keys": signal_keys, "by_suffix": by_suffix}
    return index


def main() -> None:
    print("loading extraction index ...", flush=True)
    index = load_extraction_index()
    print(f"  {len(index)} submissions loaded", flush=True)

    conn = get_conn()
    files = conn.execute(
        "SELECT month, name FROM files WHERE has_cited_sidecar=1 AND category='assessed'"
    ).fetchall()
    print(f"files with a citation log to check: {len(files)}", flush=True)

    total_relevant = 0
    total_missed = 0
    total_log_keys = 0
    total_log_keys_confirmed = 0
    n_files_with_any_miss = 0
    n_without_signal = 0

    for n_processed, r in enumerate(files, start=1):
        arxiv_id = submission_of(r["name"])
        suffix = int(r["name"][len(arxiv_id) + 1:])
        rec = index.get((r["month"], arxiv_id))
        if rec is None or suffix not in rec["by_suffix"] or not rec["signal_keys"]:
            n_without_signal += 1
            continue

        relevant = rec["by_suffix"][suffix] & rec["signal_keys"]

        # Precision counts the log keys of every file with a usable index entry, including files with no relevant key.
        log_keys = load_cited_csv(BIB_ROOT / r["month"] / f"{r['name']}-cited.csv")
        total_log_keys += len(log_keys)
        total_log_keys_confirmed += len(log_keys & rec["signal_keys"])

        if not relevant:
            continue

        missed = relevant - log_keys
        total_relevant += len(relevant)
        total_missed += len(missed)
        if missed:
            n_files_with_any_miss += 1
        if n_processed % 20000 == 0:
            print(f"  processed {n_processed}/{len(files)} ...", flush=True)

    out = {
        "n_files_checked": len(files),
        "n_files_without_signal": n_without_signal,
        "n_files_with_signal": len(files) - n_without_signal,
        "total_relevant_keys": total_relevant,
        "total_missed_by_citation_log": total_missed,
        "overall_recall_pct": pct(total_relevant - total_missed, total_relevant) if total_relevant else None,
        "total_citation_log_keys": total_log_keys,
        "total_citation_log_keys_confirmed_by_signal": total_log_keys_confirmed,
        "overall_precision_pct": pct(total_log_keys_confirmed, total_log_keys) if total_log_keys else None,
        "n_files_with_any_miss": n_files_with_any_miss,
    }

    write_json("bbl_recall_full_corpus", out)
    write_numbers("stats_bbl_recall_full_corpus", {
        "bblaccuracy_n_files_checked": {"kind": "int", "value": len(files)},
        "bblaccuracy_recall_num": {"kind": "int", "value": total_relevant - total_missed},
        "bblaccuracy_recall_den": {"kind": "int", "value": total_relevant},
        "bblaccuracy_recall_pct": {
            "kind": "ratio", "num": total_relevant - total_missed, "den": total_relevant, "decimals": 3,
        },
        "bblaccuracy_recall_pct_short": {
            "kind": "ratio", "num": total_relevant - total_missed, "den": total_relevant, "decimals": 1,
        },
        "bblaccuracy_precision_pct_short": {
            "kind": "ratio", "num": total_log_keys_confirmed, "den": total_log_keys, "decimals": 1,
        },
        "bblaccuracy_precision_num": {"kind": "int", "value": total_log_keys_confirmed},
        "bblaccuracy_precision_den": {"kind": "int", "value": total_log_keys},
        "bblaccuracy_precision_pct": {
            "kind": "ratio", "num": total_log_keys_confirmed, "den": total_log_keys, "decimals": 3,
        },
    })
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
