#!/usr/bin/env python3
"""Checks the ISSN of every legitimate citation whose venue name occurs only in the name lists of the CORE backends
(core_journals, core_conferences), which never record an ISSN, and not in those of the ISSN-capable legitimacy backends.
A candidate is such a citation where a CORE backend voted legitimate and no ISSN-capable backend did; it is a confirmed
misattribution if the cached ISSN of the CORE record differs from the citing ISSN, or is empty while the citing ISSN is on
another cache row. The population is the confirmed-cited entries (entries_confirmed_bbl_tex) with the stored
final_assessment 'legitimate'. Reads EVAL_DB_PATH_BBL_TEX, EVAL_CACHE_DB_PATH and the per-run JSONL under
EVAL_RESULTS_ROOT (one process task per file, MAX_WORKERS = os.cpu_count() worker processes). Writes counts only:
EVAL_OUTPUT_DIR/output_core_name_legitimate_issn_check/core_name_legitimate_issn_check.json and
numbers/core_name_legitimate_issn_check.numbers.json (the candidates, the checkable candidates, the share without a citing
ISSN and the share of confirmed misattributions among the checkable ones)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import stats_common
from identity_vote_rules import (
    build_any_row_has_issn_index, format_issn, matched_cache_issn, normalize_issn,
)
from stats_common import pct, required_path_env, write_json, write_numbers

DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_core_name_legitimate_issn_check"
RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")
CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")

stats_common.DB_PATH = DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR

# legitimacy backends whose cached record carries no ISSN, and those whose record can carry one
NEVER_ISSN_LEGITIMACY_BACKENDS = {"core_journals", "core_conferences"}
ISSN_CAPABLE_LEGITIMACY_BACKENDS = {
    "scopus", "dblp_venues", "pubmed_nlm", "doaj",
    "ugc_care_included_from_clone_group1", "ugc_care_included_from_clone_group2",
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed AS SELECT * FROM entries_confirmed_bbl_tex")
    return conn


def build_core_exclusive_names(cache_conn: sqlite3.Connection) -> set[str]:
    cur = cache_conn.cursor()

    def names_for(sources: tuple[str, ...]) -> set[str]:
        placeholders = ",".join("?" * len(sources))
        cur.execute(
            f"""
            SELECT DISTINCT LOWER(jn.name) n FROM journal_names jn
            JOIN source_assessments sa ON sa.journal_id = jn.journal_id
            JOIN data_sources ds ON ds.id = sa.source_id
            WHERE ds.name IN ({placeholders})
            """,
            sources,
        )
        return {r["n"] for r in cur.fetchall()}

    core_names = names_for(tuple(NEVER_ISSN_LEGITIMACY_BACKENDS))
    other_names = names_for(tuple(ISSN_CAPABLE_LEGITIMACY_BACKENDS))
    return core_names - other_names


def jsonl_path(month: str, arxiv_id: str, suffix: str) -> Path:
    return RESULTS_ROOT / f"{month}__{arxiv_id}-{suffix}.jsonl"


MAX_WORKERS = os.cpu_count()

_any_row_issn: set[str] | None = None


def _init_worker(any_row_issn: set[str]) -> None:
    global _any_row_issn
    _any_row_issn = any_row_issn


def process_file(month: str, arxiv_id: str, suffix: str, wanted: list[dict]) -> list[dict]:
    """`wanted`: {"entry_key", "issn", "venue"} of this file's CORE-exclusive-name legitimate citations. Returns a verdict
    per candidate (a CORE backend voted legitimate, no ISSN-capable backend did)."""
    path = jsonl_path(month, arxiv_id, suffix)
    if not path.exists():
        return []
    wanted_by_key = {w["entry_key"]: w for w in wanted}
    verdicts = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = rec.get("entry_key")
            w = wanted_by_key.get(key)
            if w is None:
                continue
            found_legit = {
                b["backend_name"] for b in rec.get("backend_results", [])
                if b.get("status") == "found" and b.get("assessment") == "legitimate"
            }
            core_fired = found_legit & NEVER_ISSN_LEGITIMACY_BACKENDS
            other_issn_capable_fired = found_legit & ISSN_CAPABLE_LEGITIMACY_BACKENDS
            if not core_fired or other_issn_capable_fired:
                continue  # not a candidate

            citing_issn = normalize_issn(w["issn"])
            if citing_issn is None:
                verdicts.append({"venue": w["venue"], "verdict": "unverifiable_no_citing_issn"})
                continue

            any_mismatch = False
            for backend in sorted(core_fired):
                cache_issn = normalize_issn(matched_cache_issn(rec, backend))
                if cache_issn == citing_issn:
                    continue
                elif cache_issn is not None:
                    any_mismatch = True
                else:
                    if citing_issn in _any_row_issn:
                        any_mismatch = True
            verdict = "confirmed_misattribution" if any_mismatch else "confirmed_correct"
            verdicts.append({
                "venue": w["venue"], "citing_issn": format_issn(citing_issn), "verdict": verdict,
            })
    return verdicts


def main() -> None:
    conn = get_conn()
    cache_conn = sqlite3.connect(str(CACHE_DB_PATH))
    cache_conn.row_factory = sqlite3.Row

    core_exclusive_names = build_core_exclusive_names(cache_conn)
    print(f"CORE-exclusive names: {len(core_exclusive_names)}", flush=True)

    placeholders = ",".join("?" * len(core_exclusive_names))
    rows = conn.execute(
        f"""
        SELECT month, arxiv_id, suffix, entry_key, issn,
               COALESCE(matched_venue_name, venue_raw) AS venue
        FROM entries_confirmed
        WHERE final_assessment='legitimate'
          AND LOWER(COALESCE(matched_venue_name, venue_raw)) IN ({placeholders})
        """,
        sorted(core_exclusive_names),
    ).fetchall()
    n_population = len(rows)
    print(f"CORE-exclusive-name legitimate citations (population): {n_population}", flush=True)

    any_row_issn = build_any_row_has_issn_index(cache_conn)

    by_file: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_file[(r["month"], r["arxiv_id"], r["suffix"])].append(
            {"entry_key": r["entry_key"], "issn": r["issn"], "venue": r["venue"]}
        )
    targets = list(by_file.items())
    print(f"distinct files to read: {len(targets)}", flush=True)

    all_verdicts: list[dict] = []
    n_done = 0
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=MAX_WORKERS, initializer=_init_worker, initargs=(any_row_issn,)) as pool:
        futures = {
            pool.submit(process_file, month, arxiv_id, suffix, wanted): (month, arxiv_id, suffix)
            for (month, arxiv_id, suffix), wanted in targets
        }
        for fut in as_completed(futures):
            all_verdicts.extend(fut.result())
            n_done += 1
            if n_done % 5000 == 0 or n_done == len(targets):
                elapsed = time.monotonic() - start
                print(f"  {n_done}/{len(targets)} files processed ({elapsed:.0f}s elapsed)", flush=True)

    n_candidates = len(all_verdicts)
    dist = Counter(v["verdict"] for v in all_verdicts)
    n_unverifiable = dist.get("unverifiable_no_citing_issn", 0)
    n_checkable = n_candidates - n_unverifiable
    n_confirmed_misattribution = dist.get("confirmed_misattribution", 0)
    n_confirmed_correct = dist.get("confirmed_correct", 0)

    out = {
        "method": "Every CORE-exclusive-name legitimate citation is checked: a candidate (a CORE "
                   "backend voted legitimate, no ISSN-capable backend did) is a confirmed misattribution "
                   "if the cached ISSN of its CORE record differs from the citing ISSN, or is empty "
                   "while the citing ISSN is on another cache row.",
        "n_population": n_population,
        "n_candidates": n_candidates,
        "n_unverifiable_no_citing_issn": n_unverifiable,
        "pct_no_citing_issn": pct(n_unverifiable, n_candidates),
        "n_checkable": n_checkable,
        "n_confirmed_misattribution": n_confirmed_misattribution,
        "pct_confirmed_misattribution_of_checkable": pct(n_confirmed_misattribution, n_checkable),
        "n_confirmed_correct": n_confirmed_correct,
    }
    write_json("core_name_legitimate_issn_check", out)
    print(json.dumps(out, indent=2, default=str))

    numbers_payload = {
        "mirrorexhaustive_n_true_candidates": {"kind": "int", "value": n_candidates},
        "mirrorexhaustive_pct_no_citing_issn": {"kind": "ratio", "num": n_unverifiable, "den": n_candidates, "decimals": 2},
        "mirrorexhaustive_n_checkable": {"kind": "int", "value": n_checkable},
        "mirrorexhaustive_pct_confirmed_misattribution": {
            "kind": "ratio", "num": n_confirmed_misattribution, "den": n_checkable, "decimals": 1,
        },
    }
    write_numbers("core_name_legitimate_issn_check", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
