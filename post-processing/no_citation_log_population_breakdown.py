#!/usr/bin/env python3
"""Strict and broad predatory counts after the identity check for (a) the confirmed-cited entries (citation_status_bbl_tex
'confirmed_cited'), (b) the unverified-isolated entries (entries of files without a citation log) and (c) both combined.

For each population the strict-predatory entries are checked as in full_corpus_identity_mismatch_audit.py
(identity_vote_rules.classify_votes: citing ISSN against the ISSN of the winning backends' cache rows; the two UGC-CARE copy
lists cannot be decided by ISSN), mismatching entries are evaluated again without their invalid votes
(identity_check_headline_analyses.recompute_assessment_excluding), and the strict and broad counts after the identity check
follow. The logic is checked on (a) against the audit keys file. The strict count
of a population is its entries with final_assessment 'predatory' minus the strict exclusions; the broad count is its entries with
n_predatory_list_hits > 0 minus the broad drops; the rates divide by the entries of the population. Reads
EVAL_DB_PATH_BBL_TEX, the per-run JSONL under EVAL_RESULTS_ROOT and EVAL_CACHE_DB_PATH. Writes
numbers/no_citation_log_population_breakdown.numbers.json (keys nosidecarcorr_*) and
EVAL_OUTPUT_DIR/output_no_citation_log_population_breakdown/no_citation_log_population_breakdown.json (aggregates only).
"""
from __future__ import annotations

import json
import sqlite3

import stats_common
from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import build_any_row_has_issn_index, classify_votes, invalid_backends, normalize_issn
from leave_one_out_helpers import load_records

DB_PATH_BBL_TEX = stats_common.required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_no_citation_log_population_breakdown"
AUDIT_KEYS_JSON = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"
stats_common.OUTPUT_DIR = OUTPUT_DIR
stats_common.DB_PATH = DB_PATH_BBL_TEX

POPULATIONS = {"a": "confirmed_cited", "b": "unverified_isolated"}


def classify(rows, records, any_row_has_issn) -> list[dict]:
    """Per-citation identity check of the strict-predatory rows (identity_vote_rules.classify_votes)."""
    verdicts = []
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        citing_issn = normalize_issn(r["issn"])
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]

        if citing_issn is None:
            verdicts.append({"key": key, "citing_issn": None, "winners": winners, "verdict": "unverifiable_no_citing_issn"})
            continue

        rec = records.get(key)
        if rec is None:
            raise SystemExit("a strict entry with a citing ISSN has no backend record; the population needs it")

        per_backend, verdict = classify_votes(rec, winners, citing_issn, any_row_has_issn)
        verdicts.append({"key": key, "citing_issn": citing_issn, "winners": winners,
                         "per_backend": per_backend, "verdict": verdict})
    return verdicts


