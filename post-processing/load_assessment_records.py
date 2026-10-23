#!/usr/bin/env python3
"""Helper module (no main, no output): load_records(path) reads the per-entry records of one assessment run (a JSONL file named
<month>__<id>-<n>.jsonl) into a dictionary keyed on (entry_key, venue_raw); a missing file gives an empty dictionary. It reads
no environment variable. Imported by stats_reproducibility_flip_rate.py and reassessment_sample_composition.py, which join the
records of two assessment runs.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_records(path: Path) -> dict[tuple[str, str], dict]:
    records: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = (rec.get("entry_key", ""), rec.get("venue_raw", ""))
            records[key] = rec
    return records
