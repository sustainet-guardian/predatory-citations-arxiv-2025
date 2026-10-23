#!/usr/bin/env python3
"""Helper module for the leave-one-out scripts; it writes no output and no numbers file.
Reads EVAL_RESULTS_ROOT (the stored backend results, *.jsonl) and EVAL_DB_PATH_BBL_TEX (sets stats_common.DB_PATH
on import).

Used by other scripts: get_conn, jsonl_path, load_records, recompute_assessment (the tool's weighted-vote category rule of
decision_rule.py with one backend left out), validate_reimplementation (counts of recomputed verdicts that equal / differ
from the stored one).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import stats_common
from decision_rule import weighted_vote_assessment
from stats_common import pct, required_path_env

RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")

stats_common.DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")


def get_conn():
    """stats_common.get_conn(); when the database has entries_confirmed_bbl_tex, entries_confirmed is a temp
    view onto it."""
    conn = stats_common.get_conn()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    if "entries_confirmed_bbl_tex" in names:
        conn.execute("CREATE TEMP VIEW entries_confirmed AS SELECT * FROM entries_confirmed_bbl_tex")
    return conn


def jsonl_path(month: str, arxiv_id: str, suffix: str) -> Path:
    return RESULTS_ROOT / f"{month}__{arxiv_id}-{suffix}.jsonl"


def load_records(targets: list[tuple[str, str, str, str]]) -> dict[tuple, dict]:
    """targets: list of (month, arxiv_id, suffix, entry_key). Returns {key: record}, reading each file once."""
    by_file: dict[Path, list[tuple[str, str, str, str]]] = defaultdict(list)
    for month, arxiv_id, suffix, entry_key in targets:
        by_file[jsonl_path(month, arxiv_id, suffix)].append((month, arxiv_id, suffix, entry_key))

    out: dict[tuple, dict] = {}
    n_files = len(by_file)
    for i, (path, keys) in enumerate(by_file.items()):
        if n_files > 1000 and i % 1000 == 0:
            print(f"  [{datetime.now().isoformat(timespec='seconds')}] load_records: {i}/{n_files} files ...", flush=True)
        wanted = {k[3] for k in keys}
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("entry_key") in wanted:
                    for k in keys:
                        if k[3] == rec.get("entry_key"):
                            out[k] = rec
    return out


def recompute_assessment(backend_results: list[dict], exclude_backend: str | None) -> tuple[str, float]:
    """The tool's weighted-vote category rule (decision_rule.py) over the backend results with status "found", leaving out
    exclude_backend. Returns (assessment, confidence); the confidence has no agreement bonus."""
    found = [r for r in backend_results if r.get("status") == "found" and r.get("backend_name") != exclude_backend]
    return weighted_vote_assessment(found)


def validate_reimplementation(records: dict[tuple, dict]) -> dict:
    """Recomputes each record with no exclusion and compares it with the stored final_assessment; returns counts."""
    match, mismatch = 0, 0
    for rec in records.values():
        recomputed, _ = recompute_assessment(rec.get("backend_results", []), exclude_backend=None)
        stored = rec.get("final_assessment")
        if recomputed == stored:
            match += 1
        else:
            mismatch += 1
    return {"match": match, "mismatch": mismatch, "pct_match": pct(match, match + mismatch)}
