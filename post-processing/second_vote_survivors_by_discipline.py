#!/usr/bin/env python3
"""Assigns the second-vote survivors (strict-predatory references that keep their verdict only through a second
vote of a backend built from the same pages as another list, after the invalid votes of the identity check are removed) to their submissions and discipline groups.
Per group: submissions, survivor citations and submissions, survivor submissions that lose the strict flag, and the strict
submission counts with and without the survivors. Groups come from classify() of disciplinary_classification.py on the primary
category in the snapshot category database EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission of the arXiv metadata snapshot,
made by build_snapshot_categories_db.py; no category row: "Other"). The API database EVAL_ARXIV_CATEGORIES_DB
(fetch_arxiv_categories.py, primary categories for the confirmed-cited submissions only) is not used, so that the groups are the
same as in the other disciplinary tables. Reads EVAL_DB_PATH_BBL_TEX and the audit keys file under EVAL_OUTPUT_DIR. Writes
second_vote_survivors_by_discipline.json to EVAL_OUTPUT_DIR/output_second_vote_survivors_by_discipline and
numbers/second_vote_survivors_by_discipline.numbers.json (keys survivorgroups_*); counts only."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import stats_common
import identity_check_table_analyses as table_analyses
from disciplinary_classification import classify, snapshot_categories_db

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_second_vote_survivors_by_discipline"
stats_common.OUTPUT_DIR = OUTPUT_DIR

GROUP_KEY = {"Computer Science": "cs", "Other": "other", "Mathematics": "math", "Physics": "physics"}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()
    cat_conn = sqlite3.connect(f"file:{snapshot_categories_db()}?mode=ro", uri=True)

    audit = json.loads(table_analyses.AUDIT_KEYS_JSON.read_text())
    survivors = [tuple(k) for k in audit["second_vote_survivor_keys"]]
    assert len(survivors) > 0, "no second-vote survivors in the audit keys file"
    for k in survivors:  # each must be a strict-predatory citation after the identity check
        n = conn.execute(
            "SELECT COUNT(*) n FROM entries_confirmed WHERE month=? AND arxiv_id=? AND suffix=? AND entry_key=? "
            "AND final_assessment='predatory'", k
        ).fetchone()["n"]
        assert n == 1, "a survivor is not among the strict references after the identity check"

    strict_by_sub: dict[tuple, int] = Counter()
    for r in conn.execute(
        "SELECT month, arxiv_id, SUM(CASE WHEN final_assessment='predatory' THEN 1 ELSE 0 END) n_strict "
        "FROM entries_confirmed GROUP BY month, arxiv_id"
    ):
        strict_by_sub[(r["month"], r["arxiv_id"])] = r["n_strict"]
    # population of the table: submissions with a confirmed-cited entry (entries_confirmed_all)
    all_subs = {(r["month"], r["arxiv_id"]) for r in conn.execute("SELECT DISTINCT month, arxiv_id FROM entries_confirmed_all")}
    assert len(all_subs) > 0, "no submission with a confirmed-cited entry"

    category_by_key = {
        (month, arxiv_id): primary_category
        for month, arxiv_id, primary_category in cat_conn.execute(
            "SELECT month, arxiv_id, primary_category FROM categories WHERE found=1")
    }
    group_of = lambda sub: GROUP_KEY[classify(category_by_key.get(sub))]  # noqa: E731

    survivors_in_sub = Counter((k[0], k[1]) for k in survivors)
    n_sub = Counter(group_of(s) for s in all_subs)
    strict_with = Counter(group_of(s) for s in all_subs if strict_by_sub[s] > 0)
    strict_without = Counter(group_of(s) for s in all_subs if strict_by_sub[s] - survivors_in_sub.get(s, 0) > 0)
    n_cit = Counter(group_of((k[0], k[1])) for k in survivors)
    n_survivor_subs = Counter(group_of(s) for s in survivors_in_sub)
    n_lose_flag = Counter(group_of(s) for s, c in survivors_in_sub.items() if strict_by_sub[s] - c == 0)
    in_found = sum(1 for s in survivors_in_sub if s in category_by_key)
    assert sum(n_sub.values()) == len(all_subs)

    out = {
        "n_survivors": len(survivors), "n_survivor_submissions": len(survivors_in_sub),
        "survivor_submissions_with_a_category_row": in_found,
        "by_group": {
            g: {
                "n_submissions": n_sub[g], "n_survivor_citations": n_cit[g],
                "n_survivor_submissions": n_survivor_subs[g],
                "n_survivor_submissions_that_lose_the_strict_flag": n_lose_flag[g],
                "strict_submissions_with": strict_with[g], "strict_submissions_without": strict_without[g],
                "strict_pct_with": round(100 * strict_with[g] / n_sub[g], 4),
                "strict_pct_without": round(100 * strict_without[g] / n_sub[g], 4),
            }
            for g in ("cs", "physics", "math", "other")
        },
    }
    stats_common.write_json("second_vote_survivors_by_discipline", out)
    print(json.dumps(out, indent=2))
    assert sum(n_cit.values()) == len(survivors) and sum(strict_with.values()) == sum(1 for v in strict_by_sub.values() if v > 0)

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    numbers = {}
    for g in ("cs", "physics", "math", "other"):
        numbers[f"survivorgroups_{g}_n_citations"] = i(n_cit[g])
        numbers[f"survivorgroups_{g}_n_survivor_submissions"] = i(n_survivor_subs[g])
        numbers[f"survivorgroups_{g}_n_lose_strict_flag"] = i(n_lose_flag[g])
        numbers[f"survivorgroups_{g}_n_submissions"] = i(n_sub[g])
        numbers[f"survivorgroups_{g}_strict_n_with"] = i(strict_with[g])
        numbers[f"survivorgroups_{g}_strict_n_without"] = i(strict_without[g])
    stats_common.write_numbers("second_vote_survivors_by_discipline", numbers)


if __name__ == "__main__":
    main()
