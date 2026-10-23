#!/usr/bin/env python3
"""Builds a synthetic .bib file with one @article per row of the Retraction Watch Hijacked Journal Checker, using the
identity of the listed journal: journal = title, issn = first valid ISSN parsed from the hijacked-ISSN column (no issn
field if there is none), url = the journal's URL. Each row is classified by the relation of the hijacked ISSN column to
the original-ISSN column (exact_string_match, overlapping_normalized_match, hijacked_issn_missing, different_issn).
Input: EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/raw_data/retraction_watch_hijacked_checker.csv (name as listed in the
Makefile). Writes bib_input_clone_identity/retraction_watch_clone_identity.bib there, and
numbers/build_retraction_watch_clone_identity_bib.numbers.json (keys rwclone_*: row count and the counts of the four ISSN
categories, which add up to the row count). With --write-citation-keys it also writes clone_identity_issn_categories.json
(entry key and ISSN category of each entry; read by analyze_identity_and_negative_control.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from stats_common import required_path_env, write_numbers

BASE = required_path_env("EVAL_OUTPUT_DIR") / "hijacked_doaj_benchmark"
INPUT_DIR = BASE / "raw_data"
BIB_INPUT = BASE / "bib_input_clone_identity"

ISSN_PATTERN = re.compile(r"^\d{4}-?\d{3}[\dXx]$")


def escape_bib_value(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("{", "(").replace("}", ")")
    return " ".join(value.split())


def parse_valid_issns(issn_field: str) -> set[str]:
    if not issn_field:
        return set()
    out = set()
    for part in re.split(r"[,;/.]", issn_field):
        candidate = re.sub(r"[^0-9Xx-]", "", part.strip())
        if ISSN_PATTERN.match(candidate):
            if "-" not in candidate:
                candidate = candidate[:4] + "-" + candidate[4:]
            out.add(candidate.upper())
    return out


def first_valid_issn(issn_field: str) -> str:
    issns = parse_valid_issns(issn_field)
    return sorted(issns)[0] if issns else ""


def categorize(hijacked_field: str, original_field: str) -> str:
    if hijacked_field.strip() == original_field.strip():
        return "exact_string_match"
    h, o = parse_valid_issns(hijacked_field), parse_valid_issns(original_field)
    if h and o and (h & o):
        return "overlapping_normalized_match"
    if not h:
        return "hijacked_issn_missing"
    return "different_issn"


def write_bib(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(f"@article{{{e['key']},\n")
            f.write(f"  journal = {{{escape_bib_value(e['journal'])}}},\n")
            if e.get("issn"):
                f.write(f"  issn = {{{escape_bib_value(e['issn'])}}},\n")
            if e.get("url"):
                f.write(f"  url = {{{escape_bib_value(e['url'])}}},\n")
            f.write(f"  title = {{{e['key']} clone-identity benchmark probe entry}},\n")
            f.write("  year = {2026}\n")
            f.write("}\n\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-citation-keys", action="store_true",
                        help="also write clone_identity_issn_categories.json (entry key and ISSN category)")
    args = parser.parse_args()

    with (INPUT_DIR / "retraction_watch_hijacked_checker.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    data = rows[2:]  # row 0 is a banner, row 1 the header

    entries = []
    category_counts: dict[str, int] = {}
    category_by_key = {}
    for i, r in enumerate(data, start=1):
        if len(r) < 6 or not r[1].strip():
            continue
        hijacked_title, url_hijacked, issn_hijacked_field = r[1], r[2], r[3]
        issn_original_field = r[5]

        category = categorize(issn_hijacked_field, issn_original_field)
        category_counts[category] = category_counts.get(category, 0) + 1

        key = f"rwclone{i:04d}"
        entries.append({
            "key": key,
            "journal": hijacked_title,
            "issn": first_valid_issn(issn_hijacked_field),
            "url": url_hijacked.strip(),
        })
        category_by_key[key] = category

    write_bib(entries, BIB_INPUT / "retraction_watch_clone_identity.bib")
    if args.write_citation_keys:
        (BASE / "clone_identity_issn_categories.json").write_text(
            json.dumps(category_by_key, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    print(f"built {len(entries)} entries")
    print("ISSN-relationship category counts:", json.dumps(category_counts, indent=2))

    numbers_payload = {"rwclone_n_total": {"kind": "int", "value": len(entries)}}
    for cat in ("exact_string_match", "overlapping_normalized_match", "hijacked_issn_missing", "different_issn"):
        numbers_payload[f"rwclone_{cat}_n"] = {"kind": "int", "value": category_counts.get(cat, 0)}
    write_numbers("build_retraction_watch_clone_identity_bib", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
