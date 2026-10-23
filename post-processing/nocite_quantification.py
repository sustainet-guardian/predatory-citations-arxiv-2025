#!/usr/bin/env python3
"""Quantifies how many confirmed-cited entries come from keys that appear in the .bbl only and are attributable to \\nocite.

Per submission, a key is .bbl-only when it is in bbl_keys and not in tex_cite_keys (submissions of file_type tar_gz with a
non-empty bbl_keys | tex_cite_keys); it is nocite-attributable when the submission has \\nocite{*} or lists the key in an
explicit \\nocite. Reads EVAL_BBL_JSONL_PATH, EVAL_DB_PATH_BBL_TEX and the identity audit output in
EVAL_OUTPUT_DIR/output_identity_mismatch_audit/ (full_corpus_identity_mismatch_audit.json: strict and broad counts;
full_corpus_identity_mismatch_audit_keys.json: the citations the identity check excludes). Reports the .bbl-only keys, the
promoted entries that are nocite-attributable (total, strict, broad) and the strict / broad rates without them. Basis: the
strict and broad counts are those after the identity check, and the promoted nocite-attributable strict and broad entries are
counted after the check too (entries in strict_exclude or broad_drops do not count), so none is subtracted twice. The
denominator of the rates is the number of confirmed-cited entries (the entries_confirmed_bbl_tex view), for the excluded_*
rates reduced by the promoted nocite-attributable entries; the strict (final_assessment 'predatory') and broad numerators
are the counts of the audit output. Writes numbers/nocite_quantification.numbers.json
(keys nocite_*) and EVAL_OUTPUT_DIR/output_nocite_quantification/nocite_quantification.json.
"""
from __future__ import annotations

import json
import sqlite3

import stats_common
from stats_common import required_path_env

JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_nocite_quantification"
AUDIT_JSON = required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit.json"
AUDIT_KEYS_JSON = required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    submissions: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("file_type") != "tar_gz":
                continue
            key = (rec["month"], rec["arxiv_id"])
            submissions[key] = rec

    # Submissions with a usable signal: file_type 'tar_gz' and bbl_keys | tex_cite_keys non-empty
    # (the filter of set_citation_status_bbl_tex.py).
    usable_signal_subs = {
        key: rec for key, rec in submissions.items()
        if (set(rec.get("bbl_keys") or []) | set(rec.get("tex_cite_keys") or []))
    }
    n_usable_signal_submissions = len(usable_signal_subs)

    # .bbl-only keys, and the nocite-attributable subset of them.
    bbl_only_total = 0
    bbl_only_nocite_attributable = 0
    nocite_attributable_keys: set[tuple[str, str, str]] = set()  # (month, arxiv_id, entry_key)

    for (month, arxiv_id), rec in usable_signal_subs.items():
        bbl_keys = set(rec.get("bbl_keys") or [])
        tex_keys = set(rec.get("tex_cite_keys") or [])
        nocite_star = bool(rec.get("nocite_star"))
        nocite_explicit = set(rec.get("nocite_explicit_keys") or [])
        bbl_only = bbl_keys - tex_keys
        bbl_only_total += len(bbl_only)
        for key in bbl_only:
            if nocite_star or key in nocite_explicit:
                bbl_only_nocite_attributable += 1
                nocite_attributable_keys.add((month, arxiv_id, key))

    non_nocite_bbl_only = bbl_only_total - bbl_only_nocite_attributable

    # Promoted entries (citation_status_bbl_tex_changed) that are nocite-attributable.
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row

    promoted_rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, final_assessment, n_predatory_list_hits "
        "FROM entries WHERE citation_status_bbl_tex_changed='1' AND citation_status_bbl_tex='confirmed_cited'"
    ).fetchall()
    n_promoted_total = len(promoted_rows)

    promoted_nocite_attributable = [
        r for r in promoted_rows if (r["month"], r["arxiv_id"], r["entry_key"]) in nocite_attributable_keys
    ]
    n_promoted_nocite_attributable = len(promoted_nocite_attributable)

    # Strict and broad counts among them after the identity check: entries that the check excludes (strict_exclude_keys) or
    # whose broad flag it drops (broad_drop_keys) do not count.
    audit_keys = json.loads(AUDIT_KEYS_JSON.read_text())
    strict_exclude = {tuple(k) for k in audit_keys["strict_exclude_keys"]}
    broad_drop = {tuple(k) for k in audit_keys["broad_drop_keys"]}

    def entry_key(r) -> tuple:
        return (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])

    n_promoted_nocite_strict = sum(
        1 for r in promoted_nocite_attributable if r["final_assessment"] == "predatory" and entry_key(r) not in strict_exclude
    )
    n_promoted_nocite_broad = sum(
        1 for r in promoted_nocite_attributable if int(r["n_predatory_list_hits"] or 0) > 0 and entry_key(r) not in broad_drop
    )

    # Rates with the nocite-attributable promoted entries removed from numerator and denominator.
    confirmed_cited_total = conn.execute("SELECT COUNT(*) FROM entries_confirmed_bbl_tex").fetchone()[0]
    print(f"confirmed_cited: {confirmed_cited_total}", flush=True)

    # Strict and broad counts after the identity check, from the audit output.
    audit_json = json.loads(AUDIT_JSON.read_text())
    strict_after_identity_check = audit_json["counts_after_identity_check"]["strict_predatory"]["n_after_identity_check"]
    broad_after_identity_check = audit_json["counts_after_identity_check"]["broad_predatory"]["n_after_identity_check"]

    excluded_denominator = confirmed_cited_total - n_promoted_nocite_attributable
    excluded_strict = strict_after_identity_check - n_promoted_nocite_strict
    excluded_broad = broad_after_identity_check - n_promoted_nocite_broad

    strict_rate_after_identity_check = 100.0 * strict_after_identity_check / confirmed_cited_total
    broad_rate_after_identity_check = 100.0 * broad_after_identity_check / confirmed_cited_total
    excluded_strict_rate = 100.0 * excluded_strict / excluded_denominator
    excluded_broad_rate = 100.0 * excluded_broad / excluded_denominator

    out = {
        "bbl_only_superset": {
            "n_usable_signal_submissions": n_usable_signal_submissions,
            "bbl_only_total_keys": bbl_only_total,
            "bbl_only_nocite_attributable_keys": bbl_only_nocite_attributable,
            "bbl_only_non_nocite_keys": non_nocite_bbl_only,
        },
        "intersection_with_headline_populations": {
            "n_confirmed_cited_promoted_total": n_promoted_total,
            "n_promoted_that_are_nocite_attributable": n_promoted_nocite_attributable,
            "pct_of_promoted_that_are_nocite_attributable": (
                round(100.0 * n_promoted_nocite_attributable / n_promoted_total, 3) if n_promoted_total else 0.0
            ),
            "n_nocite_attributable_promoted_strict_predatory": n_promoted_nocite_strict,
            "n_nocite_attributable_promoted_broad_predatory": n_promoted_nocite_broad,
        },
        "denominator_effect": {
            "confirmed_cited_total": confirmed_cited_total,
            "strict_after_identity_check": strict_after_identity_check,
            "broad_after_identity_check": broad_after_identity_check,
            "strict_rate_after_identity_check_pct": round(strict_rate_after_identity_check, 5),
            "broad_rate_after_identity_check_pct": round(broad_rate_after_identity_check, 5),
            "if_nocite_attributable_promoted_entries_excluded": {
                "denominator": excluded_denominator,
                "strict_n": excluded_strict,
                "broad_n": excluded_broad,
                "strict_rate_pct": round(excluded_strict_rate, 5),
                "broad_rate_pct": round(excluded_broad_rate, 5),
                "strict_rate_delta_pct_points": round(excluded_strict_rate - strict_rate_after_identity_check, 5),
                "broad_rate_delta_pct_points": round(excluded_broad_rate - broad_rate_after_identity_check, 5),
            },
        },
    }
    stats_common.write_json("nocite_quantification", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("nocite_quantification", {
        "nocite_bbl_only_total": {"kind": "int", "value": bbl_only_total},
        "nocite_bbl_only_attributable": {"kind": "int", "value": bbl_only_nocite_attributable},
        "nocite_bbl_only_non_attributable": {"kind": "int", "value": non_nocite_bbl_only},
        "nocite_bbl_only_attributable_pct": {
            "kind": "ratio", "num": bbl_only_nocite_attributable, "den": bbl_only_total, "decimals": 1,
        },
        "nocite_n_promoted_total": {"kind": "int", "value": n_promoted_total},
        "nocite_n_promoted_attributable": {"kind": "int", "value": n_promoted_nocite_attributable},
        "nocite_n_promoted_attributable_strict": {"kind": "int", "value": n_promoted_nocite_strict},
        "nocite_n_promoted_attributable_broad": {"kind": "int", "value": n_promoted_nocite_broad},
        "nocite_excluded_denominator": {"kind": "int", "value": excluded_denominator},
        "nocite_excluded_strict_count": {"kind": "int", "value": excluded_strict},
        "nocite_excluded_broad_count": {"kind": "int", "value": excluded_broad},
        "nocite_pct_of_promoted_attributable": {
            "kind": "ratio", "num": n_promoted_nocite_attributable, "den": n_promoted_total, "decimals": 1,
        },
        "nocite_pct_strict_of_attributable": {
            "kind": "ratio", "num": n_promoted_nocite_strict, "den": n_promoted_nocite_attributable, "decimals": 3,
        },
        "nocite_pct_broad_of_attributable": {
            "kind": "ratio", "num": n_promoted_nocite_broad, "den": n_promoted_nocite_attributable, "decimals": 3,
        },
        "nocite_excluded_strict_rate": {"kind": "ratio", "num": excluded_strict, "den": excluded_denominator, "decimals": 3},
        "nocite_excluded_broad_rate": {"kind": "ratio", "num": excluded_broad, "den": excluded_denominator, "decimals": 3},
    })


if __name__ == "__main__":
    main()
