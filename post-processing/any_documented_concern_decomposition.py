#!/usr/bin/env python3
"""Partitions the submissions flagged under "any documented concern" into the seven mutually exclusive combinations of the three
criteria (broad-predatory citation, hijacked-identity flag, elevated retraction-risk tier), after the identity check.
Reads EVAL_DB_PATH_BBL_TEX and, in EVAL_OUTPUT_DIR, the audit keys, the retraction-risk basis CSV and
identity_audit_citation_detail.json (through identity_check_headline_analyses.py); the flagged total must equal the one of
any_concern_submissions() in that script. The submissions are those of the view entries_confirmed_all (all confirmed-cited
entries, stored verdicts) with the broad flags that the identity check drops removed; the strict and the legitimate
populations do not occur. Writes any_documented_concern_decomposition.json in EVAL_OUTPUT_DIR/output_any_documented_concern_decomposition and
numbers/any_documented_concern_decomposition.numbers.json (keys concern_*)."""
from __future__ import annotations

import json
from collections import Counter

import identity_check_headline_analyses as headline_analyses
import stats_common
from stats_common import required_path_env

OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_any_documented_concern_decomposition"
CITATION_DETAIL_PATH = required_path_env("EVAL_OUTPUT_DIR") / "identity_audit_citation_detail.json"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn, _ = headline_analyses.get_conn_with_exclusions()
    per_citation = json.loads(CITATION_DETAIL_PATH.read_text())
    headline_analyses.setup_broad_drops_table(conn, per_citation)

    # any_concern_submissions() gives the "any flag" total that the seven categories must sum to.
    any_concern = headline_analyses.any_concern_submissions(conn)
    n_papers = any_concern["n_papers"]
    n_any_flag_expected = any_concern["any_flag_after_n"]

    venue_basis = headline_analyses.venue_retraction_basis()

    flagged = conn.execute(
        "SELECT month, arxiv_id, retraction_risk_level, COALESCE(matched_venue_name, venue_raw) AS venue "
        "FROM entries_confirmed_all WHERE retraction_risk_level IN ('moderate','high','critical')"
    ).fetchall()
    tier_drop_by_submission: Counter = Counter()
    for r in flagged:
        basis = venue_basis.get(r["venue"])
        if basis is None:
            continue
        tier_without_recent_rate = headline_analyses.tier_without_recent_rate_below_min_publications(**basis)
        if r["retraction_risk_level"] in headline_analyses.FLAGGED_RETRACTION_TIERS and tier_without_recent_rate not in headline_analyses.FLAGGED_RETRACTION_TIERS:
            tier_drop_by_submission[(r["month"], r["arxiv_id"])] += 1

    papers = conn.execute(
        """
        SELECT month, arxiv_id,
               SUM(CASE WHEN is_hijacked='1' THEN 1 ELSE 0 END) n_hijacked,
               SUM(CASE WHEN retraction_risk_level IN ('critical','high','moderate') THEN 1 ELSE 0 END) n_retraction_flagged
        FROM entries_confirmed_all GROUP BY month, arxiv_id
        """
    ).fetchall()

    n_broad_by_submission = {
        (r["month"], r["arxiv_id"]): r["n_broad"]
        for r in conn.execute(
            """
            SELECT e.month AS month, e.arxiv_id AS arxiv_id,
                   SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS (
                       SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id
                       AND b.suffix=e.suffix AND b.entry_key=e.entry_key
                   ) THEN 1 ELSE 0 END) n_broad
            FROM entries_confirmed_all e GROUP BY e.month, e.arxiv_id
            """
        ).fetchall()
    }

    category_counts: Counter = Counter()
    for p in papers:
        key = (p["month"], p["arxiv_id"])
        broad_flag = n_broad_by_submission.get(key, 0) > 0
        hijacked_flag = p["n_hijacked"] > 0
        retracted_flag = (p["n_retraction_flagged"] - tier_drop_by_submission.get(key, 0)) > 0

        n_true = sum([broad_flag, hijacked_flag, retracted_flag])
        if n_true == 0:
            category_counts["none"] += 1
        elif n_true == 1:
            if broad_flag:
                category_counts["broad_only"] += 1
            elif hijacked_flag:
                category_counts["hijacked_only"] += 1
            else:
                category_counts["retraction_only"] += 1
        elif n_true == 2:
            if broad_flag and hijacked_flag:
                category_counts["broad_and_hijacked"] += 1
            elif broad_flag and retracted_flag:
                category_counts["broad_and_retraction"] += 1
            else:
                category_counts["hijacked_and_retraction"] += 1
        else:
            category_counts["all_three"] += 1

    seven_categories = [
        "broad_only", "hijacked_only", "retraction_only",
        "broad_and_hijacked", "broad_and_retraction", "hijacked_and_retraction",
        "all_three",
    ]
    n_any_flag = sum(category_counts[c] for c in seven_categories)
    assert n_any_flag == n_any_flag_expected, (
        f"decomposition's 'any flag' sum ({n_any_flag}) != any_concern_submissions() any_flag_after_n ({n_any_flag_expected})"
    )
    print(f"[after identity check] flagged submissions: {n_any_flag}; total submissions: {n_papers}", flush=True)
    assert n_any_flag + category_counts["none"] == n_papers

    dominant = max(seven_categories, key=lambda c: category_counts[c])
    single_criterion_totals = {
        "broad": category_counts["broad_only"] + category_counts["broad_and_hijacked"]
        + category_counts["broad_and_retraction"] + category_counts["all_three"],
        "hijacked": category_counts["hijacked_only"] + category_counts["broad_and_hijacked"]
        + category_counts["hijacked_and_retraction"] + category_counts["all_three"],
        "retraction": category_counts["retraction_only"] + category_counts["broad_and_retraction"]
        + category_counts["hijacked_and_retraction"] + category_counts["all_three"],
    }
    dominant_criterion = max(single_criterion_totals, key=single_criterion_totals.get)

    out = {
        "n_papers": n_papers,
        "n_any_flag": n_any_flag,
        "n_no_flag": category_counts["none"],
        "seven_way_breakdown": {c: category_counts[c] for c in seven_categories},
        "pct_of_flagged": {c: headline_analyses.pct(category_counts[c], n_any_flag) for c in seven_categories},
        "single_criterion_totals_including_overlaps": single_criterion_totals,
        "dominant_exclusive_category": dominant,
        "dominant_criterion_including_overlaps": dominant_criterion,
        "dominant_criterion_pct_of_flagged": headline_analyses.pct(single_criterion_totals[dominant_criterion], n_any_flag),
    }
    stats_common.write_json("any_documented_concern_decomposition", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("any_documented_concern_decomposition", {
        "concern_n_papers": {"kind": "int", "value": n_papers},
        "concern_n_any_flag": {"kind": "int", "value": n_any_flag},
        "concern_n_broad_only": {"kind": "int", "value": category_counts["broad_only"]},
        "concern_n_hijacked_only": {"kind": "int", "value": category_counts["hijacked_only"]},
        "concern_n_retraction_only": {"kind": "int", "value": category_counts["retraction_only"]},
        "concern_n_broad_total_incl_overlaps": {"kind": "int", "value": single_criterion_totals["broad"]},
        "concern_n_hijacked_total_incl_overlaps": {"kind": "int", "value": single_criterion_totals["hijacked"]},
        "concern_pct_retraction_of_flagged": {"kind": "ratio", "num": single_criterion_totals["retraction"], "den": n_any_flag, "decimals": 1},
    })


if __name__ == "__main__":
    main()
