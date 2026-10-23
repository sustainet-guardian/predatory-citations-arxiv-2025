#!/usr/bin/env python3
"""Computes the distribution of final assessments over the population after the identity check (a citation whose venue was
attributed to the wrong identity keeps its row and is assessed on its remaining valid votes), the number of citations that
satisfy each of two alternative vote-threshold definitions of strict-predatory (at least 2 predatory votes; predatory votes >=
legitimate votes), the hypothetical strict counts for fixed assumed predatory rates among unknown-venue citations, and the
Journal of Scientific Computing citation counts. The assessments partition the population (legitimate, not applicable,
unknown, suspicious, predatory (strict), insufficient data); the hijacked count is a separate flag that overlaps with them, and
the strict count is written by other scripts. The headline_*_pct ratios divide the rows with a verdict by all rows of entries_confirmed_valid_votes; the
verdict is the one recomputed without the invalid votes (final_assessment_valid_votes), so headline_legitimate_pct is the share
of 'legitimate' in that sense. Reads EVAL_DB_PATH_BBL_TEX through identity_check_table_analyses.get_conn_after_identity_check(). Writes
numbers/headline_distribution_and_vote_rules.numbers.json (keys headline_*, strictalt_*, unknownsens_*, jsc_*) and
headline_distribution_and_vote_rules.json in EVAL_OUTPUT_DIR/output_headline_distribution_and_vote_rules.
"""
from __future__ import annotations

import json

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_headline_distribution_and_vote_rules"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def final_assessment_distribution(conn) -> dict:
    """Final-assessment distribution over entries_confirmed_valid_votes: each citation carries
    final_assessment_valid_votes (its verdict with the invalid votes of the identity check removed), so the rows sum to the total."""
    total = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_valid_votes").fetchone()["n"]
    rows = conn.execute(
        "SELECT final_assessment_valid_votes, COUNT(*) n FROM entries_confirmed_valid_votes "
        "GROUP BY final_assessment_valid_votes ORDER BY n DESC"
    ).fetchall()
    breakdown = {(r["final_assessment_valid_votes"] or "not_applicable"): r["n"] for r in rows}
    assert sum(breakdown.values()) == total

    n_hijacked = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE is_hijacked='1'").fetchone()["n"]
    # Broad-predatory: at least one list hit, excluding the entries in broad_drops.
    n_broad = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
        "AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
    ).fetchone()["n"]

    return {
        "total": total,
        "legitimate": breakdown.get("legitimate", 0),
        "not_applicable": breakdown.get("not_applicable", 0),
        "unknown": breakdown.get("unknown", 0),
        "suspicious": breakdown.get("suspicious", 0),
        "predatory_strict": breakdown.get("predatory", 0),
        "insufficient_data": breakdown.get("insufficient_data", 0),
        "predatory_broad": n_broad,
        "hijacked": n_hijacked,
        "counts_by_assessment": breakdown,
    }


def vote_threshold_alternatives(conn) -> dict:
    """Number of citations that satisfy each vote-threshold definition, over the whole population."""

    def threshold_count(where_clause: str) -> int:
        return conn.execute(f"SELECT COUNT(*) n FROM entries_confirmed_all WHERE {where_clause}").fetchone()["n"]

    return {
        "ge2_predatory_votes": threshold_count("CAST(predatory_votes AS INTEGER) >= 2"),
        "votes_ge_legitimate": threshold_count(
            "CAST(predatory_votes AS INTEGER) >= CAST(legitimate_votes AS INTEGER) AND CAST(predatory_votes AS INTEGER) >= 1"
        ),
    }


def unknown_rate_sensitivity(strict_count: int, unknown_count: int) -> dict:
    """strict_count + round(assumed_rate * unknown_count) for each fixed assumed rate."""
    rates = [0.0, 0.0001, 0.0005, 0.001, 0.005, 0.01]
    out = {}
    for rate in rates:
        hypothetical = strict_count + round(rate * unknown_count)
        out[f"rate_{rate}"] = {
            "assumed_pct": round(rate * 100, 2),
            "hypothetical_strict_count": hypothetical,
            "multiple_of_observed": round(hypothetical / strict_count, 1),
        }
    return out