def second_vote_split(verdicts, records) -> dict:
    """Second-vote survivors, strict exclusions and broad drops, as in full_corpus_identity_mismatch_audit.py main()."""
    misattributed = [v for v in verdicts if v["verdict"] == "confirmed_misattribution"]
    misattributed_keys = {v["key"] for v in misattributed}
    broad_drop_keys: set = set()
    broad_survives_keys: set = set()
    survivors: set = set()
    for v in misattributed:
        invalid = invalid_backends(v["per_backend"])
        if set(v["winners"]) - invalid:
            broad_survives_keys.add(v["key"])
        else:
            broad_drop_keys.add(v["key"])
        if recompute_assessment_excluding(records[v["key"]].get("backend_results", []), invalid) == "predatory":
            survivors.add(v["key"])
    return {
        "mismatch": misattributed_keys, "survivors": survivors,
        "strict_exclude": misattributed_keys - survivors,
        "broad_drop": broad_drop_keys, "broad_survives": broad_survives_keys,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{DB_PATH_BBL_TEX}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cache_conn = sqlite3.connect(str(stats_common.required_path_env("EVAL_CACHE_DB_PATH")))
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    res: dict[str, dict] = {}
    for label, status in POPULATIONS.items():
        n_total = conn.execute("SELECT COUNT(*) n FROM entries WHERE citation_status_bbl_tex = ?", (status,)).fetchone()["n"]
        n_broad_without_check = conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE citation_status_bbl_tex = ? AND CAST(n_predatory_list_hits AS INTEGER) > 0", (status,)
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits FROM entries "
            "WHERE citation_status_bbl_tex = ? AND final_assessment = 'predatory'", (status,)
        ).fetchall()
        records = load_records([(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows])
        verdicts = classify(rows, records, any_row_has_issn)
        rp = second_vote_split(verdicts, records)
        vc = {}
        for v in verdicts:
            vc[v["verdict"]] = vc.get(v["verdict"], 0) + 1
        res[label] = {
            "n_total": n_total, "n_strict_without_check": len(rows), "n_broad_without_check": n_broad_without_check,
            "n_with_issn": len(rows) - vc.get("unverifiable_no_citing_issn", 0),
            "n_without_issn": vc.get("unverifiable_no_citing_issn", 0),
            "n_mismatch": len(rp["mismatch"]), "n_undecidable": vc.get("structurally_undecidable", 0),
            "n_confirmed_correct": vc.get("confirmed_correct", 0), "n_survivors": len(rp["survivors"]),
            "n_strict_exclude": len(rp["strict_exclude"]), "n_broad_drop": len(rp["broad_drop"]),
            "n_strict_after_check": len(rows) - len(rp["strict_exclude"]),
            "n_broad_after_check": n_broad_without_check - len(rp["broad_drop"]),
            "_keys": rp,
        }

    # Population (a) is compared with the audit keys file.
    keys = json.loads(AUDIT_KEYS_JSON.read_text())
    ka = res["a"]["_keys"]
    for field, mine in (("confirmed_misattribution_keys", "mismatch"), ("second_vote_survivor_keys", "survivors"),
                        ("strict_exclude_keys", "strict_exclude"), ("broad_drop_keys", "broad_drop"),
                        ("broad_survives_keys", "broad_survives")):
        assert ka[mine] == {tuple(k) for k in keys[field]}, f"population (a): {mine} differ from the audit's {field}"
    a, b = res["a"], res["b"]
    for r in res.values():
        r.pop("_keys")

    c = {
        "n_total": a["n_total"] + b["n_total"],
        "n_strict_after_check": a["n_strict_after_check"] + b["n_strict_after_check"],
        "n_broad_after_check": a["n_broad_after_check"] + b["n_broad_after_check"],
    }
    out = {"a_confirmed_cited": a, "b_unverified_isolated": b, "c_combined": c}
    stats_common.write_json("no_citation_log_population_breakdown", out)
    print(json.dumps(out, indent=2))

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    def ratio(num: int, den: int) -> dict:
        return {"kind": "ratio", "num": int(num), "den": int(den), "decimals": 3}

    stats_common.write_numbers("no_citation_log_population_breakdown", {
        "nosidecarcorr_a_n_total": i(a["n_total"]),
        "nosidecarcorr_a_strict_n": i(a["n_strict_after_check"]),
        "nosidecarcorr_a_broad_n": i(a["n_broad_after_check"]),
        "nosidecarcorr_a_strict_pct": ratio(a["n_strict_after_check"], a["n_total"]),
        "nosidecarcorr_a_broad_pct": ratio(a["n_broad_after_check"], a["n_total"]),
        "nosidecarcorr_b_n_total": i(b["n_total"]),
        "nosidecarcorr_b_strict_raw_n": i(b["n_strict_without_check"]),
        "nosidecarcorr_b_broad_raw_n": i(b["n_broad_without_check"]),
        "nosidecarcorr_b_strict_with_issn_n": i(b["n_with_issn"]),
        "nosidecarcorr_b_strict_without_issn_n": i(b["n_without_issn"]),
        "nosidecarcorr_b_issn_mismatch_n": i(b["n_mismatch"]),
        "nosidecarcorr_b_structurally_undecidable_n": i(b["n_undecidable"]),
        "nosidecarcorr_b_confirmed_correct_n": i(b["n_confirmed_correct"]),
        "nosidecarcorr_b_second_vote_survivor_n": i(b["n_survivors"]),
        "nosidecarcorr_b_strict_n": i(b["n_strict_after_check"]),
        "nosidecarcorr_b_broad_dropped_n": i(b["n_broad_drop"]),
        "nosidecarcorr_b_broad_n": i(b["n_broad_after_check"]),
        "nosidecarcorr_b_strict_pct": ratio(b["n_strict_after_check"], b["n_total"]),
        "nosidecarcorr_b_broad_pct": ratio(b["n_broad_after_check"], b["n_total"]),
        "nosidecarcorr_c_n_total": i(c["n_total"]),
        "nosidecarcorr_c_strict_n": i(c["n_strict_after_check"]),
        "nosidecarcorr_c_broad_n": i(c["n_broad_after_check"]),
        "nosidecarcorr_c_strict_pct": ratio(c["n_strict_after_check"], c["n_total"]),
        "nosidecarcorr_c_broad_pct": ratio(c["n_broad_after_check"], c["n_total"]),
    })


if __name__ == "__main__":
    main()
