#!/usr/bin/env python3
"""Partitions the BibTeX entry count of the assessed files and the confirmed-cited entry count by file class and by origin.

Reads EVAL_DB_PATH_BBL_TEX (tables files and entries) and EVAL_OUTPUT_DIR/cited_entry_counts.json (written by
cited_entry_counts.py, which must run first). Computes the entries of files with and without a citation log, the entries table
rows by citation_status_bbl_tex, the confirmed-cited entries by file class, the per-file recount of cited keys in files with a
log, and the excess duplicate (month, arxiv_id, entry_key) rows within the confirmed-cited entries. Writes
numbers/corpus_size_partition.numbers.json (keys a1_*, corpus_*; counts only, no ratio) and
EVAL_OUTPUT_DIR/output_corpus_size_partition/corpus_size_partition.json (its percentages are shares of all .bib entries of the
assessed files and of the confirmed-cited entries, i.e. the entries rows with citation_status_bbl_tex = 'confirmed_cited').
The submission id of a file name '<NNNNN>-<k>' is the part before its hyphen (stats_common.submission_of); the data need exactly
one hyphen per name, and a name with another number of hyphens raises ValueError.
"""
from __future__ import annotations

import json
import sqlite3

import stats_common
from stats_common import required_path_env, submission_of

DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_corpus_size_partition"
CITED_ENTRY_COUNTS_JSON = required_path_env("EVAL_OUTPUT_DIR") / "cited_entry_counts.json"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.create_function("submission_of", 1, submission_of, deterministic=True)

    # Entries of the assessed .bib files (total, and split by files with / without a citation log).
    total_bib_entries = conn.execute("SELECT SUM(entry_count) FROM files WHERE category='assessed'").fetchone()[0]

    n_log_files = conn.execute("SELECT COUNT(*) FROM files WHERE category='assessed' AND has_cited_sidecar=1").fetchone()[0]
    n_no_log_files = conn.execute("SELECT COUNT(*) FROM files WHERE category='assessed' AND has_cited_sidecar=0").fetchone()[0]

    bib_entries_log = conn.execute("SELECT SUM(entry_count) FROM files WHERE category='assessed' AND has_cited_sidecar=1").fetchone()[0]
    bib_entries_no_log = conn.execute("SELECT SUM(entry_count) FROM files WHERE category='assessed' AND has_cited_sidecar=0").fetchone()[0]
    assert bib_entries_log + bib_entries_no_log == total_bib_entries

    total_entries_db_rows = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    print(f"total_entries_db_rows: {total_entries_db_rows}", flush=True)

    status_counts = dict(conn.execute(
        "SELECT citation_status_bbl_tex, COUNT(*) FROM entries GROUP BY citation_status_bbl_tex"
    ).fetchall())
    confirmed_cited = status_counts["confirmed_cited"]
    excluded_sibling = status_counts["excluded_sibling_inferred_uncited"]
    unverified_isolated = status_counts["unverified_isolated"]
    excluded_comment_artifact = status_counts["excluded_comment_artifact_suspected"]
    print(f"confirmed_cited: {confirmed_cited}", flush=True)
    assert confirmed_cited + excluded_sibling + unverified_isolated + excluded_comment_artifact == total_entries_db_rows

    # Initial citation-key pass: all entries rows except the suspected comment artifacts.
    candidate_population = total_entries_db_rows - excluded_comment_artifact
    print(f"candidate_population: {candidate_population}", flush=True)

    # Entries rows by file class: log-having files hold only the entries marked cited in their citation log, plus
    # the missed-keys rows; no-log files hold every .bib entry except malformed result lines.
    materialized_log = conn.execute("SELECT COUNT(*) FROM entries WHERE has_cited_sidecar='1'").fetchone()[0]
    materialized_no_log = conn.execute("SELECT COUNT(*) FROM entries WHERE has_cited_sidecar='0'").fetchone()[0]
    print(f"materialized_log: {materialized_log}", flush=True)
    assert 0 <= materialized_no_log <= bib_entries_no_log

    dropped_at_materialization_log = bib_entries_log - materialized_log  # .bib entries of log-having files without an entries row
    dropped_at_materialization_no_log = bib_entries_no_log - materialized_no_log  # .bib entries of no-log files without an entries row (malformed result lines)
    print(f"dropped_at_materialization_log: {dropped_at_materialization_log}", flush=True)
    assert dropped_at_materialization_no_log >= 0
    total_dropped_at_materialization = dropped_at_materialization_log + dropped_at_materialization_no_log
    assert total_entries_db_rows == total_bib_entries - total_dropped_at_materialization

    # Confirmed-cited entries split by file class.
    confirmed_from_log = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE citation_status_bbl_tex='confirmed_cited' AND has_cited_sidecar='1'"
    ).fetchone()[0]
    confirmed_from_no_log = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE citation_status_bbl_tex='confirmed_cited' AND has_cited_sidecar='0'"
    ).fetchone()[0]
    print(f"confirmed_from_log: {confirmed_from_log}", flush=True)
    assert confirmed_from_log + confirmed_from_no_log == confirmed_cited

    comment_artifact_from_log = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE citation_status_bbl_tex='excluded_comment_artifact_suspected' AND has_cited_sidecar='1'"
    ).fetchone()[0]
    assert comment_artifact_from_log == excluded_comment_artifact, (
        "all comment-artifact-suspected entries are expected to come from log-having files"
    )
    assert materialized_log - comment_artifact_from_log == confirmed_from_log

    # No-log files: .bib entries minus the excluded and unverified entries
    # = malformed-line drops + confirmed-cited entries of no-log files.
    no_log_remainder = bib_entries_no_log - (excluded_sibling + unverified_isolated)
    no_log_drops_and_confirmed = dropped_at_materialization_no_log + confirmed_from_no_log
    assert no_log_drops_and_confirmed == no_log_remainder, f"{no_log_drops_and_confirmed} != {no_log_remainder}"

    # Log-having side: "recounted_cited_sum" of cited_entry_counts.json is the per-file recount
    # |logged keys & compiled-bibliography/TeX signal| over the log-having files; it does not depend on the entries table.
    cited_entry_counts = json.loads(CITED_ENTRY_COUNTS_JSON.read_text())
    per_file_recount_cited = cited_entry_counts["recounted_cited_sum"]
    assert per_file_recount_cited > 0

    # a1_log_having_materialization_gap: per-file recount of cited keys in log-having files minus the
    # confirmed-cited entries of log-having files.
    materialization_gap = per_file_recount_cited - confirmed_from_log
    print(f"log-having materialization gap: {materialization_gap}", flush=True)

    # Submissions with more than one assessed .bib file, and duplicate (month, arxiv_id, entry_key) rows within
    # the confirmed-cited entries (one logical citation present in several .bib files of a submission).
    n_multi_bib_submissions = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT month, submission_of(name) AS aid
            FROM files WHERE category='assessed'
            GROUP BY month, aid HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    assert n_multi_bib_submissions > 0

    n_excess_dup_rows = conn.execute(
        """
        SELECT COALESCE(SUM(n_rows - 1), 0) FROM (
            SELECT month, arxiv_id, entry_key, COUNT(*) AS n_rows
            FROM entries WHERE citation_status_bbl_tex='confirmed_cited'
            GROUP BY month, arxiv_id, entry_key HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    print(f"n_excess_dup_rows: {n_excess_dup_rows}", flush=True)

    out = {
        "given_inputs_confirmed": {
            "total_bib_entries": total_bib_entries,
            "n_log_files": n_log_files, "n_no_log_files": n_no_log_files,
            "bib_entries_log_files": bib_entries_log, "bib_entries_no_log_files": bib_entries_no_log,
            "total_entries_db_rows": total_entries_db_rows,
            "confirmed_cited": confirmed_cited, "excluded_sibling_inferred_uncited": excluded_sibling,
            "unverified_isolated": unverified_isolated, "excluded_comment_artifact_suspected": excluded_comment_artifact,
        },
        "initial_citation_key_pass": {
            "definition": (
                f"entries rows with citation_status_bbl_tex != 'excluded_comment_artifact_suspected': "
                f"total_entries_db_rows ({total_entries_db_rows}) minus {excluded_comment_artifact} suspected comment artifacts."
            ),
            "value": candidate_population,
            "pct_of_bib_entries": round(100.0 * candidate_population / total_bib_entries, 2),
        },
        "log_having_files": {
            "per_file_recount_cited": per_file_recount_cited,
            "confirmed_cited_from_log_having_only": confirmed_from_log,
            "materialization_gap": materialization_gap,
            "confirmed_cited_from_no_log_files": confirmed_from_no_log,
        },
        "multi_bib_dedup_quantification": {
            "n_multi_bib_submissions": n_multi_bib_submissions,
            "n_excess_duplicate_rows_within_confirmed_cited": n_excess_dup_rows,
            "pct_of_confirmed_cited_that_is_excess_duplicate": round(100.0 * n_excess_dup_rows / confirmed_cited, 2),
        },
    }
    stats_common.write_json("corpus_size_partition", out)
    print(json.dumps(out, indent=2, default=str))

    # Identity behind the a1_* keys: per-file recount - gap + no-log confirmed-cited entries = confirmed-cited entries.
    assert per_file_recount_cited - materialization_gap - (-confirmed_from_no_log) == confirmed_cited
    stats_common.write_numbers("corpus_size_partition", {
        "a1_log_having_identified_as_cited": {"kind": "int", "value": per_file_recount_cited},
        "a1_log_having_materialization_gap": {"kind": "int", "value": materialization_gap},
        "a1_log_having_residual_x": {"kind": "int", "value": -confirmed_from_no_log},
        "corpus_confirmed_cited_entries": {"kind": "int", "value": confirmed_cited},
        "corpus_candidate_population": {"kind": "int", "value": candidate_population},
        "corpus_excluded_entries": {"kind": "int", "value": excluded_sibling},
        "corpus_unverified_entries": {"kind": "int", "value": unverified_isolated},
        "corpus_raw_total_entries": {"kind": "int", "value": total_bib_entries},
        "corpus_multi_bib_excess_dup_rows": {"kind": "int", "value": n_excess_dup_rows},
        "corpus_n_multi_bib_submissions": {"kind": "int", "value": n_multi_bib_submissions},
    })


if __name__ == "__main__":
    main()
