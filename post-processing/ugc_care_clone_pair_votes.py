#!/usr/bin/env python3
"""Counts the confirmed-cited references that have a vote from both backends built from the same copied UGC-CARE pages:
ugc_care_cloned / ugc_care_cloned_group2 (assessment predatory) and ugc_care_included_from_clone_group1 / _group2 (assessment
legitimate). A vote is a backend result with status 'found'. Reports, per group and across groups, how many references have a
vote from both backends, their verdicts, and the strict count when the confidence-weighted rule is applied again without the
vote of the included backend of that group (votes invalidated by the identity check are removed, as in the counts after the
identity check). Only the strict count can change: the included vote is a legitimate vote and the broad count depends on the
predatory-list votes only. Applied to all votes except the invalidated ones, the rule is first checked to reproduce the
verdict of every reference.
The keys uccdv_strict_before_n / uccdv_broad_before_n are the counts after the identity check; uccdv_strict_after_n is the
counterfactual strict count with the included votes of the pairs left out (not a count of the assessment as run).
Strict = final_assessment 'predatory' minus strict_exclude; broad = n_predatory_list_hits > 0 minus the broad drops.
Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT, EVAL_CACHE_DB_PATH and the audit keys file under EVAL_OUTPUT_DIR. Writes
EVAL_OUTPUT_DIR/output_ugc_care_clone_pair_votes/ugc_care_clone_pair_votes.json and
numbers/ugc_care_clone_pair_votes.numbers.json (keys uccdv_*)."""
from __future__ import annotations

import json
from collections import Counter

import stats_common
import identity_check_table_analyses as table_analyses
from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import build_invalid_backends_map
from leave_one_out_helpers import load_records

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_ugc_care_clone_pair_votes"
stats_common.OUTPUT_DIR = OUTPUT_DIR
stats_common.DB_PATH = table_analyses.DB_PATH_BBL_TEX
stats_common.get_conn = table_analyses.get_conn_after_identity_check

COPY_LISTS = {"1": "ugc_care_cloned", "2": "ugc_care_cloned_group2"}
INCLUDED_LISTS = {"1": "ugc_care_included_from_clone_group1", "2": "ugc_care_included_from_clone_group2"}
VERDICTS = ("predatory", "suspicious", "legitimate", "unknown")
# group names used in the numbers keys (macro names are built with the digits stripped)
GROUP_NAME = {"1": "groupi", "2": "groupii"}


