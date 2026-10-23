#!/usr/bin/env python3
"""Final disposition of the negative-control sample of DOAJ journals (a random sample of DOAJ's public journal list):
records that hit a predatory-list watchlist are set aside; each remaining record is assigned to found-beyond-doaj /
found-only-by-doaj / found-by-neither (analyze_identity_and_negative_control.negative_control_bucket) and
cross-tabulated against final_assessment. Reads negative_control__negative_control.jsonl under
EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/mass_eval_output. Writes
output_negative_control_disposition/negative_control_disposition.json (counts only) and
numbers/negative_control_disposition.numbers.json (keys negctrl_*: sample size, records set aside, records left).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import stats_common
from analyze_hijacked_doaj_benchmark import watchlist_overlap
from analyze_identity_and_negative_control import negative_control_bucket
from stats_common import required_path_env

BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
NEG_JSONL = BASE / "mass_eval_output" / "negative_control__negative_control.jsonl"
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_negative_control_disposition"


def main() -> None:
    recs = [json.loads(l) for l in NEG_JSONL.open(encoding="utf-8") if l.strip()]

    excluded = [r for r in recs if watchlist_overlap(r)]
    no_list_hit = [r for r in recs if not watchlist_overlap(r)]

    crosstab: dict[str, Counter] = defaultdict(Counter)
    for r in no_list_hit:
        bucket = negative_control_bucket(r)
        fa = r.get("final_assessment")
        crosstab[bucket][fa] += 1

    table = {
        bucket: dict(counts) for bucket, counts in crosstab.items()
    }

    out = {
        "total_drawn": len(recs),
        "excluded_for_watchlist_hit": {
            "n": len(excluded),
        },
        "n_no_list_hit": len(no_list_hit),
        "crosstab_bucket_by_final_assessment": table,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "negative_control_disposition.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("negative_control_disposition", {
        "negctrl_n_clean": {"kind": "int", "value": len(no_list_hit)},
        "negctrl_n_total_drawn": {"kind": "int", "value": len(recs)},
        "negctrl_n_excluded_watchlist_hit": {"kind": "int", "value": len(excluded)},
    })


if __name__ == "__main__":
    main()
