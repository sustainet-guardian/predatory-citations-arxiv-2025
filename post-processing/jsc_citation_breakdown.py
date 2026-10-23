#!/usr/bin/env python3
"""Breakdown of the Journal of Scientific Computing citations (Springer; formerly Kluwer) that are flagged broad-predatory after the
identity check: legitimate-vs-predatory vote split, the number of flagged citations whose only list hit is the Algerian ministry
list, and the classification by DOI prefix (10.1007 Springer, 10.1023 Kluwer) and by ISSN (the ISSNs of the journal, and ISSN
1524-2560 of the site that copies it).
Reads EVAL_DB_PATH_BBL_TEX through identity_check_table_analyses.py and numbers/headline_distribution_and_vote_rules.numbers.json
(cross-check of the totals). Writes jsc_citation_breakdown.json to
EVAL_OUTPUT_DIR/output_jsc_citation_breakdown and numbers/jsc_citation_breakdown.numbers.json (keys jscrecount_*). The denominator
of each jscrecount_*_pct is the number of flagged citations of the journal (broad-predatory after the identity check: at least
one predatory-list hit, not in broad_drops, view entries_confirmed_all of get_conn_after_identity_check()); there is no legitimate
or strict denominator.
"""
from __future__ import annotations

import json
import re

import stats_common
import identity_check_table_analyses as table_analyses

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_jsc_citation_breakdown"
stats_common.OUTPUT_DIR = OUTPUT_DIR

JSC_VENUE = "Journal of Scientific Computing"
# Print and electronic ISSN of the Journal of Scientific Computing, with and without hyphen.
JSC_ISSNS = {"0885-7474", "08857474", "1573-7691", "15737691"}
# ISSN of the site that copies the journal, with and without hyphen.
COPY_SITE_ISSN = {"1524-2560", "15242560"}


def norm_doi(d: str | None) -> str:
    if not d:
        return ""
    d = d.strip().lower()
    return re.sub(r"^https?://(doi\.org/|dx\.doi\.org/)?", "", d)


def norm_issn_set(s: str | None) -> set[str]:
    if not s:
        return set()
    return {p for p in re.split(r"[,\s]+", s.strip()) if p}


def classify(doi: str | None, issn: str | None, eissn: str | None) -> str:
    d = norm_doi(doi)
    issns = norm_issn_set(issn) | norm_issn_set(eissn)
    if d.startswith("10.1007/"):
        return "springer_doi"
    if d.startswith("10.1023/"):
        return "kluwer_legacy_doi"
    if issns & COPY_SITE_ISSN:
        return "copy_site_issn"
    if issns & JSC_ISSNS:
        return "jsc_issn_match"
    return "unresolved"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()

    n_total = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all WHERE COALESCE(matched_venue_name, venue_raw)=?", (JSC_VENUE,)
    ).fetchone()["n"]

    rows = conn.execute(
        f"SELECT month, arxiv_id, suffix, entry_key, doi, issn, eissn, "
        f"CAST(predatory_votes AS INTEGER) AS predatory_votes, CAST(legitimate_votes AS INTEGER) AS legitimate_votes, "
        f"predatory_list_hits "
        f"FROM entries_confirmed_all e WHERE COALESCE(matched_venue_name, venue_raw)=? "
        f"AND CAST(n_predatory_list_hits AS INTEGER) > 0 AND {table_analyses.BROAD_NOT_DROPPED}",
        (JSC_VENUE,),
    ).fetchall()
    n_flagged = len(rows)

    n_flagged_solely_this_list = sum(1 for r in rows if r["predatory_list_hits"] == "algerian_ministry")

    # Number of legitimate and predatory votes per flagged citation.
    vote_split: dict[str, int] = {}
    for r in rows:
        key = f"{r['legitimate_votes']}legit_vs_{r['predatory_votes']}predatory"
        vote_split[key] = vote_split.get(key, 0) + 1

    # Classification by DOI prefix, then ISSN.
    classification_counts: dict[str, int] = {}
    for r in rows:
        bucket = classify(r["doi"], r["issn"], r["eissn"])
        classification_counts[bucket] = classification_counts.get(bucket, 0) + 1

    out = {
        "n_total_citations": n_total,
        "n_flagged": n_flagged,
        "n_flagged_solely_this_list": n_flagged_solely_this_list,
        "vote_split": vote_split,
        "classification_counts": classification_counts,
    }
    stats_common.write_json("jsc_citation_breakdown", out)
    print(json.dumps(out, indent=2, default=str))

    # Cross-check against the totals in headline_distribution_and_vote_rules.numbers.json.
    cross_check_path = stats_common.NUMBERS_DIR / "headline_distribution_and_vote_rules.numbers.json"
    cross_check = json.loads(cross_check_path.read_text())
    assert n_total == cross_check["jsc_n_total_citations"]["value"], (
        f"{n_total} != {cross_check['jsc_n_total_citations']['value']}"
    )
    assert n_flagged_solely_this_list == cross_check["jsc_n_flagged_solely_algerian"]["value"], (
        f"{n_flagged_solely_this_list} != {cross_check['jsc_n_flagged_solely_algerian']['value']}"
    )

    numbers_payload = {
        "jscrecount_genuine_issn_match_pct": {
            "kind": "ratio", "num": classification_counts.get("jsc_issn_match", 0), "den": n_flagged, "decimals": 1,
        },
        "jscrecount_genuine_springer_doi_pct": {
            "kind": "ratio", "num": classification_counts.get("springer_doi", 0), "den": n_flagged, "decimals": 1,
        },
        "jscrecount_genuine_kluwer_legacy_doi_pct": {
            "kind": "ratio", "num": classification_counts.get("kluwer_legacy_doi", 0), "den": n_flagged, "decimals": 1,
        },
    }
    stats_common.write_numbers("jsc_citation_breakdown", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
