#!/usr/bin/env python3
"""Leave-one-backend-out sweep of the strict label over the 22 backends. For every broad-predatory
citation that keeps its broad flag after the identity check, each backend in turn is removed from the
stored backend results together with that citation's own invalid votes (votes of lists whose matched
entry carries another ISSN than the citation), and the strict verdict is recomputed. Records, per
backend, the number of strict and of broad citations that remain. The baselines are the counts after the
identity check: all strict citations (final_assessment 'predatory') minus the strict-excluded ones, and all broad
citations minus those whose broad flag does not survive the check; they are the denominators of the percentage changes.

Reads EVAL_DB_PATH_BBL_TEX, EVAL_CACHE_DB_PATH, EVAL_RESULTS_ROOT (via leave_one_out_helpers) and, under
EVAL_OUTPUT_DIR, the identity-audit key file (output_identity_mismatch_audit/) and identity_audit_citation_detail.json.
Writes leave_one_out_backend_sweep.json and a per-backend CSV to EVAL_OUTPUT_DIR/output_leave_one_out_backend_sweep/,
and numbers/leave_one_out_backend_sweep.numbers.json (keys leave_one_out_*: the two baselines, the number of records whose
recomputed verdict is compared with the stored one and the share that match, and the counts and percentage changes of four
backends).
"""
from __future__ import annotations

import json
import sqlite3

from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import ALL_BACKENDS, build_invalid_backends_map
import leave_one_out_helpers as loo
import stats_common
from stats_common import required_path_env, write_csv, write_json

DB_PATH_BBL_TEX = required_path_env("EVAL_DB_PATH_BBL_TEX")
CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_leave_one_out_backend_sweep"
AUDIT_KEYS_JSON = required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"
CITATION_DETAIL_PATH = required_path_env("EVAL_OUTPUT_DIR") / "identity_audit_citation_detail.json"

stats_common.DB_PATH = DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR


def get_conn():
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed_all AS SELECT * FROM entries_confirmed_bbl_tex")
    return conn


