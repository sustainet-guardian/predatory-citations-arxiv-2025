#!/usr/bin/env python3
"""Builds the SQLite database EVAL_DB_PATH with the tables `entries` and `files` and their indexes.

Inputs: the manifest EVAL_OUTPUT_DIR/analysis_manifest.csv, written by build_analysis_manifest.py (columns month, name, category,
entry_count, bib_size_bytes, result_file; category is assessed, string_comment_preamble_only, empty_or_no_bibtex_syntax or
unassessed_with_entries; this script reads all of them except result_file); for every manifest row of category `assessed` the
assessment results EVAL_RESULTS_ROOT/<month>__<name>.jsonl (name = <arxiv_id>-<n>), the .bib file EVAL_BIB_ROOT/<month>/<name>.bib
and, if it exists, the citation log EVAL_BIB_ROOT/<month>/<name>-cited.csv (columns citation_key and count). The .bib file is parsed
with the parser of the tool (aletheia-probe, importable or found under EVAL_ALETHEIA_PROBE_SRC) to re-read entry_type and pub_year.

`entries`: one row per assessed entry (columns: the fields of the results line, entry_type, pub_year, is_cited = 1 when the key is
in the citation log, cited_count = its count, has_cited_sidecar = 1 when the file has a citation log). For a file with a citation
log only the entries that the log lists get a row; for a file without one every entry gets a row with is_cited = 0.
`files`: one row per manifest row (month, name, category, entry_count, bib_size_bytes) plus cited_entry_count (entries of the results
file that the log lists) and has_cited_sidecar; both are NULL for the files that were not processed.

The script only appends: it refuses to run when EVAL_DB_PATH already holds rows in `entries` or `files`, unless --fresh is given,
which deletes the database file first and builds it again from the start. --limit N builds a test database from the first N
assessed files in the order of their names `<month>__<name>`; `files` then holds only the manifest rows of those files.
--workers N sets the size of the process pool (default: the number of CPUs of the machine, 4 if that is unknown).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import io
import json
import os
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pybtex.io
from pybtex.database import parse_file
from pybtex.scanner import PybtexError, PybtexSyntaxError

from result_line import result_line_columns
from stats_common import required_path_env

# The tool (aletheia-probe) is imported from EVAL_ALETHEIA_PROBE_SRC if set, otherwise it must be importable.
_aletheia_probe_src = os.environ.get("EVAL_ALETHEIA_PROBE_SRC")
if _aletheia_probe_src:
    sys.path.insert(0, _aletheia_probe_src)
from aletheia_probe.bibtex_parser import BibtexParser  # noqa: E402

BIB_ROOT = required_path_env("EVAL_BIB_ROOT")
RESULTS_ROOT = required_path_env("EVAL_RESULTS_ROOT")
DB_PATH = required_path_env("EVAL_DB_PATH")

ENTRY_COLUMNS = [
    "month", "arxiv_id", "suffix", "entry_key", "venue_raw", "venue_type",
    "issn", "eissn", "doi", "entry_type", "pub_year",
    "state", "final_assessment", "confidence", "overall_score",
    "is_suspicious", "has_conflict", "predatory_votes", "legitimate_votes",
    "n_predatory_list_hits", "predatory_list_hits",
    "is_hijacked", "is_retracted", "retraction_risk_level",
    "matched_venue_name", "matched_publisher",
    "is_cited", "cited_count", "has_cited_sidecar",
]

CREATE_ENTRIES_SQL = f"""
CREATE TABLE IF NOT EXISTS entries (
    {", ".join(f"{c} TEXT" if c not in (
        "confidence", "overall_score"
    ) else f"{c} REAL" for c in ENTRY_COLUMNS)}
)
"""


def parse_name(name: str) -> tuple[str, str, str]:
    """'2501__00001-1' -> (month, arxiv_id, suffix)."""
    month, rest = name.split("__", 1)
    arxiv_id, suffix = rest.rsplit("-", 1)
    return month, arxiv_id, suffix


def load_cited_csv(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    out: dict[str, int] = {}
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("citation_key")
            if key is None:
                continue
            try:
                out[key] = int(row.get("count", 0) or 0)
            except ValueError:
                out[key] = 0
    return out


_ENCODINGS = ["utf-8", "latin-1", "cp1252"]


def _parse_bib(path: Path, relax_parsing: bool) -> dict:
    """Parses a .bib file with pybtex directly (encoding fallback utf-8, latin-1, cp1252); returns entry_key -> pybtex Entry."""
    last_error = None
    for encoding in _ENCODINGS:
        try:
            # pybtex prints parse errors of malformed entries to its own stderr stream; both streams are discarded.
            with contextlib.redirect_stderr(io.StringIO()), BibtexParser._configure_pybtex(relax_parsing):
                pybtex.io.stderr = sys.stderr
                bib_data = parse_file(str(path), encoding=encoding)
                return bib_data.entries
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except PybtexSyntaxError as e:
            raise ValueError(f"Invalid BibTeX syntax in {path.name}: {e}") from e
        except PermissionError:
            raise
        except PybtexError as e:
            last_error = e
            continue
    raise ValueError(f"Could not parse {path.name}: {last_error}")


def load_bib_entries(path: Path) -> dict:
    """entry_key -> pybtex Entry, trying strict then relaxed parsing."""
    try:
        return _parse_bib(path, relax_parsing=False)
    except Exception:
        try:
            return _parse_bib(path, relax_parsing=True)
        except Exception:
            return {}


def entry_type_and_year(entry) -> tuple[str | None, str | None]:
    year = None
    try:
        if "year" in entry.fields:
            year = str(entry.fields["year"]).strip() or None
    except Exception:
        pass
    return getattr(entry, "type", None), year


def process_file(name: str) -> tuple[list[tuple], dict[str, Any]] | None:
    month, arxiv_id, suffix = parse_name(name)
    jsonl_path = RESULTS_ROOT / f"{name}.jsonl"
    bib_path = BIB_ROOT / month / f"{arxiv_id}-{suffix}.bib"
    cited_path = BIB_ROOT / month / f"{arxiv_id}-{suffix}-cited.csv"

    if not jsonl_path.exists():
        return None

    cited_map = load_cited_csv(cited_path)
    has_citation_log = cited_path.exists()
    bib_entries = load_bib_entries(bib_path)

    rows: list[tuple] = []
    n_cited = 0
    n_entries = 0

    with jsonl_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_entries += 1

            entry_key = rec.get("entry_key", "")
            is_cited = entry_key in cited_map
            if is_cited:
                n_cited += 1

            # With a -cited.csv citation log only the cited entries enter the entries table.
            if has_citation_log and not is_cited:
                continue

            entry = bib_entries.get(entry_key)
            entry_type, pub_year = entry_type_and_year(entry) if entry is not None else (None, None)

            values = result_line_columns(rec)
            values.update(
                month=month, arxiv_id=arxiv_id, suffix=suffix, entry_key=entry_key, entry_type=entry_type, pub_year=pub_year,
                is_cited=int(is_cited), cited_count=cited_map.get(entry_key, 0), has_cited_sidecar=int(has_citation_log),
            )
            rows.append(tuple(values[c] for c in ENTRY_COLUMNS))

    summary = {
        "name": name, "cited_entry_count": n_cited,
        "has_cited_sidecar": int(has_citation_log), "n_entries": n_entries,
    }
    return rows, summary


def load_manifest() -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    manifest_path = required_path_env("EVAL_OUTPUT_DIR") / "analysis_manifest.csv"
    with manifest_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = f"{row['month']}__{row['name']}"
            by_name[name] = row
    return by_name


async def _async_build(args, assessed_names: list[str]) -> tuple[int, int, dict[str, Any]]:
    """Processes all files in a process pool (asyncio + run_in_executor, bounded by a semaphore) and inserts the rows."""
    total_rows = 0
    processed_files = 0
    file_summaries: dict[str, Any] = {}
    progress_every = max(1, min(2000, len(assessed_names) // 10 or 1))
    loop = asyncio.get_running_loop()

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(CREATE_ENTRIES_SQL)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                month TEXT, name TEXT, category TEXT, entry_count INTEGER,
                bib_size_bytes INTEGER, cited_entry_count INTEGER,
                has_cited_sidecar INTEGER
            )
        """)
        conn.commit()

        placeholders = ",".join("?" for _ in ENTRY_COLUMNS)
        insert_sql = f"INSERT INTO entries ({', '.join(ENTRY_COLUMNS)}) VALUES ({placeholders})"

        sem = asyncio.Semaphore(args.workers)

        async def run_one(name: str):
            async with sem:
                result = await loop.run_in_executor(executor, process_file, name)
                return name, result

        tasks = [asyncio.create_task(run_one(name)) for name in assessed_names]
        for coro in asyncio.as_completed(tasks):
            name, result = await coro
            if result is None:
                continue
            rows, summary = result
            if rows:
                conn.executemany(insert_sql, rows)
                total_rows += len(rows)
            file_summaries[name] = summary
            processed_files += 1
            if processed_files % progress_every == 0:
                conn.commit()
                print(f"  ... {processed_files}/{len(assessed_names)} files, {total_rows} entry-rows so far", flush=True)

        conn.commit()
    return total_rows, processed_files, file_summaries


