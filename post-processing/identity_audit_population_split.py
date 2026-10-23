#!/usr/bin/env python3
"""Splits the strict-predatory citations by the ISSN identity check of full_corpus_identity_mismatch_audit.py (the vote rules of
identity_vote_rules.py are reused). Winning backends decide the class: `no_extract` (none extracts an ISSN itself, WATCHLIST_ISSN_CAPABLE),
`extract` (all do) or `mixed` (some do). Counts of citations per class and overall: no citing ISSN, mismatch, structurally
undecidable, confirmed correct. Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT, EVAL_CACHE_DB_PATH and the audit keys file under
EVAL_OUTPUT_DIR/output_identity_mismatch_audit; stops unless the mismatch set equals the audit's. Writes
EVAL_OUTPUT_DIR/output_identity_audit_population_split/identity_audit_population_split.json and
numbers/identity_audit_population_split.numbers.json (keys auditpop_*)."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import stats_common
import full_corpus_identity_mismatch_audit as audit
from identity_vote_rules import WATCHLIST_ISSN_CAPABLE, build_any_row_has_issn_index, classify_votes, normalize_issn
from leave_one_out_helpers import load_records

OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_audit_population_split"
AUDIT_KEYS_JSON = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"
# the audit import sets these to its own values; set them to this script's own
stats_common.DB_PATH = audit.DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR


def winner_class(winners: list[str]) -> str:
    capable = [WATCHLIST_ISSN_CAPABLE.get(b, True) for b in winners]
    if not any(capable):
        return "no_extract"
    if all(capable):
        return "extract"
    return "mixed"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = audit.get_conn_bbl_tex()
    cache_conn = sqlite3.connect(str(audit.CACHE_DB_PATH))
    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits "
        "FROM entries_confirmed WHERE final_assessment='predatory'"
    ).fetchall()
    records = load_records([(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows])
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    verdict_totals: Counter = Counter()
    by_class: dict[str, Counter] = {c: Counter() for c in ("no_extract", "extract", "mixed")}
    mismatch_keys: set = set()

    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        citing = normalize_issn(r["issn"])
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]
        if citing is None:
            verdict_totals["unverifiable_no_citing_issn"] += 1
            continue
        rec = records.get(key)
        if rec is None:
            verdict_totals["missing_backend_record"] += 1
            continue
        per_backend, verdict = classify_votes(rec, winners, citing, any_row_has_issn)
        mis = verdict == "confirmed_misattribution"
        undecidable_vote = any(pb["type"] == "not_decidable_by_issn" for pb in per_backend)
        verdict_totals[verdict] += 1
        cls = winner_class(winners)
        by_class[cls]["with_citing_issn"] += 1
        by_class[cls][verdict] += 1
        if undecidable_vote:
            by_class[cls]["has_undecidable_winner"] += 1
        if mis:
            mismatch_keys.add(key)

    # the mismatch set must equal the audit's before any split is written
    keys = json.loads(AUDIT_KEYS_JSON.read_text())
    audit_keys = {tuple(k) for k in keys["confirmed_misattribution_keys"]}
    assert mismatch_keys == audit_keys, f"mismatch set differs from the audit's: {len(mismatch_keys)} vs {len(audit_keys)}"
    n_strict = len(rows)
    with_issn = n_strict - verdict_totals["unverifiable_no_citing_issn"]
    assert sum(c["with_citing_issn"] for c in by_class.values()) == with_issn

    out = {
        "n_strict_predatory": n_strict,
        "n_with_citing_issn": with_issn,
        "verdicts": dict(verdict_totals),
        "by_winner_class": {c: dict(v) for c, v in by_class.items()},
    }
    stats_common.write_json("identity_audit_population_split", out)
    print(json.dumps(out, indent=2))

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    numbers = {
        "auditpop_n_original_strict": i(n_strict),
        "auditpop_n_with_citing_issn": i(with_issn),
        "auditpop_n_without_citing_issn": i(verdict_totals["unverifiable_no_citing_issn"]),
        "auditpop_n_mismatch": i(verdict_totals["confirmed_misattribution"]),
        "auditpop_n_structurally_undecidable": i(verdict_totals["structurally_undecidable"]),
        "auditpop_n_confirmed_correct": i(verdict_totals["confirmed_correct"]),
    }
    for cls, c in by_class.items():
        numbers[f"auditpop_{cls}_n_with_issn"] = i(c["with_citing_issn"])
        numbers[f"auditpop_{cls}_n_mismatch"] = i(c["confirmed_misattribution"])
        numbers[f"auditpop_{cls}_n_undecidable"] = i(c["structurally_undecidable"])
        numbers[f"auditpop_{cls}_n_confirmed_correct"] = i(c["confirmed_correct"])
    stats_common.write_numbers("identity_audit_population_split", numbers)


if __name__ == "__main__":
    main()
