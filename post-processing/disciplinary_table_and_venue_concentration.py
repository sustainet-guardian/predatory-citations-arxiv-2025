#!/usr/bin/env python3
"""Per-discipline table of submissions with at least one strict-predatory and one broad-predatory reference (Wilson 95%
intervals, mean entries per submission), venue concentration of the strict-predatory and of the legitimate citations, and
the number of audited citations whose verdict on the valid votes is legitimate. Disciplines come from classify() of
disciplinary_classification.py on the snapshot category database EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission of the arXiv
metadata snapshot, made by build_snapshot_categories_db.py), not from the API database EVAL_ARXIV_CATEGORIES_DB
(fetch_arxiv_categories.py: primary categories for the confirmed-cited submissions only); the snapshot database is the one the
disciplinary tables use for all 2025 submissions. Ratios: disc_*_strict_pct and disc_*_broad_pct are the submissions of a
discipline with at least one strict (after the identity check: final_assessment = 'predatory' without the strict_exclude
keys) or broad citation over all its submissions with a confirmed-cited entry (per-discipline total);
legitreclassified_pct is the number of audited citations whose recomputed (valid-vote) verdict is legitimate over the entries of
entries_confirmed_valid_votes with final_assessment_valid_votes = 'legitimate' (the legitimate population of the valid-vote
verdict). Reads EVAL_DB_PATH_BBL_TEX
(through identity_check_table_analyses.py) and output_disciplinary_submission_counts/disciplinary_submission_counts.json in
EVAL_OUTPUT_DIR. Writes disciplinary_table.json, venue_concentration.json and venue_concentration_summary.json to
EVAL_OUTPUT_DIR/output_disciplinary_table_and_venue_concentration, and
numbers/disciplinary_table_and_venue_concentration.numbers.json (keys disc_*, conc_*, legitreclassified_*)."""
from __future__ import annotations

import json
import sqlite3

import stats_common
import identity_check_table_analyses as table_analyses
import identity_check_headline_analyses as headline_analyses
from disciplinary_classification import classify, snapshot_categories_db

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_disciplinary_table_and_venue_concentration"
stats_common.OUTPUT_DIR = OUTPUT_DIR

SUBMISSION_COUNTS_JSON = (
    stats_common.required_path_env("EVAL_OUTPUT_DIR")
    / "output_disciplinary_submission_counts"
    / "disciplinary_submission_counts.json"
)