def votes(rec: dict) -> tuple[set, set]:
    """(backends with a predatory-list vote, backends with a legitimate-list vote) from a record."""
    pred, legit = set(), set()
    for b in rec.get("backend_results") or []:
        if b.get("status") != "found":
            continue
        if b.get("assessment") == "predatory" and b.get("evidence_type") == "predatory_list":
            pred.add(b["backend_name"])
        elif b.get("assessment") == "legitimate":
            legit.add(b["backend_name"])
    return pred, legit


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()
    cache_conn = table_analyses.get_cache_conn()

    n_pop = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all").fetchone()["n"]
    rows = conn.execute(
        "SELECT e.month, e.arxiv_id, e.suffix, e.entry_key, e.final_assessment, e.predatory_list_hits, "
        "e.final_assessment_valid_votes AS verdict_valid_votes FROM entries_confirmed_valid_votes e "
        "WHERE e.predatory_list_hits LIKE '%ugc_care_cloned%'"
    ).fetchall()
    rows = [r for r in rows if set((r["predatory_list_hits"] or "").split(",")) & set(COPY_LISTS.values())]
    keys = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    records = load_records(keys)
    missing = [k for k in keys if k not in records]
    if missing:
        raise SystemExit(f"{len(missing)} entries with a vote of a UGC-CARE copy list have no backend record; cannot continue")

    audit_keys = json.loads(table_analyses.AUDIT_KEYS_JSON.read_text())
    audited = {tuple(k) for k in audit_keys["confirmed_misattribution_keys"]}
    survivors = {tuple(k) for k in audit_keys["second_vote_survivor_keys"]}
    strict_excluded = {tuple(k) for k in audit_keys["strict_exclude_keys"]}
    broad_dropped = {tuple(r) for r in conn.execute("SELECT month, arxiv_id, suffix, entry_key FROM broad_drops")}
    invalid_map = build_invalid_backends_map(conn, cache_conn, audited)

    C: Counter = Counter()
    transitions: Counter = Counter()
    both_verdict: Counter = Counter()
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        rec = records[key]
        pred, legit = votes(rec)
        db_hits = set((r["predatory_list_hits"] or "").split(",")) - {""}
        assert pred == db_hits, "predatory-list votes of the record differ from the database column"
        cl = {g for g, b in COPY_LISTS.items() if b in pred}
        inc = {g for g, b in INCLUDED_LISTS.items() if b in legit}
        same = cl & inc                      # groups where the entry holds both votes of the same pair
        verdict = r["verdict_valid_votes"] if key in audited else r["final_assessment"]
        invalid = set(invalid_map.get(key, set())) if key in audited else set()

        # the rule applied without the invalidated votes must reproduce the verdict of the reference
        verdict_check = recompute_assessment_excluding(rec.get("backend_results") or [], invalid)
        assert verdict_check == verdict, f"recomputed verdict {verdict_check} != verdict {verdict}"

        in_strict = r["final_assessment"] == "predatory" and key not in strict_excluded
        in_broad = key not in broad_dropped and len(db_hits) > 0
        assert in_strict == (verdict == "predatory")

        C["with_cloned_vote"] += 1
        for g in ("1", "2"):
            C[f"cloned_g{g}"] += g in cl
            C[f"cloned_g{g}_and_included_same_group"] += g in same
        C["cloned_both_groups"] += len(cl) == 2
        C["with_cloned_and_any_included"] += bool(inc)
        C["with_cloned_and_same_group_included"] += bool(same)
        C["with_cloned_and_other_group_only_included"] += bool(inc) and not same
        C["no_included_vote"] += not inc
        if in_strict:
            C["strict_with_cloned"] += 1
            C["strict_with_cloned_and_same_group_included"] += bool(same)
            if key in survivors:
                C["survivors_with_cloned"] += 1
        if in_broad:
            C["broad_with_cloned"] += 1

        if same:
            both_verdict[verdict] += 1
            verdict_without = recompute_assessment_excluding(rec.get("backend_results") or [], invalid | {INCLUDED_LISTS[g] for g in same})
            transitions[f"{verdict}->{verdict_without}"] += 1
            C["pairs_checked"] += 1
            C["verdict_changed"] += verdict_without != verdict
            C["strict_lost"] += verdict == "predatory" and verdict_without != "predatory"
            C["strict_gained"] += verdict != "predatory" and verdict_without == "predatory"
            C["strict_gained_audited_entries"] += verdict != "predatory" and verdict_without == "predatory" and key in audited

    strict_after_check = conn.execute("SELECT COUNT(*) n FROM entries_confirmed WHERE final_assessment='predatory'").fetchone()["n"]
    broad_after_check = conn.execute(
        f"SELECT COUNT(*) n FROM entries_confirmed_all e WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED}"
    ).fetchone()["n"]
    assert strict_after_check > 0 and broad_after_check > 0, "empty population"
    strict_without_included = strict_after_check - C["strict_lost"] + C["strict_gained"]
    assert sum(both_verdict.values()) == C["with_cloned_and_same_group_included"] == C["pairs_checked"]
    assert sum(transitions.values()) == C["pairs_checked"]

    out = {
        "population_confirmed_cited": n_pop, "counts": dict(C),
        "verdicts_of_entries_with_both_votes": {v: both_verdict.get(v, 0) for v in sorted(set(VERDICTS) | set(both_verdict))},
        "verdict_transitions_without_included_vote": dict(transitions),
        "strict": {"with_included_votes": strict_after_check, "without_included_votes": strict_without_included},
        "broad": {"with_included_votes": broad_after_check},
    }
    stats_common.write_json("ugc_care_clone_pair_votes", out)
    print(json.dumps(out, indent=2))

    i = lambda v: {"kind": "int", "value": int(v)}  # noqa: E731
    numbers = {"uccdv_n_population": i(n_pop), "uccdv_n_with_cloned_vote": i(C["with_cloned_vote"]),
               "uccdv_n_cloned_both_groups": i(C["cloned_both_groups"]),
               "uccdv_n_with_cloned_and_any_included": i(C["with_cloned_and_any_included"]),
               "uccdv_n_with_cloned_and_same_group_included": i(C["with_cloned_and_same_group_included"]),
               "uccdv_n_with_cloned_and_other_group_only_included": i(C["with_cloned_and_other_group_only_included"]),
               "uccdv_n_no_included_vote": i(C["no_included_vote"])}
    for g in ("1", "2"):
        numbers[f"uccdv_{GROUP_NAME[g]}_n_cloned"] = i(C[f"cloned_g{g}"])
        numbers[f"uccdv_{GROUP_NAME[g]}_n_cloned_and_included_same_group"] = i(C[f"cloned_g{g}_and_included_same_group"])
    for v in VERDICTS:
        numbers[f"uccdv_both_votes_n_{v}"] = i(both_verdict.get(v, 0))
    numbers["uccdv_both_votes_n_other_verdict"] = i(sum(c for v, c in both_verdict.items() if v not in VERDICTS))
    for k in ("strict_with_cloned", "strict_with_cloned_and_same_group_included",
              "survivors_with_cloned", "broad_with_cloned",
              "verdict_changed", "strict_lost", "strict_gained", "strict_gained_audited_entries"):
        numbers[f"uccdv_n_{k}"] = i(C[k])
    numbers.update({"uccdv_strict_before_n": i(strict_after_check), "uccdv_strict_after_n": i(strict_without_included),
                    "uccdv_broad_before_n": i(broad_after_check)})
    stats_common.write_numbers("ugc_care_clone_pair_votes", numbers)


if __name__ == "__main__":
    main()
