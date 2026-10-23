#!/usr/bin/env python3
"""Builds synthetic .bib files (one @article per benchmark journal, journal name and ISSN only) for three benchmark
sets. Inputs are files under EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/raw_data/ with the names the Makefile lists:
retraction_watch_hijacked_checker.csv (Retraction Watch Hijacked Journal Checker; the hijacked title and ISSN are used),
doaj_withdrawn_until_2024_01.csv and doaj_withdrawn_from_2024_02.csv (the two DOAJ withdrawal change logs; columns title,
ISSN, date removed, reason; the first row of each is a banner) and doaj_full_journal_list.csv (DOAJ public journal list;
random sample of NEGATIVE_CONTROL_SAMPLE_SIZE journals, print ISSN preferred, e-ISSN as fallback, seed 42). The DOAJ
withdrawn-journal set (`doaj_best_practice`) keeps the rows of the two change logs (first log first) whose reason
contains "Journal not adhering to Best practice" (case-insensitive) and removes repeated rows (same title, ISSN, date and
reason); it has one entry per remaining row with a title, not deduplicated by ISSN. Writes bib_input/<set>/<set>.bib into
EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/ and prints the entry count per set. The assessment tool's mass evaluation of
these .bib files writes mass_eval_output/<set>__<set>.jsonl, which analyze_hijacked_doaj_benchmark.py reads.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

from stats_common import required_path_env

BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
INPUT_DIR = BASE / "raw_data"
BIB_INPUT = BASE / "bib_input"
SEED = 42
NEGATIVE_CONTROL_SAMPLE_SIZE = 320
DOAJ_WITHDRAWAL_LOGS = ["doaj_withdrawn_until_2024_01.csv", "doaj_withdrawn_from_2024_02.csv"]
DOAJ_REASON_TEXT = "journal not adhering to best practice"


def escape_bib_value(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("{", "(").replace("}", ")")
    value = " ".join(value.split())
    return value


def first_issn(issn_field: str) -> str:
    if not issn_field:
        return ""
    return issn_field.split(",")[0].strip()


def write_bib(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(f"@article{{{e['key']},\n")
            f.write(f"  journal = {{{escape_bib_value(e['journal'])}}},\n")
            if e.get("issn"):
                f.write(f"  issn = {{{escape_bib_value(e['issn'])}}},\n")
            f.write(f"  title = {{{e['key']} benchmark probe entry}},\n")
            f.write("  year = {2026}\n")
            f.write("}\n\n")


def build_retraction_watch() -> list[dict]:
    with (INPUT_DIR / "retraction_watch_hijacked_checker.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    data = rows[2:]  # row0=banner, row1=header
    entries = []
    for i, r in enumerate(data, start=1):
        if len(r) < 6 or not r[1].strip():
            continue
        entries.append({
            "key": f"rw{i:04d}",
            "journal": r[1],
            "issn": first_issn(r[3]),
        })
    return entries


def read_withdrawal_log(path: Path) -> list[list[str]]:
    """Data rows of a DOAJ change log: title, ISSN, date removed, reason (the banner row is dropped)."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return [r for r in rows[1:] if len(r) >= 4]


def doaj_best_practice_rows() -> list[dict]:
    """Rows of both change logs whose reason contains the best-practice text, without repeated rows."""
    rows = []
    for name in DOAJ_WITHDRAWAL_LOGS:
        rows.extend(read_withdrawal_log(INPUT_DIR / name))
    matched = [r for r in rows if DOAJ_REASON_TEXT in r[3].lower()]
    seen = set()
    out = []
    for title, issn, date_removed, reason in (r[:4] for r in matched):
        row_id = (title, issn, date_removed, reason.lower())
        if row_id in seen:
            continue
        seen.add(row_id)
        out.append({"journal_title": title, "issn": issn, "date_removed": date_removed, "reason": reason})
    return out


def build_doaj_best_practice() -> list[dict]:
    data = doaj_best_practice_rows()
    entries = []
    for i, r in enumerate(data, start=1):
        if not r["journal_title"].strip():
            continue
        entries.append({
            "key": f"doaj{i:04d}",
            "journal": r["journal_title"],
            "issn": first_issn(r["issn"]),
        })
    return entries


def build_negative_control() -> list[dict]:
    with (INPUT_DIR / "doaj_full_journal_list.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        data = list(reader)
    rng = random.Random(SEED)
    sample = rng.sample(data, min(NEGATIVE_CONTROL_SAMPLE_SIZE, len(data)))
    entries = []
    for i, r in enumerate(sample, start=1):
        title = r.get("Journal title", "").strip()
        issn = r.get("Journal ISSN (print version)", "").strip() or r.get("Journal EISSN (online version)", "").strip()
        if not title:
            continue
        entries.append({
            "key": f"neg{i:04d}",
            "journal": title,
            "issn": issn,
        })
    return entries


def main() -> None:
    rw_entries = build_retraction_watch()
    write_bib(rw_entries, BIB_INPUT / "retraction_watch" / "retraction_watch.bib")

    doaj_entries = build_doaj_best_practice()
    write_bib(doaj_entries, BIB_INPUT / "doaj_best_practice" / "doaj_best_practice.bib")

    neg_entries = build_negative_control()
    write_bib(neg_entries, BIB_INPUT / "negative_control" / "negative_control.bib")

    print(f"retraction_watch: {len(rw_entries)} entries")
    print(f"doaj_best_practice: {len(doaj_entries)} entries")
    print(f"negative_control: {len(neg_entries)} entries")


if __name__ == "__main__":
    main()
