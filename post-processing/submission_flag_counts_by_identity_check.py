#!/usr/bin/env python3
"""Counts the arXiv submissions that lose their strict / broad predatory flag through the identity check.

Unit = submission (month/arxiv_id); population = entries_confirmed_bbl_tex. "before" = the verdicts stored in the database
(strict: final_assessment 'predatory'; broad: n_predatory_list_hits > 0); "after" = the same with the identity-check
overlay of identity_check_table_analyses.get_conn_after_identity_check() applied; "lost" = flagged before and not after;
"gained" = the reverse. The counts are computed by three code paths (Python set difference, one grouped SQL pass, and for
strict the view with the verdicts recomputed without invalid votes) that must agree, and the script stops otherwise.
Optional --verify-jsonl compares the "before" verdicts with the per-run JSONL results under EVAL_RESULTS_ROOT.

Reads EVAL_DB_PATH_BBL_TEX, the audit keys file written by full_corpus_identity_mismatch_audit.py and (through
get_conn_after_identity_check) identity_audit_citation_detail.json. Writes
numbers/submission_flag_counts_by_identity_check.numbers.json (keys correction_*) and
EVAL_OUTPUT_DIR/output_submission_flag_counts_by_identity_check/submission_flag_counts_by_identity_check.json
(aggregates only).
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from multiprocessing import Pool

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_submission_flag_counts_by_identity_check"
stats_common.OUTPUT_DIR = OUTPUT_DIR

SUB = "e.month || '/' || e.arxiv_id"
STRICT_BEFORE = "e.final_assessment = 'predatory'"
BROAD_BEFORE = "CAST(e.n_predatory_list_hits AS INTEGER) > 0"
STRICT_NOT_EXCLUDED = (
    "NOT EXISTS (SELECT 1 FROM strict_exclude x WHERE x.month = e.month AND x.arxiv_id = e.arxiv_id "
    "AND x.suffix = e.suffix AND x.entry_key = e.entry_key)"
)
BROAD_NOT_DROPPED = table_analyses.BROAD_NOT_DROPPED

JSONL_SAMPLE_SEED = 42


def submissions(conn, where: str) -> set[str]:
    return {r[0] for r in conn.execute(f"SELECT DISTINCT {SUB} FROM entries_confirmed_all e WHERE {where}")}


def submissions_by_set_difference(conn) -> dict:
    """Python set difference of per-submission sets."""
    out = {}
    for crit, before_where, after_where in (
        ("strict", STRICT_BEFORE, f"{STRICT_BEFORE} AND {STRICT_NOT_EXCLUDED}"),
        ("broad", BROAD_BEFORE, f"{BROAD_BEFORE} AND {BROAD_NOT_DROPPED}"),
    ):
        before, after = submissions(conn, before_where), submissions(conn, after_where)
        out[crit] = {
            "before": len(before), "after": len(after),
            "lost": len(before - after), "gained": len(after - before),
        }
    return out


def submissions_by_grouped_sql(conn) -> dict:
    """One grouped SQL pass: four per-submission flags, then SUM() the anti-joins."""
    row = conn.execute(
        f"""
        SELECT SUM(bs), SUM(a_s), SUM(bs = 1 AND a_s = 0), SUM(bs = 0 AND a_s = 1),
               SUM(bb), SUM(ab), SUM(bb = 1 AND ab = 0), SUM(bb = 0 AND ab = 1)
        FROM (
            SELECT {SUB} AS sub,
                   MAX({STRICT_BEFORE}) AS bs,
                   MAX({STRICT_BEFORE} AND {STRICT_NOT_EXCLUDED}) AS a_s,
                   MAX({BROAD_BEFORE}) AS bb,
                   MAX({BROAD_BEFORE} AND {BROAD_NOT_DROPPED}) AS ab
            FROM entries_confirmed_all e GROUP BY sub
        )
        """
    ).fetchone()
    keys = ("before", "after", "lost", "gained")
    return {"strict": dict(zip(keys, row[:4])), "broad": dict(zip(keys, row[4:]))}


def strict_submissions_by_valid_votes_view(conn) -> dict:
    """Strict only, via the view that substitutes the recomputed verdict (without invalid votes) per audited citation."""
    before = submissions(conn, STRICT_BEFORE)
    after = {
        r[0] for r in conn.execute(
            f"SELECT DISTINCT {SUB} FROM entries_confirmed_valid_votes e WHERE e.final_assessment_valid_votes = 'predatory'"
        )
    }
    return {"before": len(before), "after": len(after), "lost": len(before - after), "gained": len(after - before)}


def citation_counts(conn) -> dict:
    def n(table: str, where: str) -> int:
        return conn.execute(f"SELECT COUNT(*) n FROM {table} e WHERE {where}").fetchone()["n"]

    return {
        "before_strict": n("entries_confirmed_all", STRICT_BEFORE),
        "after_strict": n("entries_confirmed", STRICT_BEFORE),
        "before_broad": n("entries_confirmed_all", BROAD_BEFORE),
        "after_broad": n("entries_confirmed_all", f"{BROAD_BEFORE} AND {BROAD_NOT_DROPPED}"),
    }


# Optional cross-check of the database's "before" verdicts against the per-run JSONL results.

def _jsonl_records(path):
    """Yield (entry_key, final_assessment, n_hits) per line. final_assessment
    and predatory_list_hits precede the large backend_results field, so only
    that prefix is parsed; a full parse is the fallback."""
    with open(path, "rb") as f:
        for encoded_line in f:
            line = encoded_line.decode("utf-8", "replace")
            i = line.find('"backend_results"')
            rec = None
            if i > 0:
                try:
                    rec = json.loads(line[:i].rstrip().rstrip(",") + "}")
                except ValueError:
                    rec = None
            if rec is None:
                rec = json.loads(line)
            yield rec["entry_key"], rec.get("final_assessment"), len(rec.get("predatory_list_hits") or [])


def _check_file(job):
    results_root, (month, arxiv_id, suffix), db_rows = job
    want: dict = {}
    for key, fa, nh in db_rows:
        want.setdefault(key, set()).add((fa, int(nh or 0)))
    seen: dict = {}
    for key, fa, nh in _jsonl_records(f"{results_root}/{month}__{arxiv_id}-{suffix}.jsonl"):
        if key in want:
            seen.setdefault(key, set()).add((fa, nh))
    missing = sum(1 for k in want if k not in seen)
    mismatch = sum(1 for k, v in want.items() if k in seen and not v <= seen[k])
    return len(db_rows), missing, mismatch


def verify_jsonl(sample_files: int, workers: int) -> dict:
    results_root = str(stats_common.required_path_env("EVAL_RESULTS_ROOT"))
    db = sqlite3.connect(f"file:{table_analyses.DB_PATH_BBL_TEX}?mode=ro", uri=True)
    flagged = set(db.execute(
        f"SELECT DISTINCT month, arxiv_id, suffix FROM entries_confirmed_bbl_tex e "
        f"WHERE {STRICT_BEFORE} OR {BROAD_BEFORE}"
    ))
    others = sorted(set(db.execute("SELECT DISTINCT month, arxiv_id, suffix FROM entries_confirmed_bbl_tex")) - flagged)
    sample = set(random.Random(JSONL_SAMPLE_SEED).sample(others, min(sample_files, len(others))))

    def jobs(files):
        out = []
        for f in sorted(files):
            rows = db.execute(
                "SELECT entry_key, final_assessment, n_predatory_list_hits FROM entries_confirmed_bbl_tex "
                "WHERE month = ? AND arxiv_id = ? AND suffix = ?", f
            ).fetchall()
            out.append((results_root, f, rows))
        return out

    report = {"sample_seed": JSONL_SAMPLE_SEED, "n_files_total_confirmed_cited": len(others) + len(flagged)}
    for name, files in (("unflagged_sample", sample), ("flagged_files", flagged)):
        rows = missing = mismatch = 0
        with Pool(workers) as pool:
            for n, ms, mm in pool.imap_unordered(_check_file, jobs(files), chunksize=4):
                rows += n
                missing += ms
                mismatch += mm
        report[name] = {
            "files": len(files), "db_rows_checked": rows,
            "keys_missing_in_jsonl": missing, "keys_with_verdict_or_hit_count_mismatch": mismatch,
        }
        print(f"  jsonl cross-check {name}: {report[name]}", flush=True)
    report["all_agree"] = all(
        report[k]["keys_missing_in_jsonl"] == 0 and report[k]["keys_with_verdict_or_hit_count_mismatch"] == 0
        for k in ("unflagged_sample", "flagged_files")
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--verify-jsonl", action="store_true",
                    help="also cross-check the database's 'before' verdicts against the per-run JSONL results (slow)")
    ap.add_argument("--jsonl-sample-files", type=int, default=5000,
                    help="unflagged files to sample for --verify-jsonl (default: %(default)s)")
    ap.add_argument("--workers", type=int, default=8, help="processes for --verify-jsonl (default: %(default)s)")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    n_pop = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all").fetchone()["n"]
    n_subs = conn.execute(f"SELECT COUNT(DISTINCT {SUB}) n FROM entries_confirmed_all e").fetchone()["n"]

    by_set_difference = submissions_by_set_difference(conn)
    by_grouped_sql = submissions_by_grouped_sql(conn)
    by_valid_votes_view = strict_submissions_by_valid_votes_view(conn)
    cit = citation_counts(conn)
    audit = json.loads(table_analyses.AUDIT_KEYS_JSON.read_text())
    n_misattr = len(audit["confirmed_misattribution_keys"])
    n_survivors = len(audit["second_vote_survivor_keys"])
    n_strict_excl = len(audit["strict_exclude_keys"])
    n_broad_drops = conn.execute("SELECT COUNT(*) n FROM broad_drops").fetchone()["n"]

    checks = {
        "set_difference_and_grouped_sql_agree_exactly": by_set_difference == by_grouped_sql,
        "strict_valid_votes_view_agrees_with_set_difference": by_valid_votes_view == by_set_difference["strict"],
        "citation_level_strict_removed_equals_misattributed_minus_survivors": (
            cit["before_strict"] - cit["after_strict"] == n_misattr - n_survivors == n_strict_excl
        ),
        "citation_level_broad_removed_equals_broad_drop_keys": (
            cit["before_broad"] - cit["after_broad"] == n_broad_drops
        ),
        "lost_strict_le_strict_citations_removed": by_set_difference["strict"]["lost"] <= cit["before_strict"] - cit["after_strict"],
        "lost_broad_le_broad_citations_removed": by_set_difference["broad"]["lost"] <= cit["before_broad"] - cit["after_broad"],
    }
    for crit in ("strict", "broad"):
        checks[f"{crit}_before_equals_after_plus_lost_minus_gained"] = (
            by_set_difference[crit]["before"] == by_set_difference[crit]["after"]
            + by_set_difference[crit]["lost"] - by_set_difference[crit]["gained"]
        )

    out = {
        "population_confirmed_cited_entries": n_pop,
        "population_submissions": n_subs,
        "set_difference": by_set_difference,
        "grouped_sql": by_grouped_sql,
        "valid_votes_view_strict_only": by_valid_votes_view,
        "citation_level": cit,
        "audit_keys": {
            "confirmed_misattribution": n_misattr, "second_vote_survivors": n_survivors,
            "strict_exclude": n_strict_excl, "broad_drops": n_broad_drops,
        },
        "checks": checks,
    }
    if args.verify_jsonl:
        print("verifying database 'before' verdicts against the per-run JSONL results ...", flush=True)
        out["jsonl_crosscheck"] = verify_jsonl(args.jsonl_sample_files, args.workers)
        checks["jsonl_crosscheck_agrees"] = out["jsonl_crosscheck"]["all_agree"]
    out["all_checks_pass"] = all(checks.values())

    stats_common.write_json("submission_flag_counts_by_identity_check", out)
    print(json.dumps(out, indent=2, default=str))
    failed = [k for k, v in checks.items() if not v]
    assert not failed, f"checks failed, not writing numbers: {failed}"

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    stats_common.write_numbers("submission_flag_counts_by_identity_check", {
        "correction_before_strict_submissions_n": i(by_set_difference["strict"]["before"]),
        "correction_after_strict_submissions_n": i(by_set_difference["strict"]["after"]),
        "correction_lost_strict_submissions_n": i(by_set_difference["strict"]["lost"]),
        "correction_gained_strict_submissions_n": i(by_set_difference["strict"]["gained"]),
        "correction_before_broad_submissions_n": i(by_set_difference["broad"]["before"]),
        "correction_after_broad_submissions_n": i(by_set_difference["broad"]["after"]),
        "correction_lost_broad_submissions_n": i(by_set_difference["broad"]["lost"]),
        "correction_gained_broad_submissions_n": i(by_set_difference["broad"]["gained"]),
        "correction_before_strict_citations_n": i(cit["before_strict"]),
        "correction_after_strict_citations_n": i(cit["after_strict"]),
        "correction_before_broad_citations_n": i(cit["before_broad"]),
        "correction_after_broad_citations_n": i(cit["after_broad"]),
    })


if __name__ == "__main__":
    main()
