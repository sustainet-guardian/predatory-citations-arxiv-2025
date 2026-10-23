#!/usr/bin/env python3
"""Composition of the sample assessed in a second run. Every file of EVAL_SECOND_RUN_ROOT is joined with its
EVAL_RESULTS_ROOT counterpart (the first run) on (entry_key, venue_raw) (load_assessment_records.load_records). For the joined
entries the script counts, per run: final_assessment 'predatory' (strict), the broad flag (predatory_list_hits non-empty),
and flips of either; for the entries in the primary population (confirmed-cited, EVAL_DB_PATH_BBL_TEX) it also counts them
after removal of the strict and broad flags dropped by the identity check. The script does not draw the sample: the
second-run files are the files in EVAL_SECOND_RUN_ROOT. It reports the sampling frame, the files of category 'assessed'
whose entry_count lies within the 25th-75th percentile band of their month (the sorted entry counts of the month at positions
int(0.25*(n-1)) and int(0.75*(n-1))), with the numbers of confirmed-cited, strict and broad references in them. The shares of
the sample in the population use as denominators all confirmed-cited entries (view entries_confirmed_all) and the
resolved-eligible entries (view entries_confirmed_valid_votes with final_assessment_valid_votes legitimate, predatory,
suspicious or insufficient_data). Writes
reassessment_sample_composition.json to EVAL_OUTPUT_DIR/output_reassessment_sample_composition and
numbers/reassessment_sample_composition.numbers.json (keys reevalsample_*). Aggregates only.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import stats_common
import identity_check_table_analyses as table_analyses
from load_assessment_records import load_records

FIRST_RUN_DIR = stats_common.required_path_env("EVAL_RESULTS_ROOT")
SECOND_RUN_DIR = stats_common.required_path_env("EVAL_SECOND_RUN_ROOT")
OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_reassessment_sample_composition"
stats_common.OUTPUT_DIR = OUTPUT_DIR

RESOLVED = {"legitimate", "predatory", "suspicious", "insufficient_data"}
NAME = re.compile(r"^(\d{4})__(\d+)-(\d+)$")


def broad(rec: dict) -> bool:
    return bool(rec.get("predatory_list_hits"))


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = table_analyses.get_conn_after_identity_check()
    names = sorted(p.stem for p in SECOND_RUN_DIR.glob("*.jsonl"))
    assert all(NAME.match(n) for n in names)

    # database side for the sampled files: confirmed-cited rows with the verdict from the valid votes
    strict_exclude_keys = {tuple(r) for r in conn.execute("SELECT month, arxiv_id, suffix, entry_key FROM strict_exclude")}
    broad_drop_keys = {tuple(r) for r in conn.execute("SELECT month, arxiv_id, suffix, entry_key FROM broad_drops")}
    db: dict[tuple, dict] = {}
    for nm in names:
        month, arxiv_id, suffix = NAME.match(nm).groups()
        for r in conn.execute(
            "SELECT entry_key, final_assessment_valid_votes AS valid_votes_assessment FROM entries_confirmed_valid_votes e "
            "WHERE month = ? AND arxiv_id = ? AND suffix = ?", (month, arxiv_id, suffix)
        ):
            k = (month, arxiv_id, suffix, r["entry_key"])
            db[k] = {"valid_votes_assessment": r["valid_votes_assessment"]}

    n_files = len(names)
    joined = 0
    C: Counter = Counter()          # all counters, by name
    for nm in names:
        month, arxiv_id, suffix = NAME.match(nm).groups()
        first_run = load_records(Path(FIRST_RUN_DIR) / f"{nm}.jsonl")
        second_run = load_records(Path(SECOND_RUN_DIR) / f"{nm}.jsonl")
        for key in set(first_run) & set(second_run):
            rec_first, rec_second = first_run[key], second_run[key]
            joined += 1
            final_first, final_second = rec_first.get("final_assessment"), rec_second.get("final_assessment")
            strict_first, strict_second = final_first == "predatory", final_second == "predatory"
            broad_first, broad_second = broad(rec_first), broad(rec_second)
            C["flip_any"] += final_first != final_second
            d = db.get((month, arxiv_id, suffix, key[0]))
            confirmed = d is not None
            C["confirmed_cited"] += confirmed
            # strict / broad flags from the JSONL records, all joined entries
            C["strict_main"] += strict_first; C["strict_reeval"] += strict_second; C["strict_both"] += strict_first and strict_second
            C["strict_either"] += strict_first or strict_second
            C["broad_main"] += broad_first; C["broad_reeval"] += broad_second; C["broad_both"] += broad_first and broad_second
            C["broad_either"] += broad_first or broad_second
            flagged_either = strict_first or strict_second or broad_first or broad_second
            C["flagged_either"] += flagged_either
            if flagged_either:
                C["flagged_flip_final_assessment"] += final_first != final_second
                C["flagged_flip_strict_status"] += strict_first != strict_second
                C["flagged_flip_broad_status"] += broad_first != broad_second
                C["flagged_flip_any_status"] += (final_first != final_second) or (broad_first != broad_second)
            C["to_or_from_predatory_flips"] += (final_first != final_second) and (strict_first or strict_second)
            # confirmed-cited (primary population): flags from the records and flags after removal of those dropped by the identity check
            if confirmed:
                full = (month, arxiv_id, suffix, key[0])
                strict_after_check_first = strict_first and full not in strict_exclude_keys
                broad_after_check_first = broad_first and full not in broad_drop_keys
                C["conf_strict_main_raw"] += strict_first; C["conf_strict_reeval_raw"] += strict_second; C["conf_strict_both_raw"] += strict_first and strict_second
                C["conf_broad_main_raw"] += broad_first; C["conf_broad_reeval_raw"] += broad_second; C["conf_broad_both_raw"] += broad_first and broad_second
                C["conf_strict_main_corrected"] += strict_after_check_first
                C["conf_strict_reeval_corrected"] += strict_second and full not in strict_exclude_keys
                C["conf_strict_both_corrected"] += strict_after_check_first and (strict_second and full not in strict_exclude_keys)
                C["conf_broad_main_corrected"] += broad_after_check_first
                C["conf_broad_reeval_corrected"] += broad_second and full not in broad_drop_keys
                C["conf_broad_both_corrected"] += broad_after_check_first and (broad_second and full not in broad_drop_keys)
                C["conf_resolved_eligible"] += d["valid_votes_assessment"] in RESOLVED

    # ---- sampling frame: confirmed-cited, strict and broad references in files inside each month's 25th-75th percentile band ----
    files = conn.execute("SELECT month, name, entry_count FROM files WHERE category = 'assessed'").fetchall()
    by_month: dict[str, list[int]] = defaultdict(list)
    for r in files:
        by_month[r["month"]].append(r["entry_count"])
    band: dict[str, tuple[int, int]] = {}
    for m, v in by_month.items():
        v.sort()
        band[m] = (v[int(0.25 * (len(v) - 1))], v[int(0.75 * (len(v) - 1))])
    in_band = {(r["month"], r["name"]) for r in files if band[r["month"]][0] <= r["entry_count"] <= band[r["month"]][1]}
    files_total, files_in_band = len(files), len(in_band)
    frame = Counter()
    for r in conn.execute(
        "SELECT e.month, e.arxiv_id || '-' || e.suffix AS name, e.final_assessment, "
        "CAST(e.n_predatory_list_hits AS INTEGER) AS h, "
        "EXISTS (SELECT 1 FROM strict_exclude x WHERE x.month=e.month AND x.arxiv_id=e.arxiv_id AND x.suffix=e.suffix AND x.entry_key=e.entry_key) AS sx, "
        "EXISTS (SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id AND b.suffix=e.suffix AND b.entry_key=e.entry_key) AS bd "
        "FROM entries_confirmed_all e"
    ):
        inside = (r["month"], r["name"]) in in_band
        if r["final_assessment"] == "predatory" and not r["sx"]:
            frame["strict"] += 1; frame["strict_in_band"] += inside
        if r["h"] > 0 and not r["bd"]:
            frame["broad"] += 1; frame["broad_in_band"] += inside

    n_conf = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all").fetchone()["n"]
    resolved_pop = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE final_assessment_valid_votes IN "
        "('legitimate','predatory','suspicious','insufficient_data')").fetchone()["n"]
    out = {
        "n_files": n_files, "n_joined": joined, "counts": dict(C),
        "primary_population": {"confirmed_cited": n_conf, "resolved_eligible": resolved_pop},
        "sampling_frame": {"files_total": files_total, "files_in_band": files_in_band, **dict(frame)},
    }
    stats_common.write_json("reassessment_sample_composition", out)
    print(json.dumps(out, indent=2))

    i = lambda v: {"kind": "int", "value": int(v)}  # noqa: E731
    numbers = {"reevalsample_n_files": i(n_files), "reevalsample_n_joined": i(joined),
               "reevalsample_n_confirmed_cited": i(C["confirmed_cited"]),
               "reevalsample_n_resolved_eligible": i(C["conf_resolved_eligible"]),
               "reevalsample_confirmed_share_of_population": {"kind": "ratio", "num": C["confirmed_cited"], "den": n_conf, "decimals": 3},
               "reevalsample_resolved_share_of_population": {"kind": "ratio", "num": C["conf_resolved_eligible"], "den": resolved_pop, "decimals": 3},
               "reevalsample_n_flips": i(C["flip_any"])}
    for k in ("strict_main", "strict_reeval", "strict_both", "strict_either", "broad_main", "broad_reeval", "broad_both", "broad_either",
              "flagged_either", "flagged_flip_final_assessment", "flagged_flip_strict_status", "flagged_flip_broad_status",
              "flagged_flip_any_status", "to_or_from_predatory_flips",
              "conf_strict_main_raw", "conf_strict_reeval_raw", "conf_strict_both_raw", "conf_broad_main_raw",
              "conf_broad_reeval_raw", "conf_broad_both_raw", "conf_strict_main_corrected", "conf_strict_reeval_corrected",
              "conf_strict_both_corrected", "conf_broad_main_corrected", "conf_broad_reeval_corrected", "conf_broad_both_corrected"):
        numbers[f"reevalsample_{k}_n"] = i(C[k])
    numbers.update({
        "reevalsample_frame_files_total": i(files_total), "reevalsample_frame_files_in_band": i(files_in_band),
        "reevalsample_frame_strict_in_band_n": i(frame["strict_in_band"]), "reevalsample_frame_strict_n": i(frame["strict"]),
        "reevalsample_frame_broad_in_band_n": i(frame["broad_in_band"]), "reevalsample_frame_broad_n": i(frame["broad"]),
    })
    stats_common.write_numbers("reassessment_sample_composition", numbers)


if __name__ == "__main__":
    main()
