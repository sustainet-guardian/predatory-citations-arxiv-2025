#!/usr/bin/env python3
"""Citation-level disciplinary predatory rates after the identity check: per group (Computer Science, Physics, Mathematics,
Other) the confirmed-cited entries, strict and broad predatory entries and their shares, entries per submission, and the
Mathematics broad-flag decomposition by list, with the number of distinct venues flagged. Reads EVAL_DB_PATH_BBL_TEX (through
identity_check_table_analyses.get_conn_after_identity_check()) and EVAL_SNAPSHOT_CATEGORIES_DB. Groups are assigned with
classify() of disciplinary_classification.py from the snapshot category database (every 2025 submission of the arXiv metadata
snapshot, made by build_snapshot_categories_db.py), not from the API database EVAL_ARXIV_CATEGORIES_DB (primary categories
reported by the arXiv API for the confirmed-cited submissions, made by fetch_arxiv_categories.py); the snapshot database is
used so that the groups are the same as in the other disciplinary tables, which need the submissions without confirmed-cited
entries. A submission without a category belongs to Other. Rates: the strict count of a group is the number of its
confirmed-cited entries (view entries_confirmed_all) with final_assessment = 'predatory' that are not in strict_exclude (the
strict population after the identity check), the broad count those with at least one predatory-list hit that is not dropped by
the identity check, and the denominator of every rate (disccite_*_strict_pct, disccite_math_broad_pct) is the number of
confirmed-cited entries of the group (per-discipline total). Writes
numbers/citation_level_disciplinary_rates_by_group.numbers.json (keys disccite_*) and JSON/CSV files in
EVAL_OUTPUT_DIR/output_citation_level_disciplinary_rates_by_group.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import stats_common
from stats_common import percentiles, pct, required_path_env, write_csv, write_json, write_numbers
import identity_check_table_analyses as table_analyses
from disciplinary_classification import classify, snapshot_categories_db

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_citation_level_disciplinary_rates_by_group"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    cat_conn = sqlite3.connect(str(snapshot_categories_db()))
    cat_conn.row_factory = sqlite3.Row
    groups_by_submission = {
        (r["month"], r["arxiv_id"]): classify(r["primary_category"])
        for r in cat_conn.execute("SELECT month, arxiv_id, primary_category FROM categories WHERE found=1").fetchall()
    }

    # is_strict: final_assessment 'predatory' and not in the strict exclusion table; is_broad: at least one list hit and not
    # among the dropped broad hits.
    rows = conn.execute(
        f"SELECT e.month AS month, e.arxiv_id AS arxiv_id, "
        f"CASE WHEN e.final_assessment='predatory' AND NOT EXISTS ("
        f"  SELECT 1 FROM strict_exclude x WHERE x.month=e.month AND x.arxiv_id=e.arxiv_id "
        f"  AND x.suffix=e.suffix AND x.entry_key=e.entry_key) THEN 1 ELSE 0 END AS is_strict, "
        f"CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED} "
        f"THEN 1 ELSE 0 END AS is_broad, "
        f"e.predatory_list_hits AS predatory_list_hits, "
        f"COALESCE(e.matched_venue_name, e.venue_raw) AS venue "
        f"FROM entries_confirmed_all e"
    ).fetchall()
    print(f"total confirmed-cited entries: {len(rows)}", flush=True)

    group_totals: dict[str, dict] = defaultdict(lambda: {"n_entries": 0, "n_strict": 0, "n_broad": 0})
    per_submission_counts: dict[str, Counter] = defaultdict(Counter)
    math_backend_counts: Counter = Counter()
    math_venue_broad_counts: Counter = Counter()
    n_no_group_data = 0

    for r in rows:
        key = (r["month"], r["arxiv_id"])
        group = groups_by_submission.get(key)
        if group is None:
            n_no_group_data += 1
            group = "Other"

        gt = group_totals[group]
        gt["n_entries"] += 1
        is_strict = bool(r["is_strict"])
        is_broad = bool(r["is_broad"])
        if is_strict:
            gt["n_strict"] += 1
        if is_broad:
            gt["n_broad"] += 1

        per_submission_counts[group][key] += 1

        if group == "Mathematics":
            if is_broad and r["predatory_list_hits"]:
                for backend in r["predatory_list_hits"].split(","):
                    math_backend_counts[backend] += 1
                if r["venue"]:
                    math_venue_broad_counts[r["venue"]] += 1

    print(f"entries with no category data for their submission: {n_no_group_data}", flush=True)

    # Each submission belongs to exactly one group: the per-group submission counts add up to the number of submissions.
    total_subs_all_groups = sum(len(c) for c in per_submission_counts.values())
    n_submissions = len({(r["month"], r["arxiv_id"]) for r in rows})
    assert total_subs_all_groups == n_submissions, (
        f"per-group submission counts add up to {total_subs_all_groups}, but there are {n_submissions} submissions"
    )

    citation_level_rows = []
    for group, gt in sorted(group_totals.items(), key=lambda kv: -kv[1]["n_entries"]):
        counts = list(per_submission_counts[group].values())
        mean_per_sub = round(sum(counts) / len(counts), 2) if counts else None
        p = percentiles([float(c) for c in counts]) if counts else {}
        citation_level_rows.append({
            "group": group,
            "n_confirmed_cited_entries": gt["n_entries"],
            "n_submissions": len(counts),
            "mean_entries_per_submission": mean_per_sub,
            "median_entries_per_submission": p.get(50),
            "n_strict_predatory_entries": gt["n_strict"],
            "citation_level_strict_pct": pct(gt["n_strict"], gt["n_entries"]),
            "n_broad_predatory_entries": gt["n_broad"],
            "citation_level_broad_pct": pct(gt["n_broad"], gt["n_entries"]),
        })

    total_entries = sum(gt["n_entries"] for gt in group_totals.values())
    total_strict = sum(gt["n_strict"] for gt in group_totals.values())
    total_broad = sum(gt["n_broad"] for gt in group_totals.values())

    math_total_broad = group_totals["Mathematics"]["n_broad"]
    math_backend_breakdown = [
        {"backend": b, "n_broad_flagged_entries": n, "pct_of_math_broad": pct(n, math_total_broad)}
        for b, n in math_backend_counts.most_common()
    ]

    out = {
        "corpus_wide_citation_level": {
            "n_entries": total_entries,
            "n_strict": total_strict,
            "strict_pct": pct(total_strict, total_entries),
            "n_broad": total_broad,
            "broad_pct": pct(total_broad, total_entries),
        },
        "citation_level_by_group": citation_level_rows,
        "mathematics_decomposition": {
            "n_broad_flagged_entries": math_total_broad,
            "n_strict_flagged_entries": group_totals["Mathematics"]["n_strict"],
            "backend_breakdown_of_broad_flags": math_backend_breakdown,
            "n_distinct_venues_flagged_broad": len(math_venue_broad_counts),
        },
    }

    write_json("citation_level_disciplinary_rates", out)
    write_csv("citation_level_disciplinary_rates_by_group", citation_level_rows, list(citation_level_rows[0].keys()))
    if math_backend_breakdown:
        write_csv("mathematics_broad_backend_breakdown", math_backend_breakdown, list(math_backend_breakdown[0].keys()))

    print(json.dumps(out, indent=2, default=str))

    GROUP_KEYS = {"Computer Science": "cs", "Other": "other", "Mathematics": "math", "Physics": "physics"}
    numbers_payload = {}
    for group, key in GROUP_KEYS.items():
        gt = group_totals[group]
        numbers_payload[f"disccite_{key}_strict_pct"] = {
            "kind": "ratio", "num": gt["n_strict"], "den": gt["n_entries"], "decimals": 3,
        }
    gt = group_totals["Mathematics"]
    numbers_payload["disccite_math_broad_pct"] = {
        "kind": "ratio", "num": gt["n_broad"], "den": gt["n_entries"], "decimals": 3,
    }
    write_numbers("citation_level_disciplinary_rates_by_group", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
