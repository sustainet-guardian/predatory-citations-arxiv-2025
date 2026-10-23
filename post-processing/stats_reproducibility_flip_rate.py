#!/usr/bin/env python3
"""Compares two assessment runs: for every file in EVAL_SECOND_RUN_ROOT, the records are joined with those of
EVAL_RESULTS_ROOT on (entry_key, venue_raw) (load_assessment_records.load_records) and the entries whose final_assessment differs are
counted ("flips"); reprod_flip_pct is the flips as a percentage of the joined entries. Writes reproducibility_flip_rate.json
to EVAL_OUTPUT_DIR/output_stats_reproducibility_flip_rate/ and numbers/stats_reproducibility_flip_rate.numbers.json (keys
reprod_*).
"""
from __future__ import annotations

import stats_common
from stats_common import required_path_env, write_json, write_numbers
from load_assessment_records import load_records

FIRST_RUN_DIR = required_path_env("EVAL_RESULTS_ROOT")
SECOND_RUN_DIR = required_path_env("EVAL_SECOND_RUN_ROOT")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_stats_reproducibility_flip_rate"


def main() -> None:
    names = sorted(p.stem for p in SECOND_RUN_DIR.glob("*.jsonl"))
    print(f"files of the second run: {len(names)}", flush=True)

    total_joined = 0
    total_flips = 0
    for name in names:
        first_run = load_records(FIRST_RUN_DIR / f"{name}.jsonl")
        second_run = load_records(SECOND_RUN_DIR / f"{name}.jsonl")
        shared = set(first_run) & set(second_run)
        total_joined += len(shared)
        for key in shared:
            rec_first, rec_second = first_run[key], second_run[key]
            if rec_first.get("final_assessment") != rec_second.get("final_assessment"):
                total_flips += 1

    out = {
        "n_files_second_run": len(names),
        "n_entries_joined": total_joined,
        "n_flips": total_flips,
    }
    write_json("reproducibility_flip_rate", out)
    print(f"n_entries_joined={total_joined} n_flips={total_flips}", flush=True)

    numbers_payload = {
        "reprod_n_files": {"kind": "int", "value": len(names)},
        "reprod_n_entries_joined": {"kind": "int", "value": total_joined},
        "reprod_n_flips": {"kind": "int", "value": total_flips},
        "reprod_flip_pct": {"kind": "ratio", "num": total_flips, "den": total_joined, "decimals": 2},
    }
    write_numbers("stats_reproducibility_flip_rate", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
