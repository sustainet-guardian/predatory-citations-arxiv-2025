#!/usr/bin/env python3
"""Counts the 2025 submissions with and without entries in the corpus database and classifies the excluded ones by cause
(PDF only, single gzip file, no .tex, no .bib, .bib present but no assessed entries), with the .bbl/.tex signal available
per cause. Also counts multi-.bib submissions. Reads EVAL_DB_PATH_BBL_TEX, EVAL_BBL_JSONL_PATH and
EVAL_SNAPSHOT_CATEGORIES_DB, the snapshot category database of every 2025 submission of the arXiv metadata snapshot (made by
build_snapshot_categories_db.py); its row count is the total number of submissions, and it is used instead of the API database
EVAL_ARXIV_CATEGORIES_DB (fetch_arxiv_categories.py, confirmed-cited submissions only) because the excluded submissions have no
confirmed-cited entries. The records of EVAL_BBL_JSONL_PATH are a different population and give the cause breakdown only; keys
of the JSON ending in snapshot_categories_db count rows of the snapshot database, keys ending in bbl_tex_extraction count those
records. Writes excluded_submissions_breakdown.json directly into EVAL_OUTPUT_DIR/output_analyze_excluded_submissions/ and
numbers/analyze_excluded_submissions.numbers.json (keys corpus_*; corpus_total_2025_submissions is the row count of the
snapshot database; corpus_discipline_resolved_pct is the share of the submissions with confirmed-cited entries, view
entries_confirmed_bbl_tex, that have a found primary category in the snapshot database).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import stats_common
from stats_common import get_conn, pct, required_path_env, submission_of, write_json, write_numbers
from disciplinary_classification import snapshot_categories_db

JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
stats_common.DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_analyze_excluded_submissions"


def multi_bib_and_submission_coverage(conn) -> dict:
    rows = conn.execute("SELECT month, name FROM files WHERE category='assessed'").fetchall()
    per_sub_files: dict[tuple, int] = defaultdict(int)
    for r in rows:
        arxiv_id = submission_of(r["name"])
        per_sub_files[(r["month"], arxiv_id)] += 1
    n_subs = len(per_sub_files)
    n_multi_bib = sum(1 for v in per_sub_files.values() if v > 1)
    return {
        "n_submissions": n_subs,
        "n_multi_bib_submissions": n_multi_bib,
        "pct_multi_bib": pct(n_multi_bib, n_subs),
        # Files (not submissions) that belong to a multi-.bib submission, out of all assessed files.
        "n_assessed_files": len(rows),
        "n_files_in_multi_bib_submissions": sum(v for v in per_sub_files.values() if v > 1),
    }


def main() -> None:
    conn = get_conn()
    in_scope = {
        (r["month"], r["arxiv_id"])
        for r in conn.execute("SELECT DISTINCT month, arxiv_id FROM entries").fetchall()
    }
    print(f"in-scope submissions (have entries in the database): {len(in_scope)}")

    excluded_records = []
    n_total = 0
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            n_total += 1
            key = (rec["month"], rec["arxiv_id"])
            if key not in in_scope:
                excluded_records.append(rec)

    print(f"total submissions in the .bbl/.tex extraction: {n_total}")
    print(f"excluded (no entries in the database): {len(excluded_records)}")

    categories_conn = sqlite3.connect(str(snapshot_categories_db()))
    n_total_snapshot = categories_conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    n_excluded_snapshot = n_total_snapshot - len(in_scope)
    print(f"total submissions (EVAL_SNAPSHOT_CATEGORIES_DB): {n_total_snapshot}", flush=True)
    print(f"excluded: {n_excluded_snapshot}", flush=True)
    print(f"submissions in EVAL_SNAPSHOT_CATEGORIES_DB but not in the .bbl/.tex extraction: {n_total_snapshot - n_total}", flush=True)

    # Submissions with confirmed-cited entries (the population of the disciplinary analyses) whose primary category the snapshot
    # database resolves.
    confirmed_cited = {
        (r["month"], r["arxiv_id"])
        for r in conn.execute("SELECT DISTINCT month, arxiv_id FROM entries_confirmed_bbl_tex").fetchall()
    }
    resolved = set(categories_conn.execute(
        "SELECT month, arxiv_id FROM categories WHERE found=1 AND primary_category IS NOT NULL AND primary_category != ''"
    ).fetchall())
    n_confirmed_cited_resolved = len(confirmed_cited & resolved)
    print(f"submissions with confirmed-cited entries: {len(confirmed_cited)}, with a resolved category: {n_confirmed_cited_resolved}",
          flush=True)

    cause_counts: Counter = Counter()
    signal_by_cause: dict[str, Counter] = {}

    for rec in excluded_records:
        ft = rec["file_type"]
        n_bib_files = len(rec.get("bib_files") or [])
        n_tex_files = rec.get("n_tex_files", 0)

        if ft == "pdf_only":
            cause = "pdf_only_no_source"
        elif ft == "gzip_single_file":
            cause = "gzip_single_file"
        elif n_tex_files == 0:
            cause = "tar_gz_no_tex_files_found"
        elif n_bib_files == 0:
            cause = "tar_gz_tex_present_no_bib_file_at_all"
        else:
            # The archive has .tex and .bib files, but the submission has no assessed entries in the database.
            cause = "tar_gz_has_bib_file_but_no_assessed_entries"

        cause_counts[cause] += 1
        bucket = signal_by_cause.setdefault(cause, Counter())
        if rec.get("bbl_present"):
            bucket["bbl_present"] += 1
        elif rec.get("tex_cite_keys"):
            bucket["tex_only"] += 1
        else:
            bucket["neither"] += 1

    out = {
        "n_in_scope": len(in_scope),
        "n_confirmed_cited_submissions": len(confirmed_cited),
        "n_confirmed_cited_submissions_with_category": n_confirmed_cited_resolved,
        "n_total_2025_submissions_snapshot_categories_db": n_total_snapshot,
        "n_excluded_snapshot_categories_db": n_excluded_snapshot,
        "pct_excluded_snapshot_categories_db": pct(n_excluded_snapshot, n_total_snapshot),
        "n_total_2025_submissions_bbl_tex_extraction": n_total,
        "n_excluded_bbl_tex_extraction": len(excluded_records),
        "pct_excluded_bbl_tex_extraction": pct(len(excluded_records), n_total),
        "cause_breakdown": [],
    }
    for cause, n in cause_counts.most_common():
        sig = signal_by_cause[cause]
        out["cause_breakdown"].append({
            "cause": cause, "n": n, "pct_of_excluded": pct(n, len(excluded_records)),
            "n_bbl_present": sig.get("bbl_present", 0),
            "n_tex_only": sig.get("tex_only", 0),
            "n_neither": sig.get("neither", 0),
            "pct_any_signal": pct(sig.get("bbl_present", 0) + sig.get("tex_only", 0), n),
        })

    write_json("excluded_submissions_breakdown", out)
    print(json.dumps(out, indent=2, default=str))

    multi_bib = multi_bib_and_submission_coverage(conn)
    print(json.dumps(multi_bib, indent=2), flush=True)

    numbers_payload = {
        "corpus_total_2025_submissions": {"kind": "int", "value": n_total_snapshot},
        "corpus_covered_submissions": {"kind": "int", "value": len(in_scope)},
        "corpus_excluded_submissions": {"kind": "int", "value": n_excluded_snapshot},
        "corpus_excluded_submissions_pct": {
            "kind": "ratio", "num": n_excluded_snapshot, "den": n_total_snapshot, "decimals": 1,
        },
        "corpus_covered_submissions_pct": {
            "kind": "ratio", "num": len(in_scope), "den": n_total_snapshot, "decimals": 2,
        },
        "corpus_discipline_resolved_pct": {
            "kind": "ratio", "num": n_confirmed_cited_resolved, "den": len(confirmed_cited), "decimals": 1,
        },
        "corpus_multi_bib_submissions": {"kind": "int", "value": multi_bib["n_multi_bib_submissions"]},
        "corpus_multi_bib_pct": {
            "kind": "ratio", "num": multi_bib["n_multi_bib_submissions"], "den": multi_bib["n_submissions"], "decimals": 2,
        },
        "corpus_multi_bib_files_n": {"kind": "int", "value": multi_bib["n_files_in_multi_bib_submissions"]},
        "corpus_multi_bib_files_pct": {
            "kind": "ratio", "num": multi_bib["n_files_in_multi_bib_submissions"],
            "den": multi_bib["n_assessed_files"], "decimals": 1,
        },
    }
    write_numbers("analyze_excluded_submissions", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
