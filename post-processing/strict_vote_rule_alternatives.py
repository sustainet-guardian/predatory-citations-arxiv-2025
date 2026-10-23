#!/usr/bin/env python3
"""Counts of two vote-count alternatives to the strict verdict -- ge2: at least two predatory votes; votes_ge_legit:
predatory votes at least equal to legitimate votes and at least one -- before and after removing the invalid
votes of the confirmed-misattribution citations. A vote is a backend result with status 'found'; predatory
votes are those assessed 'predatory', legitimate votes those assessed 'legitimate'. Before: the stored
predatory_votes / legitimate_votes columns over entries_confirmed_all. After: for each audited citation the
invalid-vote backends are removed from its stored backend results (the recomputed votes are first checked
against the stored columns). Two populations: "all" confirmed-cited entries and "resolved" (verdict
legitimate, predatory, suspicious or insufficient_data; membership by the stored verdict before and by the
verdict recomputed without the invalid votes after).
The strict count after the identity check is final_assessment 'predatory' minus strict_exclude (view entries_confirmed).
Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT and EVAL_CACHE_DB_PATH (through identity_check_table_analyses).
Writes EVAL_OUTPUT_DIR/output_strict_vote_rule_alternatives/strict_vote_rule_alternatives.json and
numbers/strict_vote_rule_alternatives.numbers.json (keys strictvote_*).
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses
import leave_one_out_helpers as loo
from identity_vote_rules import build_invalid_backends_map

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_strict_vote_rule_alternatives"
# The imported modules set stats_common.OUTPUT_DIR, DB_PATH and get_conn to their own values; this script's values are set here.
stats_common.get_conn = table_analyses.get_conn_after_identity_check
stats_common.DB_PATH = table_analyses.DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR

RESOLVED = {"legitimate", "predatory", "suspicious", "insufficient_data"}
RULES = {
    "ge2": ("CAST(predatory_votes AS INTEGER) >= 2", lambda p, l: p >= 2),
    "votes_ge_legit": (
        "CAST(predatory_votes AS INTEGER) >= CAST(legitimate_votes AS INTEGER) "
        "AND CAST(predatory_votes AS INTEGER) >= 1",
        lambda p, l: p >= l and p >= 1,
    ),
}


def votes(backend_results: list[dict], exclude: frozenset = frozenset()) -> tuple[int, int]:
    p = l = 0
    for r in backend_results:
        if r.get("status") != "found" or r.get("backend_name") in exclude:
            continue
        if r.get("assessment") == "predatory":
            p += 1
        elif r.get("assessment") == "legitimate":
            l += 1
    return p, l


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()
    audit = json.loads(table_analyses.AUDIT_KEYS_JSON.read_text())
    keys = {tuple(k) for k in audit["confirmed_misattribution_keys"]}
    assert keys, "no confirmed-misattribution keys"

    invalid = build_invalid_backends_map(conn, table_analyses.get_cache_conn(), keys)
    assert len(invalid) == len(keys), f"invalid-vote map covers {len(invalid)} of {len(keys)} citations"
    records = loo.load_records(list(keys))
    assert len(records) == len(keys), f"loaded {len(records)} of {len(keys)} stored records"

    # Stored vote columns and stored / recomputed verdicts of the audited citations.
    stored = {}
    for k in keys:
        rows = conn.execute(
            "SELECT predatory_votes, legitimate_votes, final_assessment, final_assessment_valid_votes "
            "FROM entries_confirmed_valid_votes WHERE month=? AND arxiv_id=? AND suffix=? AND entry_key=?", k
        ).fetchall()
        assert len(rows) == 1, f"expected one database row per audited citation, got {len(rows)}"
        r = rows[0]
        stored[k] = (int(r[0]), int(r[1]), r[2], r[3])

    def n_where(where: str, resolved_only: bool) -> int:
        extra = " AND final_assessment IN ('legitimate','predatory','suspicious','insufficient_data')" if resolved_only else ""
        return conn.execute(f"SELECT COUNT(*) n FROM entries_confirmed_all WHERE {where}{extra}").fetchone()["n"]

    out: dict = {"n_audited_citations": len(keys)}
    n_validated = 0
    for k in keys:
        assert votes(records[k]["backend_results"]) == stored[k][:2], "recomputed votes differ from the stored columns"
        n_validated += 1
    out["n_audited_votes_validated_against_stored_columns"] = n_validated

    for rule, (where, test) in RULES.items():
        res = {}
        for pop in ("all", "resolved"):
            resolved_only = pop == "resolved"
            n_corpus_before = n_where(where, resolved_only)
            n_satisfying_before = n_satisfying_after = 0
            for k in keys:
                p0, l0 = votes(records[k]["backend_results"])
                p1, l1 = votes(records[k]["backend_results"], frozenset(invalid[k]))
                stored_verdict, recomputed_verdict = stored[k][2], stored[k][3]
                in_population_before = (stored_verdict in RESOLVED) if resolved_only else True
                in_population_after = (recomputed_verdict in RESOLVED) if resolved_only else True
                n_satisfying_before += int(in_population_before and test(p0, l0))
                n_satisfying_after += int(in_population_after and test(p1, l1))
            res[pop] = {
                "n_before_identity_check": n_corpus_before,
                "n_after_identity_check": n_corpus_before - n_satisfying_before + n_satisfying_after,
                "audited_satisfying_before_identity_check": n_satisfying_before,
                "audited_satisfying_after_identity_check": n_satisfying_after,
            }
        out[rule] = res

    strict_before = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all WHERE final_assessment='predatory'").fetchone()["n"]
    strict_after = conn.execute("SELECT COUNT(*) n FROM entries_confirmed WHERE final_assessment='predatory'").fetchone()["n"]
    out["strict_n_before_identity_check"], out["strict_n_after_identity_check"] = strict_before, strict_after

    # Resolved-eligible population by the recomputed verdict, and the strict citations inside it; all strict citations must lie in it.
    in_resolved = "final_assessment_valid_votes IN ('legitimate','predatory','suspicious','insufficient_data')"
    resolved_pop = conn.execute(f"SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE {in_resolved}").fetchone()["n"]
    strict_in_resolved = conn.execute(
        f"SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE {in_resolved} AND final_assessment_valid_votes = 'predatory'"
    ).fetchone()["n"]
    assert strict_in_resolved == strict_after, f"strict after identity check {strict_after} vs strict inside resolved-eligible {strict_in_resolved}"
    out["resolved_eligible_population_n"], out["strict_inside_resolved_eligible_n"] = resolved_pop, strict_in_resolved
    stats_common.write_json("strict_vote_rule_alternatives", out)
    print(json.dumps(out, indent=2))

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    numbers: dict = {}
    for rule in RULES:
        for pop in ("all", "resolved"):
            for when, key in (("n_before_identity_check", "precorr"), ("n_after_identity_check", "corrected")):
                numbers[f"strictvote_{rule}_{pop}_{key}_n"] = i(out[rule][pop][when])
        numbers[f"strictvote_{rule}_audited_precorr_n"] = i(out[rule]["all"]["audited_satisfying_before_identity_check"])
        numbers[f"strictvote_{rule}_audited_corrected_n"] = i(out[rule]["all"]["audited_satisfying_after_identity_check"])
    numbers["strictvote_n_audited_citations"] = i(len(keys))
    numbers["strictvote_resolved_population_n"] = i(resolved_pop)
    numbers["strictvote_strict_in_resolved_n"] = i(strict_in_resolved)
    stats_common.write_numbers("strict_vote_rule_alternatives", numbers)


if __name__ == "__main__":
    main()
