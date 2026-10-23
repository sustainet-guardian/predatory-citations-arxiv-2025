#!/usr/bin/env python3
"""Adds the column citation_status to the entries table of EVAL_DB_PATH (via stats_common.get_conn) and creates the view
entries_confirmed (rows with citation_status = 'confirmed_cited'). confirmed_cited: the entry's file has a citation log
(entries.has_cited_sidecar = '1'); excluded_sibling_inferred_uncited: the file has no citation log, but another .bib file of
the same (month, arxiv_id) submission has one; unverified_isolated: no citation log and no sibling with one. Uses
entries.has_cited_sidecar and files.has_cited_sidecar only.
"""
from __future__ import annotations

from stats_common import get_conn, submission_of


def main() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(entries)").fetchall()]
    if "citation_status" not in cols:
        cur.execute("ALTER TABLE entries ADD COLUMN citation_status TEXT")

    # files.name is "<arxiv_id>-<suffix>"; the submission is the part before the hyphen (stats_common.submission_of).
    log_files = cur.execute(
        "SELECT month, name FROM files WHERE category='assessed' AND has_cited_sidecar=1"
    ).fetchall()
    submissions_with_citation_log = {(month, submission_of(name)) for month, name in log_files}

    cur.execute("DROP TABLE IF EXISTS temp.submissions_with_citation_log")
    cur.execute("CREATE TEMP TABLE submissions_with_citation_log (month TEXT, arxiv_id TEXT)")
    cur.executemany(
        "INSERT INTO submissions_with_citation_log (month, arxiv_id) VALUES (?, ?)",
        submissions_with_citation_log,
    )
    cur.execute("CREATE INDEX idx_swcl ON submissions_with_citation_log(month, arxiv_id)")
    print("distinct submissions with >=1 citation log:", len(submissions_with_citation_log))

    cur.execute("UPDATE entries SET citation_status = NULL")

    cur.execute("UPDATE entries SET citation_status = 'confirmed_cited' WHERE has_cited_sidecar='1'")
    print("confirmed_cited:", cur.rowcount)

    cur.execute(
        """
        UPDATE entries SET citation_status = 'excluded_sibling_inferred_uncited'
        WHERE has_cited_sidecar='0' AND EXISTS (
            SELECT 1 FROM submissions_with_citation_log s
            WHERE s.month = entries.month AND s.arxiv_id = entries.arxiv_id
        )
        """
    )
    print("excluded_sibling_inferred_uncited:", cur.rowcount)

    cur.execute("UPDATE entries SET citation_status = 'unverified_isolated' WHERE citation_status IS NULL")
    print("unverified_isolated:", cur.rowcount)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_citation_status ON entries(citation_status)")

    # Primary-analysis-population view: the analysis scripts query entries_confirmed.
    cur.execute("DROP VIEW IF EXISTS entries_confirmed")
    cur.execute("CREATE VIEW entries_confirmed AS SELECT * FROM entries WHERE citation_status = 'confirmed_cited'")

    conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    by_status = cur.execute(
        "SELECT citation_status, COUNT(*) FROM entries GROUP BY citation_status"
    ).fetchall()
    print("\ntotal entries:", total)
    for status, n in by_status:
        print(f"  {status}: {n} ({100.0*n/total:.2f}%)")


if __name__ == "__main__":
    main()
