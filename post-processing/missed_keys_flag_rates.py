#!/usr/bin/env python3
"""Number of strict-predatory citations among the missed-keys rows after the identity check, with the number of
missed-keys rows. Reads rates_with_missed_keys_rows.json (written by rates_with_missed_keys_rows.py under
EVAL_OUTPUT_DIR/output_rates_with_missed_keys_rows). Writes
EVAL_OUTPUT_DIR/output_missed_keys_flag_rates/missed_keys_flag_rates.json and numbers/missed_keys_flag_rates.numbers.json with
  missedkeys_n_population            number of missed-keys rows;
  missedkeys_n_strict                strict-predatory citations among them after the identity check.
"""
from __future__ import annotations

import json

import stats_common
from stats_common import required_path_env

SOURCE_JSON = (
    required_path_env("EVAL_OUTPUT_DIR")
    / "output_rates_with_missed_keys_rows"
    / "rates_with_missed_keys_rows.json"
)
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_missed_keys_flag_rates"
stats_common.OUTPUT_DIR = OUTPUT_DIR


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    src = json.loads(SOURCE_JSON.read_text())

    n_missed = src["population"]["n_missed_keys_rows_added"]
    assert n_missed > 0, "no missed-keys rows"

    cls = src["missed_keys_rows_identity_classification"]
    n_missed_strict = cls["n_strict_after_identity_check"]

    out = {
        "n_missed_keys_rows": n_missed,
        "n_strict_predatory_within_missed_keys_rows": n_missed_strict,
    }
    stats_common.write_json("missed_keys_flag_rates", out)
    print(json.dumps(out, indent=2, default=str))

    stats_common.write_numbers("missed_keys_flag_rates", {
        "missedkeys_n_population": {"kind": "int", "value": n_missed},
        "missedkeys_n_strict": {"kind": "int", "value": n_missed_strict},
    })


if __name__ == "__main__":
    main()