def refuse_if_not_empty() -> None:
    """Exits when the database at DB_PATH already has rows in `entries` or `files`."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table in ("entries", "files"):
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists and conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
                raise SystemExit(f"{DB_PATH}: table {table} is not empty; use --fresh to delete the database and build it again")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="process only the first N assessed files (for testing)")
    parser.add_argument(
        "--workers", type=int, default=os.cpu_count() or 4,
        help="process pool size; defaults to the local CPU count",
    )
    parser.add_argument("--fresh", action="store_true", help="delete the database file first and build it again")
    args = parser.parse_args()

    manifest = load_manifest()
    assessed_names = sorted(n for n, r in manifest.items() if r["category"] == "assessed")
    if args.limit:
        assessed_names = assessed_names[: args.limit]
        manifest = {name: manifest[name] for name in assessed_names}

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if args.fresh and DB_PATH.exists():
        DB_PATH.unlink()
    if DB_PATH.exists():
        refuse_if_not_empty()

    total_rows, processed_files, file_summaries = asyncio.run(_async_build(args, assessed_names))

    conn = sqlite3.connect(str(DB_PATH))

    # files table: every manifest row (with --limit: the rows of the processed files), not only the assessed ones
    file_rows = []
    for name, r in manifest.items():
        entry_count = int(r["entry_count"]) if r["entry_count"] else 0
        summary = file_summaries.get(name)
        cited_count = summary["cited_entry_count"] if summary else None
        has_citation_log = summary["has_cited_sidecar"] if summary else None
        month = r["month"]
        file_rows.append((
            month, r["name"], r["category"], entry_count,
            int(r["bib_size_bytes"]) if r["bib_size_bytes"] else 0,
            cited_count, has_citation_log,
        ))
    conn.executemany(
        "INSERT INTO files (month, name, category, entry_count, bib_size_bytes, cited_entry_count, has_cited_sidecar) VALUES (?,?,?,?,?,?,?)",
        file_rows,
    )
    conn.commit()

    print("Building indexes...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_final ON entries(final_assessment, is_cited)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_month ON entries(month)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_venue ON entries(matched_venue_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_paper ON entries(month, arxiv_id, suffix)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(month, name)")
    conn.commit()
    conn.close()

    print(f"DONE. files_processed={processed_files} entry_rows={total_rows}")


if __name__ == "__main__":
    main()
