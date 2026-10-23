#!/usr/bin/env python3
"""Classifies the large files (assessed files with entry_count >= LARGE_FILE_ENTRIES) into bibliography families and counts their
entries. A large file is "acl" if its .bib text contains an ACL Anthology marker ("aclanthology.org" or "aclweb.org/anthology"),
else "cryptobib" if it contains "eprint.iacr.org", else "other". Reads EVAL_DB_PATH (table files) and the .bib files under
EVAL_BIB_ROOT. Writes numbers/large_file_families.numbers.json (keys megafam_*: the share of each family in the entries of the large
files found under EVAL_BIB_ROOT) and EVAL_OUTPUT_DIR/output_large_file_families/large_file_families.json.
"""
from __future__ import annotations

import stats_common
from stats_common import LARGE_FILE_ENTRIES, get_conn, pct, required_path_env, write_json, write_numbers

BIB_ROOT = required_path_env("EVAL_BIB_ROOT")
stats_common.OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_large_file_families"


def classify(text: str) -> str:
    if "aclanthology.org" in text or "aclweb.org/anthology" in text:
        return "acl"
    if "eprint.iacr.org" in text:
        return "cryptobib"
    return "other"


def main() -> None:
    conn = get_conn()
    rows = conn.execute(
        "SELECT month, name, entry_count FROM files WHERE category='assessed' AND entry_count >= ?", (LARGE_FILE_ENTRIES,)
    ).fetchall()
    print(f"large files (entry_count >= {LARGE_FILE_ENTRIES}, assessed): {len(rows)}", flush=True)

    counts: dict[str, int] = {"acl": 0, "cryptobib": 0, "other": 0}
    entries: dict[str, int] = {"acl": 0, "cryptobib": 0, "other": 0}
    n_missing = 0
    for r in rows:
        p = BIB_ROOT / r["month"] / f"{r['name']}.bib"
        if not p.exists():
            n_missing += 1
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fam = classify(text)
        counts[fam] += 1
        entries[fam] += r["entry_count"]

    total_n = sum(counts.values())
    total_entries = sum(entries.values())
    assert total_n == len(rows) - n_missing

    out = {
        "n_large_files": len(rows),
        "n_missing_bib_file": n_missing,
        "total_entries": total_entries,
        "families": {
            fam: {
                "n_files": counts[fam], "n_entries": entries[fam],
                "pct_of_large_file_entries": pct(entries[fam], total_entries),
            }
            for fam in ("acl", "cryptobib", "other")
        },
    }
    write_json("large_file_families", out)
    print(out, flush=True)

    numbers_payload = {
        "megafam_acl_pct": {"kind": "ratio", "num": entries["acl"], "den": total_entries, "decimals": 2},
        "megafam_cryptobib_pct": {"kind": "ratio", "num": entries["cryptobib"], "den": total_entries, "decimals": 2},
        "megafam_other_pct": {"kind": "ratio", "num": entries["other"], "den": total_entries, "decimals": 2},
    }
    write_numbers("large_file_families", numbers_payload)
    print(f"wrote {len(numbers_payload)} numbers", flush=True)


if __name__ == "__main__":
    main()
