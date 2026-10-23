#!/usr/bin/env python3
"""File-level statistics of the .bib corpus: file counts by category, .bib size and entries-per-file distributions, large files
(entry_count >= LARGE_FILE_ENTRIES), citation-log coverage, and the entry-type and state breakdown of the confirmed-cited entries.

Reads EVAL_DB_PATH_BBL_TEX (tables files and entries; confirmed-cited means citation_status_bbl_tex='confirmed_cited'). Writes
numbers/stats_corpus.numbers.json (keys corpus_*, corpusstate_*) and corpus_file_statistics.json (EVAL_OUTPUT_DIR/output_stats_corpus/).
The key corpus_max_entries_per_submission holds the maximum number of entries of one .bib file (the largest bibliography), not of
one submission. The keys corpus_mega_raw_entries and corpus_mega_raw_entries_pct hold the entries of the large files (at least
LARGE_FILE_ENTRIES entries) and their share of the entries of all assessed files. The shares of the entry-type and state breakdowns
are shares of the confirmed-cited entries.
"""
from __future__ import annotations

import json

import stats_common
from stats_common import LARGE_FILE_ENTRIES, get_conn, pct, percentiles, required_path_env, submission_of, write_json, write_numbers

stats_common.DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_stats_corpus"


def main() -> None:
    conn = get_conn()
    out: dict = {}

    # file counts by category
    rows = conn.execute("SELECT category, COUNT(*) n FROM files GROUP BY category").fetchall()
    by_cat = {r["category"]: r["n"] for r in rows}
    total_files = sum(by_cat.values())
    out["file_counts"] = {
        "total": total_files,
        "assessed": by_cat.get("assessed", 0),
        "non_bibliography_string_only": by_cat.get("string_comment_preamble_only", 0),
        "non_bibliography_empty": by_cat.get("empty_or_no_bibtex_syntax", 0),
        "pct_assessed": pct(by_cat.get("assessed", 0), total_files),
    }

    # .bib file size distribution (assessed files only)
    sizes = [r["bib_size_bytes"] for r in conn.execute(
        "SELECT bib_size_bytes FROM files WHERE category='assessed'"
    )]
    out["bib_size_bytes"] = {
        "n": len(sizes), "min": min(sizes), "max": max(sizes),
        "mean": round(sum(sizes) / len(sizes), 1),
        "percentiles": percentiles(sizes),
    }

    # entries-per-file distribution and large files
    frows = conn.execute(
        "SELECT month, name, entry_count, has_cited_sidecar "
        "FROM files WHERE category='assessed'"
    ).fetchall()
    entry_counts = [r["entry_count"] for r in frows]
    out["entries_per_file"] = {
        "n": len(entry_counts), "min": min(entry_counts), "max": max(entry_counts),
        "mean": round(sum(entry_counts) / len(entry_counts), 1),
        "total_entries": sum(entry_counts),
        "percentiles": percentiles(entry_counts),
    }

    large_file_rows = [r for r in frows if r["entry_count"] >= LARGE_FILE_ENTRIES]
    out["large_files"] = {
        "large_file_count": len(large_file_rows),
        "large_file_pct_of_files": pct(len(large_file_rows), len(frows)),
        "large_file_entries_sum": sum(r["entry_count"] for r in large_file_rows),
        "large_file_pct_of_entries": pct(sum(r["entry_count"] for r in large_file_rows), sum(entry_counts)),
    }

    # citation-log (-cited.csv) coverage
    n_with_log = sum(1 for r in frows if r["has_cited_sidecar"])
    out["citation_log_coverage"] = {
        "files_with_citation_log": n_with_log,
        "files_without_citation_log": len(frows) - n_with_log,
        "pct_with_citation_log": pct(n_with_log, len(frows)),
    }

    # Sibling structure of the no-citation-log files: a file has a sibling when its submission
    # (month + arXiv id, the file name minus its -N) contains at least one file that has a citation log.
    sub_of = lambda r: (r["month"], submission_of(r["name"]))  # noqa: E731
    submissions_with_log = {sub_of(r) for r in frows if r["has_cited_sidecar"]}
    nolog = [r for r in frows if not r["has_cited_sidecar"]]
    n_nolog_with_sibling = sum(1 for r in nolog if sub_of(r) in submissions_with_log)
    out["citation_log_coverage"]["files_without_log_with_log_sibling"] = n_nolog_with_sibling
    out["citation_log_coverage"]["files_without_log_isolated"] = len(nolog) - n_nolog_with_sibling

    # Denominator of the shares below: the confirmed-cited entries (citation_status_bbl_tex).
    confirmed_cited_total = conn.execute(
        "SELECT COUNT(*) n FROM entries WHERE citation_status_bbl_tex='confirmed_cited'"
    ).fetchone()["n"]
    assert confirmed_cited_total > 0, "no entries with citation_status_bbl_tex='confirmed_cited'"
    out["confirmed_cited_entries_total"] = confirmed_cited_total

    # entry-type breakdown among the confirmed-cited entries
    rows = conn.execute(
        "SELECT entry_type, COUNT(*) n FROM entries WHERE citation_status_bbl_tex='confirmed_cited' "
        "GROUP BY entry_type ORDER BY n DESC"
    ).fetchall()
    out["entry_type_breakdown"] = [
        {"entry_type": r["entry_type"], "n": r["n"], "pct": pct(r["n"], confirmed_cited_total)} for r in rows
    ]

    # state breakdown among the confirmed-cited entries
    rows = conn.execute(
        "SELECT state, COUNT(*) n FROM entries WHERE citation_status_bbl_tex='confirmed_cited' "
        "GROUP BY state ORDER BY n DESC"
    ).fetchall()
    out["state_breakdown"] = [
        {"state": r["state"], "n": r["n"], "pct": pct(r["n"], confirmed_cited_total)} for r in rows
    ]

    write_json("corpus_file_statistics", out)
    print(json.dumps(out, indent=2, default=str)[:4000])

    fc = out["file_counts"]
    large_files = out["large_files"]
    log_cov = out["citation_log_coverage"]
    numbers_payload = {
        "corpus_max_entries_per_submission": {"kind": "int", "value": out["entries_per_file"]["max"]},
        "corpus_total_bib_files": {"kind": "int", "value": fc["total"]},
        "corpus_assessed_files": {"kind": "int", "value": fc["assessed"]},
        "corpus_macroonly_files": {"kind": "int", "value": fc["non_bibliography_string_only"]},
        "corpus_empty_files": {"kind": "int", "value": fc["non_bibliography_empty"]},
        "corpus_nonbibliography_files": {
            "kind": "int", "value": fc["non_bibliography_string_only"] + fc["non_bibliography_empty"],
        },
        "corpus_sidecar_files": {"kind": "int", "value": log_cov["files_with_citation_log"]},
        "corpus_nosidecar_files": {"kind": "int", "value": log_cov["files_without_citation_log"]},
        "corpus_nosidecar_sibling_files_n": {
            "kind": "int", "value": log_cov["files_without_log_with_log_sibling"],
        },
        "corpus_nosidecar_isolated_files_n": {
            "kind": "int", "value": log_cov["files_without_log_isolated"],
        },
        "corpus_nosidecar_sibling_pct": {
            "kind": "ratio", "num": log_cov["files_without_log_with_log_sibling"],
            "den": log_cov["files_without_citation_log"], "decimals": 1,
        },
        "corpus_nosidecar_isolated_pct": {
            "kind": "ratio", "num": log_cov["files_without_log_isolated"],
            "den": log_cov["files_without_citation_log"], "decimals": 1,
        },
        "corpus_mega_raw_entries": {"kind": "int", "value": large_files["large_file_entries_sum"]},
        "corpus_mega_raw_entries_pct": {
            "kind": "ratio", "num": large_files["large_file_entries_sum"],
            "den": out["entries_per_file"]["total_entries"], "decimals": 2,
        },
    }
    for row in out["state_breakdown"]:
        state = row["state"] or "null"
        numbers_payload[f"corpusstate_{state}_n"] = {"kind": "int", "value": row["n"]}
    write_numbers("stats_corpus", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