def leave_one_out_sweep(conn, cache_conn) -> dict:
    """Returns the strict and broad baselines and, per swept backend, the strict and broad counts after exclusion."""
    audit_keys_data = json.loads(AUDIT_KEYS_JSON.read_text())
    misattributed_keys = {tuple(k) for k in audit_keys_data["confirmed_misattribution_keys"]}
    strict_exclude_keys = {tuple(k) for k in audit_keys_data["strict_exclude_keys"]}
    n_strict_total = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all WHERE final_assessment='predatory'").fetchone()["n"]

    per_citation = json.loads(CITATION_DETAIL_PATH.read_text())
    broad_drop_keys = {(c["month"], c["arxiv_id"], c["suffix"], c["entry_key"]) for c in per_citation if not c["broad_survives"]}

    # The invalid-vote map covers all confirmed-misattribution citations, including the second-vote
    # survivors that stay in the strict count.
    invalid_backends_map = build_invalid_backends_map(conn, cache_conn, misattributed_keys)
    print(f"invalid-vote map: {len(invalid_backends_map)} of {len(misattributed_keys)} confirmed-misattribution citations have an invalid vote", flush=True)

    conn.execute("CREATE TEMP TABLE broad_drop_keys (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO broad_drop_keys VALUES (?, ?, ?, ?)", sorted(broad_drop_keys))

    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, final_assessment "
        "FROM entries_confirmed_all "
        "WHERE CAST(n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM broad_drop_keys b WHERE b.month=entries_confirmed_all.month AND b.arxiv_id=entries_confirmed_all.arxiv_id "
        "  AND b.suffix=entries_confirmed_all.suffix AND b.entry_key=entries_confirmed_all.entry_key)"
    ).fetchall()
    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    records = loo.load_records(targets)
    validation = loo.validate_reimplementation(records)
    print(f"recomputed vs stored verdict: {validation['pct_match']}%", flush=True)

    # Counts after the identity check: the strict-excluded citations leave the strict count (second-vote survivors stay);
    # the citations whose broad flag does not survive leave the broad count.
    n_strict_after_identity_check = n_strict_total - len(strict_exclude_keys)
    n_broad_after_identity_check = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all WHERE CAST(n_predatory_list_hits AS INTEGER) > 0"
    ).fetchone()["n"] - len(broad_drop_keys)
    print(f"strict baseline after identity check: {n_strict_after_identity_check}", flush=True)
    print(f"broad baseline after identity check: {n_broad_after_identity_check}", flush=True)

    per_backend = []
    for i, backend in enumerate(ALL_BACKENDS):
        print(f"  sweeping backend {i+1}/{len(ALL_BACKENDS)}: {backend} ...", flush=True)
        strict_after = 0
        broad_after = 0
        for r in rows:
            key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
            rec = records.get(key)
            if rec is None:
                continue
            own_invalid = invalid_backends_map.get(key, set())
            exclude_set = {backend} | own_invalid
            assessment_without_backend = recompute_assessment_excluding(rec.get("backend_results", []), exclude_set)
            if assessment_without_backend == "predatory":
                strict_after += 1
            has_other_predatory_list_vote = any(
                b.get("status") == "found" and b.get("backend_name") not in exclude_set
                and b.get("assessment") == "predatory" and b.get("evidence_type") == "predatory_list"
                for b in rec.get("backend_results", [])
            )
            if has_other_predatory_list_vote:
                broad_after += 1
        per_backend.append({
            "backend": backend,
            "strict_after_exclusion": strict_after,
            "broad_after_exclusion": broad_after,
        })

    strict_values = [d["strict_after_exclusion"] for d in per_backend]
    range_min, range_max = min(strict_values), max(strict_values)
    return {
        "recomputed_vs_stored_verdict": validation,
        "strict_baseline_after_identity_check": n_strict_after_identity_check,
        "broad_baseline_after_identity_check": n_broad_after_identity_check,
        "per_backend": sorted(per_backend, key=lambda d: d["strict_after_exclusion"]),
        "strict_range_after_exclusion": {
            "min": range_min, "max": range_max,
            "pct_spread": round(100.0 * (range_max - range_min) / n_strict_after_identity_check, 1),
        },
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cache_conn = sqlite3.connect(str(CACHE_DB_PATH))

    print(f"sweeping the {len(ALL_BACKENDS)} backends ...", flush=True)
    sweep = leave_one_out_sweep(conn, cache_conn)

    out = {"sweep": sweep}
    write_json("leave_one_out_backend_sweep", out)
    write_csv(
        "leave_one_out_sweep_per_backend",
        sweep["per_backend"],
        list(sweep["per_backend"][0].keys()),
    )
    print(json.dumps({"sweep": {k: v for k, v in sweep.items() if k != "per_backend"}}, indent=2, default=str))
    print(json.dumps(sweep["per_backend"], indent=2, default=str))

    strict_baseline = sweep["strict_baseline_after_identity_check"]
    broad_baseline = sweep["broad_baseline_after_identity_check"]
    validation = sweep["recomputed_vs_stored_verdict"]
    numbers_payload = {
        "leave_one_out_strict_baseline": {"kind": "int", "value": strict_baseline},
        "leave_one_out_broad_baseline": {"kind": "int", "value": broad_baseline},
        "leave_one_out_reimplementation_records": {"kind": "int", "value": validation["match"] + validation["mismatch"]},
        "leave_one_out_reimplementation_match_pct": {
            "kind": "ratio", "num": validation["match"], "den": validation["match"] + validation["mismatch"], "decimals": 1,
        },
    }
    # Per backend, the fields that are written: (macro-key spelling of the backend, fields).
    kept_fields = {
        "algerian_ministry": ("algerian_ministry", ("strict_after_exclusion", "strict_pct_change", "broad_after_exclusion", "broad_pct_change")),
        "kscien_standalone_journals": ("kscien_standalone_journals", ("broad_pct_change",)),
        "scopus": ("scopus", ("strict_after_exclusion", "strict_pct_change")),
        # "..._group2" is spelled "grouptwo" in the key: the camel-case macro names drop the digit.
        "ugc_care_cloned_group2": ("ugc_care_cloned_grouptwo", ("strict_after_exclusion", "broad_pct_change")),
    }
    for d in sweep["per_backend"]:
        if d["backend"] not in kept_fields:
            continue
        key_backend, fields = kept_fields[d["backend"]]
        strict_after = d["strict_after_exclusion"]
        broad_after = d["broad_after_exclusion"]
        values = {
            "strict_after_exclusion": {"kind": "int", "value": strict_after},
            "strict_pct_change": {"kind": "ratio", "num": strict_after - strict_baseline, "den": strict_baseline, "decimals": 1},
            "broad_after_exclusion": {"kind": "int", "value": broad_after},
            "broad_pct_change": {"kind": "ratio", "num": broad_after - broad_baseline, "den": broad_baseline, "decimals": 1},
        }
        for field in fields:
            numbers_payload[f"leave_one_out_{key_backend}_{field}"] = values[field]
    stats_common.write_numbers("leave_one_out_backend_sweep", numbers_payload)


if __name__ == "__main__":
    main()