def mean_entries_per_submission(conn, cat_conn) -> dict[str, float]:
    """Mean number of confirmed-cited entries (all assessments) per submission, by discipline."""
    rows = conn.execute(
        "SELECT month, arxiv_id, COUNT(*) n FROM entries_confirmed_all GROUP BY month, arxiv_id"
    ).fetchall()
    category_by_key = {
        (r["month"], r["arxiv_id"]): r["primary_category"]
        for r in cat_conn.execute(
            "SELECT month, arxiv_id, primary_category FROM categories WHERE found=1"
        ).fetchall()
    }
    totals: dict[str, int] = {}
    counts: dict[str, int] = {}
    for r in rows:
        g = classify(category_by_key.get((r["month"], r["arxiv_id"])))
        totals[g] = totals.get(g, 0) + r["n"]
        counts[g] = counts.get(g, 0) + 1
    means = {g: round(totals[g] / counts[g], 1) for g in totals}
    means["Corpus-wide"] = round(sum(totals.values()) / sum(counts.values()), 1)
    return means


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()
    cat_conn = sqlite3.connect(str(snapshot_categories_db()))
    cat_conn.row_factory = sqlite3.Row

    # --- disciplinary table ---
    discipline_rates = headline_analyses.discipline_submission_rates(conn, cat_conn)
    means = mean_entries_per_submission(conn, cat_conn)

    submission_counts = json.loads(SUBMISSION_COUNTS_JSON.read_text())
    assert discipline_rates["Corpus-wide"]["n_submissions"] == submission_counts["n_submissions_total"], (
        f"discipline table Corpus-wide n_submissions {discipline_rates['Corpus-wide']['n_submissions']} != "
        f"disciplinary_submission_counts's {submission_counts['n_submissions_total']}"
    )
    assert discipline_rates["Corpus-wide"]["strict_after_n"] == submission_counts["n_strict"], (
        f"discipline table Corpus-wide strict_after_n {discipline_rates['Corpus-wide']['strict_after_n']} != "
        f"disciplinary_submission_counts's {submission_counts['n_strict']}"
    )
    assert discipline_rates["Corpus-wide"]["broad_after_n"] == submission_counts["n_broad"], (
        f"discipline table Corpus-wide broad_after_n {discipline_rates['Corpus-wide']['broad_after_n']} != "
        f"disciplinary_submission_counts's {submission_counts['n_broad']}"
    )

    table_output = {"per_discipline": discipline_rates, "mean_entries_per_submission": means}
    stats_common.write_json("disciplinary_table", table_output)
    print(json.dumps(table_output, indent=2, default=str))

    numbers_payload = {}
    GROUP_KEYS = {"Computer Science": "cs", "Other": "other", "Mathematics": "math", "Physics": "physics", "Corpus-wide": "corpuswide"}
    for g, key in GROUP_KEYS.items():
        v = discipline_rates[g]
        numbers_payload[f"disc_{key}_n_submissions"] = {"kind": "int", "value": v["n_submissions"]}
        numbers_payload[f"disc_{key}_pct_of_corpus"] = {
            "kind": "ratio", "num": v["n_submissions"], "den": discipline_rates["Corpus-wide"]["n_submissions"], "decimals": 1,
        }
        numbers_payload[f"disc_{key}_mean_entries"] = {"kind": "float", "value": means[g], "decimals": 1}
        if g != "Corpus-wide":
            numbers_payload[f"disc_{key}_strict_n"] = {"kind": "int", "value": v["strict_after_n"]}
        numbers_payload[f"disc_{key}_strict_pct"] = {
            "kind": "ratio", "num": v["strict_after_n"], "den": v["n_submissions"], "decimals": 2,
        }
        numbers_payload[f"disc_{key}_strict_ci_lo"] = {"kind": "float", "value": v["strict_after_ci"][0], "decimals": 2}
        numbers_payload[f"disc_{key}_strict_ci_hi"] = {"kind": "float", "value": v["strict_after_ci"][1], "decimals": 2}
        numbers_payload[f"disc_{key}_broad_pct"] = {
            "kind": "ratio", "num": v["broad_after_n"], "den": v["n_submissions"], "decimals": 2,
        }
        numbers_payload[f"disc_{key}_broad_ci_lo"] = {"kind": "float", "value": v["broad_after_ci"][0], "decimals": 2}
        numbers_payload[f"disc_{key}_broad_ci_hi"] = {"kind": "float", "value": v["broad_after_ci"][1], "decimals": 2}

    # --- venue concentration, strict-predatory and legitimate populations ---
    strict_conc = headline_analyses.concentration_measures(conn, "entries_confirmed", "final_assessment='predatory'", "strict_predatory_after_identity_check")
    legit_conc = headline_analyses.concentration_measures(
        conn, "entries_confirmed_valid_votes", "final_assessment_valid_votes='legitimate'", "legitimate_after_identity_check"
    )
    concentration_output = {"strict_predatory": strict_conc, "legitimate": legit_conc}
    stats_common.write_json("venue_concentration", concentration_output)
    print(json.dumps(concentration_output, indent=2, default=str))

    # Word labels instead of digits ("top10", "50pct"): digits are stripped from macro names and the keys would collide.
    for label, conc in (("strict", strict_conc), ("legit", legit_conc)):
        numbers_payload[f"conc_{label}_distinct_venues"] = {"kind": "int", "value": conc["distinct_venues"]}
        if label == "legit":
            numbers_payload[f"conc_{label}_total_citations"] = {"kind": "int", "value": conc["total_citations"]}
        numbers_payload[f"conc_{label}_topten_pct"] = {"kind": "float", "value": conc["top10_pct_of_total"], "decimals": 1}
        numbers_payload[f"conc_{label}_topfifty_pct"] = {"kind": "float", "value": conc["top50_pct_of_total"], "decimals": 1}
        numbers_payload[f"conc_{label}_hhi"] = {"kind": "float", "value": conc["hhi"], "decimals": 3}
        numbers_payload[f"conc_{label}_effective_venues"] = {"kind": "float", "value": conc["effective_venues"], "decimals": 0}
        numbers_payload[f"conc_{label}_gini"] = {"kind": "float", "value": conc["gini"], "decimals": 2}
        numbers_payload[f"conc_{label}_venues_for_fiftypct_n"] = {"kind": "int", "value": conc["venues_for_50pct"]["n"]}
        numbers_payload[f"conc_{label}_venues_for_fiftypct_pct"] = {
            "kind": "float", "value": conc["venues_for_50pct"]["pct_of_distinct_venues"], "decimals": 1,
        }
        numbers_payload[f"conc_{label}_venues_for_ninetypct_n"] = {"kind": "int", "value": conc["venues_for_90pct"]["n"]}
        numbers_payload[f"conc_{label}_venues_for_ninetypct_pct"] = {
            "kind": "float", "value": conc["venues_for_90pct"]["pct_of_distinct_venues"], "decimals": 1,
        }

    # --- audited citations whose verdict on the valid votes is legitimate ---
    n_audited_legitimate = conn.execute(
        "SELECT COUNT(*) n FROM valid_vote_verdict WHERE verdict_valid_votes='legitimate'"
    ).fetchone()["n"]

    n_legitimate_total = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE final_assessment_valid_votes='legitimate'"
    ).fetchone()["n"]
    summary_output = {
        "audited_citations_with_legitimate_verdict": {
            "n_citations": n_audited_legitimate,
            "legitimate_population_total": n_legitimate_total,
            "pct_of_legitimate_population": headline_analyses.pct(n_audited_legitimate, n_legitimate_total),
        },
    }
    stats_common.write_json("venue_concentration_summary", summary_output)
    print(json.dumps(summary_output, indent=2, default=str))

    numbers_payload["legitreclassified_n"] = {"kind": "int", "value": n_audited_legitimate}
    numbers_payload["legitreclassified_pct"] = {
        "kind": "ratio", "num": n_audited_legitimate, "den": n_legitimate_total, "decimals": 3,
    }

    stats_common.write_numbers("disciplinary_table_and_venue_concentration", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