def jsc_citation_counts(conn) -> dict:
    """Citations to Journal of Scientific Computing: total and flagged solely by the Algerian ministry list.
    Matched by venue name (matched_venue_name, falling back to venue_raw)."""
    n_total_citations_to_venue = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e "
        "WHERE COALESCE(e.matched_venue_name, e.venue_raw) = 'Journal of Scientific Computing'"
    ).fetchone()["n"]
    n_flagged_solely_by_algerian = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e "
        "WHERE COALESCE(e.matched_venue_name, e.venue_raw) = 'Journal of Scientific Computing' "
        "AND e.predatory_list_hits = 'algerian_ministry'"
    ).fetchone()["n"]
    return {
        "n_total_citations_to_venue": n_total_citations_to_venue,
        "n_flagged_solely_by_algerian_ministry": n_flagged_solely_by_algerian,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    distribution = final_assessment_distribution(conn)
    print("final_assessment_distribution:", json.dumps(distribution, indent=2, default=str), flush=True)

    thresholds = vote_threshold_alternatives(conn)
    print("vote_threshold_alternatives:", json.dumps(thresholds, indent=2), flush=True)

    unknown_rates = unknown_rate_sensitivity(distribution["predatory_strict"], distribution["unknown"])
    print("unknown_rate_sensitivity:", json.dumps(unknown_rates, indent=2), flush=True)

    jsc = jsc_citation_counts(conn)
    print("jsc_citation_counts:", json.dumps(jsc, indent=2), flush=True)

    out = {
        "final_assessment_distribution": distribution,
        "vote_threshold_alternatives": thresholds,
        "unknown_rate_sensitivity": unknown_rates,
        "jsc_citation_counts": jsc,
    }
    stats_common.write_json("headline_distribution_and_vote_rules", out)

    numbers_payload = {
        "headline_total": {"kind": "int", "value": distribution["total"]},
        "headline_legitimate_n": {"kind": "int", "value": distribution["legitimate"]},
        "headline_legitimate_pct": {"kind": "ratio", "num": distribution["legitimate"], "den": distribution["total"], "decimals": 1},
        "headline_not_applicable_n": {"kind": "int", "value": distribution["not_applicable"]},
        "headline_not_applicable_pct": {"kind": "ratio", "num": distribution["not_applicable"], "den": distribution["total"], "decimals": 1},
        "headline_unknown_n": {"kind": "int", "value": distribution["unknown"]},
        "headline_unknown_pct": {"kind": "ratio", "num": distribution["unknown"], "den": distribution["total"], "decimals": 1},
        "headline_suspicious_n": {"kind": "int", "value": distribution["suspicious"]},
        "headline_suspicious_pct": {"kind": "ratio", "num": distribution["suspicious"], "den": distribution["total"], "decimals": 1},
        "headline_insufficient_data_n": {"kind": "int", "value": distribution["insufficient_data"]},
        "headline_insufficient_data_pct": {"kind": "ratio", "num": distribution["insufficient_data"], "den": distribution["total"], "decimals": 3},
        "headline_hijacked_n": {"kind": "int", "value": distribution["hijacked"]},
        "headline_hijacked_pct": {"kind": "ratio", "num": distribution["hijacked"], "den": distribution["total"], "decimals": 3},

        "strictalt_ge2_votes_n": {"kind": "int", "value": thresholds["ge2_predatory_votes"]},
        "strictalt_votes_ge_legit_n": {"kind": "int", "value": thresholds["votes_ge_legitimate"]},

        "jsc_n_total_citations": {"kind": "int", "value": jsc["n_total_citations_to_venue"]},
        "jsc_n_flagged_solely_algerian": {"kind": "int", "value": jsc["n_flagged_solely_by_algerian_ministry"]},
    }
    # Word labels instead of the decimal rate: the macro-name conversion strips digits, so the decimal forms would collide.
    # The keys of unknown_rates are the assumed fractions of the unknown-venue citations that are predatory; the labels name
    # them in percent:
    #   key         assumed percent  label
    #   rate_0.0     0               zero
    #   rate_0.0001  0.01            onehundredth    (one hundredth of a percent)
    #   rate_0.0005  0.05            fivehundredths
    #   rate_0.001   0.1             onetenth
    #   rate_0.005   0.5             onehalf
    #   rate_0.01    1               onepercent
    RATE_LABELS = {
        "rate_0.0": "zero",
        "rate_0.0001": "onehundredth",
        "rate_0.0005": "fivehundredths",
        "rate_0.001": "onetenth",
        "rate_0.005": "onehalf",
        "rate_0.01": "onepercent",
    }
    for key, entry in unknown_rates.items():
        rate_label = RATE_LABELS[key]
        numbers_payload[f"unknownsens_{rate_label}_count"] = {"kind": "int", "value": entry["hypothetical_strict_count"]}
        numbers_payload[f"unknownsens_{rate_label}_multiple"] = {"kind": "float", "value": entry["multiple_of_observed"], "decimals": 1}

    stats_common.write_numbers("headline_distribution_and_vote_rules", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
