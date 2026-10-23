#!/usr/bin/env python3
"""Add-one simulation for three candidate evidence sources that are not part of the tool's 22-backend ensemble: the ACL
Anthology venue list (legitimacy vote), the Retraction Watch Hijacked Journal Checker and DOAJ's withdrawal change log
(predatory-list votes). For each source, and for all three together, the simulated vote is added to the recorded backend
results of every matching citation, the decision rule of decision_rule.py is re-applied (without the citation's own
invalid votes), and the changes in the strict, broad, legitimate and unknown counts are counted. The strict and broad
baselines are counts after the identity check (strict: final_assessment 'predatory' minus strict_exclude; broad:
n_predatory_list_hits > 0 minus the broad drops); the legitimate and unknown baselines are the stored verdict
final_assessment of entries_confirmed_all. Matching is by ISSN when
the citation has one and the source carries ISSNs, by exact venue name otherwise (the ACL list is name-only). The DOAJ
population is the benchmark set doaj_best_practice as assessed (the rows of the filtered change logs, not reduced to one
row per journal); the simulated vote uses its distinct ISSNs and names.
Reads EVAL_DB_PATH_BBL_TEX, EVAL_CACHE_DB_PATH, EVAL_RESULTS_ROOT (via leave_one_out_helpers), EVAL_ACL_VENUES_CSV
(written by analyze_bulk_database_assessment.py), the benchmark JSONLs under
EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/mass_eval_output and the identity-audit files under EVAL_OUTPUT_DIR. Writes
EVAL_OUTPUT_DIR/output_add_one_new_sources_impact/add_one_new_sources_impact.json (counts only) and
numbers/add_one_new_sources_impact.numbers.json (keys addone_*). Citations without a stored backend record are counted
in n_affected_citations and in n_missing_backend_record, in the add-one and in the combined simulation alike.
Option --limit N (smoke run): of the citations selected by each step, only those in the first N results files
(sorted by month, arxiv_id, suffix) are processed, and the invalid-vote map is built from the first N files of the
confirmed-misattribution citations; the script then runs to its end, writes
add_one_new_sources_impact_limit.json instead of add_one_new_sources_impact.json and writes no numbers file.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Callable, TypeVar

import leave_one_out_backend_sweep as loo_sweep
import identity_check_headline_analyses as headline_analyses
import leave_one_out_helpers as loo
import stats_common
from analyze_hijacked_doaj_benchmark import analyze_benchmark, load_jsonl
from decision_rule import weighted_vote_assessment
from identity_vote_rules import build_invalid_backends_map, normalize_issn
from stats_common import required_path_env

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_add_one_new_sources_impact"
stats_common.OUTPUT_DIR = OUTPUT_DIR

BENCHMARK_BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
RW_JSONL = BENCHMARK_BASE / "mass_eval_output" / "retraction_watch__retraction_watch.jsonl"
DOAJ_JSONL = BENCHMARK_BASE / "mass_eval_output" / "doaj_best_practice__doaj_best_practice.jsonl"
ACL_VENUES_CSV = required_path_env("EVAL_ACL_VENUES_CSV")


T = TypeVar("T")


def keep_first_files(items: list[T], file_of: Callable[[T], tuple], limit: int | None) -> list[T]:
    """With limit N: the items that lie in the first N distinct results files, sorted by (month, arxiv_id, suffix);
    without limit: all items."""
    if limit is None:
        return items
    allowed = set(sorted({file_of(x) for x in items})[:limit])
    return [x for x in items if file_of(x) in allowed]


def file_of_row(r) -> tuple[str, str, str]:
    return (r["month"], r["arxiv_id"], r["suffix"])


def recompute_with_extra_votes(backend_results: list[dict], exclude_backends: set[str], extra_votes: list[dict]) -> str:
    """Category of the tool's weighted-vote rule (decision_rule.py) from the backend results with status "found" without the
    backends in exclude_backends, plus the simulated votes in extra_votes."""
    found = [r for r in backend_results if r.get("status") == "found" and r.get("backend_name") not in exclude_backends]
    return weighted_vote_assessment(found + extra_votes)[0]


def load_source_lists() -> dict[str, dict]:
    rw_records = load_jsonl(RW_JSONL)
    doaj_records = load_jsonl(DOAJ_JSONL)
    if not ACL_VENUES_CSV.is_file():
        raise SystemExit(f"{ACL_VENUES_CSV} not found: run analyze_bulk_database_assessment.py first (it writes "
                         "bulk_database_venues_acl.csv to EVAL_OUTPUT_DIR) and point EVAL_ACL_VENUES_CSV at it")
    acl_names = {row["venue"].strip() for row in csv.DictReader(ACL_VENUES_CSV.open(encoding="utf-8"))}
    assert acl_names, f"{ACL_VENUES_CSV} lists no venues"

    def issn_and_name_sets(records: list[dict]) -> tuple[set[str], set[str]]:
        issns, names = set(), set()
        for r in records:
            n = normalize_issn(r.get("issn"))
            if n:
                issns.add(n)
            else:
                v = (r.get("venue_raw") or "").strip()
                if v:
                    names.add(v)
        return issns, names

    rw_issns, rw_names = issn_and_name_sets(rw_records)
    doaj_issns, doaj_names = issn_and_name_sets(doaj_records)

    return {
        "acl_anthology": {"issns": set(), "names": acl_names, "vote_assessment": "legitimate", "evidence_type": "legitimate_list"},
        "rw_hijacked_checker": {"issns": rw_issns, "names": rw_names, "vote_assessment": "predatory", "evidence_type": "predatory_list"},
        "doaj_withdrawal": {"issns": doaj_issns, "names": doaj_names, "vote_assessment": "predatory", "evidence_type": "predatory_list"},
    }


def find_affected_rows(conn, issns: set[str], names: set[str]) -> list[sqlite3.Row]:
    """Citations matched by ISSN or venue name. A citation with a normalizable ISSN is matched only by ISSN when the
    source carries ISSNs; a citation without ISSN, or a source without ISSNs (ACL), is matched by exact venue name."""
    conn.execute("DROP TABLE IF EXISTS temp.src_issn")
    conn.execute("CREATE TEMP TABLE src_issn (issn TEXT)")
    conn.executemany("INSERT INTO src_issn VALUES (?)", [(i,) for i in issns])
    conn.execute("DROP TABLE IF EXISTS temp.src_name")
    conn.execute("CREATE TEMP TABLE src_name (name TEXT)")
    conn.executemany("INSERT INTO src_name VALUES (?)", [(n,) for n in names])

    name_fallback_condition = (
        "COALESCE(e.matched_venue_name, e.venue_raw) IN (SELECT name FROM src_name)"
        if not issns
        else "(normalize_issn(e.issn) IS NULL AND COALESCE(e.matched_venue_name, e.venue_raw) IN (SELECT name FROM src_name))"
    )
    return conn.execute(
        f"""
        SELECT month, arxiv_id, suffix, entry_key, issn,
               predatory_list_hits, n_predatory_list_hits, final_assessment
        FROM entries_confirmed_all e
        WHERE
            (normalize_issn(e.issn) IS NOT NULL AND normalize_issn(e.issn) IN (SELECT issn FROM src_issn))
            OR {name_fallback_condition}
        """
    ).fetchall()


def simulate_add_one(
    source_key: str, source: dict, conn, invalid_backends_map: dict, broad_drop_keys: set,
    baseline_counts: dict, limit: int | None = None,
) -> dict:
    affected = keep_first_files(find_affected_rows(conn, source["issns"], source["names"]), file_of_row, limit)
    n_affected = len(affected)
    keys = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in affected]
    records = loo.load_records(keys)

    is_predatory_source = source["evidence_type"] == "predatory_list"
    flip_matrix: Counter = Counter()
    n_missing_backend_record = 0
    n_broad_gained = 0
    n_strict_gained = 0
    n_strict_lost = 0
    n_conflicts = 0

    for r in affected:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        rec = records.get(key)
        if rec is None:
            n_missing_backend_record += 1
            continue
        own_invalid = invalid_backends_map.get(key, set())
        backend_results = rec.get("backend_results", [])

        baseline_assessment = recompute_with_extra_votes(backend_results, own_invalid, [])
        baseline_broad = bool(int(r["n_predatory_list_hits"] or 0) > 0 and key not in broad_drop_keys)

        extra_vote = {
            "backend_name": f"sim_{source_key}", "status": "found",
            "assessment": source["vote_assessment"], "confidence": 1.0,
            "evidence_type": source["evidence_type"],
        }
        assessment_with_source = recompute_with_extra_votes(backend_results, own_invalid, [extra_vote])
        broad_with_source = True if is_predatory_source else baseline_broad

        flip_matrix[(baseline_assessment, assessment_with_source)] += 1
        if broad_with_source and not baseline_broad:
            n_broad_gained += 1
        if assessment_with_source == "predatory" and baseline_assessment != "predatory":
            n_strict_gained += 1
        if baseline_assessment == "predatory" and assessment_with_source != "predatory":
            n_strict_lost += 1
        if (baseline_assessment, assessment_with_source) in (("legitimate", "predatory"), ("predatory", "legitimate")):
            n_conflicts += 1

    n_strict_delta = n_strict_gained - n_strict_lost
    n_legit_gained = flip_matrix.get(("unknown", "legitimate"), 0) + flip_matrix.get(("suspicious", "legitimate"), 0)
    n_legit_lost = sum(v for (a, b), v in flip_matrix.items() if a == "legitimate" and b != "legitimate")
    n_unknown_lost = sum(v for (a, b), v in flip_matrix.items() if a == "unknown" and b != "unknown")
    n_unknown_gained = sum(v for (a, b), v in flip_matrix.items() if b == "unknown" and a != "unknown")

    return {
        "source": source_key,
        "n_affected_citations": n_affected,
        "n_missing_backend_record": n_missing_backend_record,
        "flip_matrix": {f"{a}->{b}": n for (a, b), n in sorted(flip_matrix.items(), key=lambda kv: -kv[1])},
        "n_verdict_unchanged": sum(n for (a, b), n in flip_matrix.items() if a == b),
        "n_verdict_changed": sum(n for (a, b), n in flip_matrix.items() if a != b),
        "count_deltas": {
            "strict_predatory_delta": n_strict_delta,
            "strict_predatory_total_after_adding": baseline_counts["strict"] + n_strict_delta,
            "broad_predatory_delta": n_broad_gained,
            "broad_predatory_total_after_adding": baseline_counts["broad"] + n_broad_gained,
            "legitimate_delta": n_legit_gained - n_legit_lost,
            "legitimate_total_after_adding": baseline_counts["legitimate"] + n_legit_gained - n_legit_lost,
            "unknown_delta": n_unknown_gained - n_unknown_lost,
            "unknown_total_after_adding": baseline_counts["unknown"] + n_unknown_gained - n_unknown_lost,
        },
        "n_conflicts_with_existing_evidence": n_conflicts,
    }


def simulate_combined(
    sources: dict, conn, invalid_backends_map: dict, broad_drop_keys: set,
    baseline_counts: dict, limit: int | None = None,
) -> dict:
    """All three sources added simultaneously: a citation matching several sources receives all their votes at once.
    Matching is done per source with find_affected_rows and merged, because a query over the union of the ISSN and name
    sets would apply the ISSN condition of the other two sources to the name-only ACL matches."""
    by_key: dict[tuple, sqlite3.Row] = {}
    matched_sources_by_key: dict[tuple, list[str]] = {}
    for source_key, s in sources.items():
        for r in find_affected_rows(conn, s["issns"], s["names"]):
            key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
            by_key[key] = r
            matched_sources_by_key.setdefault(key, []).append(source_key)

    affected = keep_first_files([(by_key[key], matched_sources_by_key[key]) for key in by_key],
                                lambda item: file_of_row(item[0]), limit)

    n_affected = len(affected)
    keys = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r, _ in affected]
    records = loo.load_records(keys)

    flip_matrix: Counter = Counter()
    n_missing_backend_record = 0
    n_broad_gained = 0
    n_strict_gained = 0
    n_strict_lost = 0
    n_multi_source_matches = sum(1 for _, ms in affected if len(ms) > 1)

    for r, matched_sources in affected:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        rec = records.get(key)
        if rec is None:
            n_missing_backend_record += 1
            continue
        own_invalid = invalid_backends_map.get(key, set())
        backend_results = rec.get("backend_results", [])

        baseline_assessment = recompute_with_extra_votes(backend_results, own_invalid, [])
        baseline_broad = bool(int(r["n_predatory_list_hits"] or 0) > 0 and key not in broad_drop_keys)

        extra_votes = [
            {"backend_name": f"sim_{sk}", "status": "found", "assessment": sources[sk]["vote_assessment"],
             "confidence": 1.0, "evidence_type": sources[sk]["evidence_type"]}
            for sk in matched_sources
        ]
        assessment_with_source = recompute_with_extra_votes(backend_results, own_invalid, extra_votes)
        broad_with_source = baseline_broad or any(sources[sk]["evidence_type"] == "predatory_list" for sk in matched_sources)

        flip_matrix[(baseline_assessment, assessment_with_source)] += 1
        if broad_with_source and not baseline_broad:
            n_broad_gained += 1
        if assessment_with_source == "predatory" and baseline_assessment != "predatory":
            n_strict_gained += 1
        if baseline_assessment == "predatory" and assessment_with_source != "predatory":
            n_strict_lost += 1

    n_strict_delta = n_strict_gained - n_strict_lost
    n_legit_gained = flip_matrix.get(("unknown", "legitimate"), 0) + flip_matrix.get(("suspicious", "legitimate"), 0)
    n_legit_lost = sum(v for (a, b), v in flip_matrix.items() if a == "legitimate" and b != "legitimate")
    n_unknown_lost = sum(v for (a, b), v in flip_matrix.items() if a == "unknown" and b != "unknown")
    n_unknown_gained = sum(v for (a, b), v in flip_matrix.items() if b == "unknown" and a != "unknown")

    return {
        "n_affected_citations_any_source": n_affected,
        "n_missing_backend_record": n_missing_backend_record,
        "n_citations_matching_multiple_sources": n_multi_source_matches,
        "flip_matrix": {f"{a}->{b}": n for (a, b), n in sorted(flip_matrix.items(), key=lambda kv: -kv[1])},
        "count_deltas": {
            "strict_predatory_delta": n_strict_delta,
            "strict_predatory_total_after_adding": baseline_counts["strict"] + n_strict_delta,
            "broad_predatory_delta": n_broad_gained,
            "broad_predatory_total_after_adding": baseline_counts["broad"] + n_broad_gained,
            "legitimate_delta": n_legit_gained - n_legit_lost,
            "legitimate_total_after_adding": baseline_counts["legitimate"] + n_legit_gained - n_legit_lost,
            "unknown_delta": n_unknown_gained - n_unknown_lost,
            "unknown_total_after_adding": baseline_counts["unknown"] + n_unknown_gained - n_unknown_lost,
        },
    }


def acl_corpus_coverage(conn, acl_names: set[str]) -> dict:
    conn.execute("CREATE TEMP TABLE acl_names (name TEXT)")
    conn.executemany("INSERT INTO acl_names VALUES (?)", [(n,) for n in acl_names])
    rows = conn.execute(
        "SELECT final_assessment, COUNT(*) n FROM entries_confirmed_all "
        "WHERE COALESCE(matched_venue_name, venue_raw) IN (SELECT name FROM acl_names) "
        "GROUP BY final_assessment"
    ).fetchall()
    dist = {r["final_assessment"]: r["n"] for r in rows}
    total = sum(dist.values())
    n_unknown_suspicious = dist.get("unknown", 0) + dist.get("suspicious", 0)
    n_legitimate = dist.get("legitimate", 0)
    return {
        "n_total_matched_citations": total,
        "final_assessment_distribution": dist,
        "n_unknown_or_suspicious": n_unknown_suspicious,
        "pct_unknown_or_suspicious": round(100.0 * n_unknown_suspicious / total, 3) if total else None,
        "n_already_legitimate": n_legitimate,
        "pct_already_legitimate": round(100.0 * n_legitimate / total, 3) if total else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Add-one simulation of three candidate evidence sources.")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="smoke run: process only the first N results files of each step; writes no numbers file")
    limit = parser.parse_args().limit
    if limit is not None and limit < 1:
        parser.error("--limit must be a positive integer")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # misattributed_keys: all confirmed identity-misattributed citations; the invalid-vote map strips each citation's own
    # invalid vote whether or not the citation keeps its strict flag.
    conn, _ = headline_analyses.get_conn_with_exclusions()
    conn.create_function("normalize_issn", 1, normalize_issn)
    cache_conn = sqlite3.connect(str(loo_sweep.CACHE_DB_PATH))

    misattributed_keys = {tuple(k) for k in json.loads(loo_sweep.AUDIT_KEYS_JSON.read_text())["confirmed_misattribution_keys"]}
    misattributed_keys = set(keep_first_files(sorted(misattributed_keys), lambda k: k[:3], limit))

    per_citation = json.loads(loo_sweep.CITATION_DETAIL_PATH.read_text())
    # citations whose broad flag does not survive the identity check
    broad_drop_keys = {(c["month"], c["arxiv_id"], c["suffix"], c["entry_key"]) for c in per_citation if not c["broad_survives"]}
    headline_analyses.setup_broad_drops_table(conn, per_citation)

    invalid_backends_map = build_invalid_backends_map(conn, cache_conn, misattributed_keys)

    n_strict_baseline = conn.execute("SELECT COUNT(*) n FROM entries_confirmed WHERE final_assessment='predatory'").fetchone()["n"]
    n_broad_baseline = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    ).fetchone()["n"]
    n_legitimate_baseline = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all WHERE final_assessment='legitimate'").fetchone()["n"]
    n_unknown_baseline = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all WHERE final_assessment='unknown'").fetchone()["n"]
    baseline_counts = {"strict": n_strict_baseline, "broad": n_broad_baseline, "legitimate": n_legitimate_baseline, "unknown": n_unknown_baseline}
    print(f"baseline counts after the identity check: {baseline_counts}", flush=True)

    # share of each benchmark list that hits at least one of the nine predatory-list watchlists
    rw_records = load_jsonl(RW_JSONL)
    doaj_records = load_jsonl(DOAJ_JSONL)
    rw_coverage = analyze_benchmark(rw_records, "retraction_watch_hijacked_checker")
    doaj_coverage = analyze_benchmark(doaj_records, "doaj_best_practice")
    assert rw_coverage["total_size"] > 0 and doaj_coverage["total_size"] > 0, "a benchmark list is empty"

    # ACL Anthology venues among the confirmed citations
    sources = load_source_lists()
    acl_coverage = acl_corpus_coverage(conn, sources["acl_anthology"]["names"])

    def ts() -> str:
        return datetime.now().isoformat(timespec="seconds")

    add_one_results = {}
    for i, (key, source) in enumerate(sources.items()):
        print(f"[{ts()}] add-one simulation {i+1}/{len(sources)}: {key} ...", flush=True)
        add_one_results[key] = simulate_add_one(key, source, conn, invalid_backends_map, broad_drop_keys, baseline_counts, limit)
    print(f"[{ts()}] combined simulation (all three sources) ...", flush=True)
    combined_result = simulate_combined(sources, conn, invalid_backends_map, broad_drop_keys, baseline_counts, limit)
    print(f"[{ts()}] add-one evaluation finished.", flush=True)

    out = {
        "overlap_coverage": {
            "rw_hijacked_checker": {
                "total_size": rw_coverage["total_size"],
                "n_overlap_any_of_9_watchlists": rw_coverage["n_overlap_any_of_9_watchlists"],
                "pct_overlap_any_of_9_watchlists": rw_coverage["pct_overlap_any_of_9_watchlists"],
            },
            "doaj_withdrawal": {
                "total_size": doaj_coverage["total_size"],
                "n_overlap_any_of_9_watchlists": doaj_coverage["n_overlap_any_of_9_watchlists"],
                "pct_overlap_any_of_9_watchlists": doaj_coverage["pct_overlap_any_of_9_watchlists"],
            },
            "acl_anthology_corpus_coverage": acl_coverage,
        },
        "add_one_impact": add_one_results,
        "combined_all_three": combined_result,
    }
    stats_common.write_json("add_one_new_sources_impact" if limit is None else "add_one_new_sources_impact_limit", out)
    print(json.dumps(out, indent=2, default=str)[:6000])

    numbers = {
        "addone_rw_coverage_pct": {"kind": "ratio", "num": rw_coverage["n_overlap_any_of_9_watchlists"], "den": rw_coverage["total_size"], "decimals": 1},
        "addone_doaj_coverage_pct": {"kind": "ratio", "num": doaj_coverage["n_overlap_any_of_9_watchlists"], "den": doaj_coverage["total_size"], "decimals": 1},
        "addone_acl_pct_unknown_suspicious": {"kind": "ratio", "num": acl_coverage["n_unknown_or_suspicious"], "den": acl_coverage["n_total_matched_citations"], "decimals": 1},
        "addone_acl_n_affected": {"kind": "int", "value": add_one_results["acl_anthology"]["n_affected_citations"]},
        "addone_rw_n_affected": {"kind": "int", "value": add_one_results["rw_hijacked_checker"]["n_affected_citations"]},
        "addone_doaj_n_affected": {"kind": "int", "value": add_one_results["doaj_withdrawal"]["n_affected_citations"]},
        "addone_acl_strict_delta": {"kind": "int", "value": add_one_results["acl_anthology"]["count_deltas"]["strict_predatory_delta"]},
        "addone_rw_strict_delta": {"kind": "int", "value": add_one_results["rw_hijacked_checker"]["count_deltas"]["strict_predatory_delta"]},
        "addone_rw_broad_delta": {"kind": "int", "value": add_one_results["rw_hijacked_checker"]["count_deltas"]["broad_predatory_delta"]},
        "addone_doaj_strict_delta": {"kind": "int", "value": add_one_results["doaj_withdrawal"]["count_deltas"]["strict_predatory_delta"]},
        "addone_doaj_broad_delta": {"kind": "int", "value": add_one_results["doaj_withdrawal"]["count_deltas"]["broad_predatory_delta"]},
        "addone_combined_strict_delta": {"kind": "int", "value": combined_result["count_deltas"]["strict_predatory_delta"]},
        "addone_combined_broad_delta": {"kind": "int", "value": combined_result["count_deltas"]["broad_predatory_delta"]},
        "addone_acl_n_conflicts": {"kind": "int", "value": add_one_results["acl_anthology"]["n_conflicts_with_existing_evidence"]},
        "addone_rw_n_conflicts": {"kind": "int", "value": add_one_results["rw_hijacked_checker"]["n_conflicts_with_existing_evidence"]},
        "addone_doaj_n_conflicts": {"kind": "int", "value": add_one_results["doaj_withdrawal"]["n_conflicts_with_existing_evidence"]},
        # ACL: number of distinct venue names, and citations moved from unknown/suspicious to legitimate
        "addone_acl_distinct_venues": {"kind": "int", "value": len(sources["acl_anthology"]["names"])},
        "addone_acl_moved_to_legitimate_n": {"kind": "int", "value": add_one_results["acl_anthology"]["flip_matrix"].get("unknown->legitimate", 0)},
        "addone_acl_moved_suspicious_to_legitimate_n": {"kind": "int", "value": add_one_results["acl_anthology"]["flip_matrix"].get("suspicious->legitimate", 0)},
        # citations that keep their predatory verdict when the source is added (predatory->predatory cell)
        "addone_rw_n_predatory_kept": {"kind": "int", "value": add_one_results["rw_hijacked_checker"]["flip_matrix"].get("predatory->predatory", 0)},
        "addone_doaj_n_predatory_kept": {"kind": "int", "value": add_one_results["doaj_withdrawal"]["flip_matrix"].get("predatory->predatory", 0)},
        # citations matched by at least one of the three sources (each counted once), and by more than one
        "addone_combined_n_affected": {"kind": "int", "value": combined_result["n_affected_citations_any_source"]},
        "addone_combined_n_multi_source": {"kind": "int", "value": combined_result["n_citations_matching_multiple_sources"]},
    }
    if limit is None:
        stats_common.write_numbers("add_one_new_sources_impact", numbers)
    else:
        print(f"--limit {limit}: smoke run, numbers/add_one_new_sources_impact.numbers.json was NOT written and the "
              "results above cover only the first files of each step", flush=True)


if __name__ == "__main__":
    main()
