#!/usr/bin/env python3
"""Extracts citation signals from a local copy of the arXiv sources: one archive per submission, <YYMM>/<NNNNN>.tar.gz under
--source-root (see README, step on obtaining the e-print archives); the archives are read, never extracted.
Per submission it records the keys of the .bbl (classic \\bibitem and biblatex \\entry formats), the keys used in \\cite-family
commands of the .tex files (natbib with up to two optional arguments, biblatex, \\onlinecite, apacite \\shortcite, \\combocite),
\\nocite{*} and explicit \\nocite keys, and per .bib file its suffix (position in archive order, from 1) and its entry keys.

Usage: extract_citation_keys.py --source-root DIR [--submissions CSV] [--limit N] [--workers N] [--skip-self-check]
[--write-citation-keys --output-path FILE.jsonl]. A self-check on a synthetic archive runs first. Prints the numbers of processed,
missing and failed archives.
- Without --submissions every file <YYMM>/*.tar.gz under --source-root is read (month directories are named by four digits); the
  submission id is the file name without .tar.gz. --limit N stops after N archives. With --submissions, --limit is ignored.
- --submissions CSV: comma-separated, two columns and no header line: month (YYMM) and submission id (the file name without
  .tar.gz); further columns are ignored. Only these archives are read; an id without a file is recorded with file_type "missing".
- --workers N: number of worker processes (default 3).
- With --write-citation-keys the script appends one JSON line per submission to --output-path (which must lie outside
  --source-root): month, arxiv_id, file_type (tar_gz, pdf_only, gzip_single_file, unknown, missing or error) and, for tar_gz,
  bbl_keys, tex_cite_keys, nocite_explicit_keys, nocite_star and bib_files. Re-running with the same path skips the submissions
  already in it. The file lists the keys of every submission; it is the input EVAL_BBL_JSONL_PATH of the scripts that use the
  citation signal and is not released.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

PDF_MAGIC = b"%PDF"
GZIP_MAGIC = b"\x1f\x8b"

# Natbib, biblatex, APS-style and apacite citation commands with 0, 1 or 2 optional bracket arguments.
# apacite's "short" forms (\shortcite, \shortciteA, \shortciteNP, ...) do not start with "cite", hence the
# separate shortcite\w* alternative.
CITE_PATTERN = re.compile(
    r"\\(cite\w*|textcite\w*|parencite\w*|autocite\w*|smartcite\w*|footcite\w*|fullcite\w*|onlinecite|shortcite\w*)"
    r"\*?\s*(?:\[[^\]]*\])?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}",
    re.IGNORECASE,
)
# \combocite{key1}{key2}[...] takes each key in its own brace group rather than one comma-separated group.
# The pattern captures the whole run of brace groups; extract_cite_keys() then pulls every {...} group out.
COMBOCITE_PATTERN = re.compile(r"\\combocite\w*((?:\s*\{[^}]*\})+)", re.IGNORECASE)
COMBOCITE_GROUP_PATTERN = re.compile(r"\{([^}]*)\}")
NOCITE_PATTERN = re.compile(r"\\nocite\s*\{([^}]+)\}", re.IGNORECASE)
BIBITEM_PATTERN = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")
BIBLATEX_ENTRY_PATTERN = re.compile(r"\\entry\{([^}]+)\}")
# .bib entry keys (same pattern as pre-check/arxiv_bib_extractor.py).
BIB_ENTRY_PATTERN = re.compile(r"@\w+\s*\{\s*([^,\s]+)", re.IGNORECASE)


def strip_latex_comments(text: str) -> str:
    """Removes unescaped %-comments per line (keeps \\% literal)."""
    out_lines = []
    for line in text.split("\n"):
        buf = []
        i = 0
        while i < len(line):
            if line[i] == "\\" and i + 1 < len(line):
                buf.append(line[i:i + 2])
                i += 2
                continue
            if line[i] == "%":
                break
            buf.append(line[i])
            i += 1
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore")


def extract_cite_keys(tex_text: str) -> tuple[set[str], set[str], bool]:
    """Returns (cite_keys, nocite_explicit_keys, nocite_star_found)."""
    text = strip_latex_comments(tex_text)
    cite_keys: set[str] = set()
    for m in CITE_PATTERN.finditer(text):
        for k in m.group(2).split(","):
            k = k.strip()
            if k:
                cite_keys.add(k)
    for m in COMBOCITE_PATTERN.finditer(text):
        for group in COMBOCITE_GROUP_PATTERN.finditer(m.group(1)):
            for k in group.group(1).split(","):
                k = k.strip()
                if k:
                    cite_keys.add(k)

    nocite_keys: set[str] = set()
    nocite_star = False
    for m in NOCITE_PATTERN.finditer(text):
        arg = m.group(1).strip()
        if arg == "*":
            nocite_star = True
            continue
        for k in arg.split(","):
            k = k.strip()
            if k:
                nocite_keys.add(k)

    return cite_keys, nocite_keys, nocite_star


def extract_bbl_keys(bbl_text: str) -> tuple[set[str], str]:
    bibitem_keys = set(BIBITEM_PATTERN.findall(bbl_text))
    biblatex_keys = set(BIBLATEX_ENTRY_PATTERN.findall(bbl_text))
    if bibitem_keys and biblatex_keys:
        fmt = "both"
    elif bibitem_keys:
        fmt = "bibitem"
    elif biblatex_keys:
        fmt = "biblatex_entry"
    else:
        fmt = "none"
    return bibitem_keys | biblatex_keys, fmt


def process_archive(archive_bytes: bytes) -> dict:
    header = archive_bytes[:4]
    if header.startswith(PDF_MAGIC):
        return {"file_type": "pdf_only"}

    if not header.startswith(GZIP_MAGIC):
        return {"file_type": "unknown"}

    try:
        tf = tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz")
    except tarfile.ReadError:
        return {"file_type": "gzip_single_file"}

    result = {
        "file_type": "tar_gz",
        "bbl_present": False, "bbl_format": "none", "bbl_keys": [],
        "tex_cite_keys": [], "nocite_star": False, "nocite_explicit_keys": [],
        "n_tex_files": 0, "bib_files": [],
    }
    try:
        members = tf.getmembers()

        # .bbl keys, pooled over all .bbl files of the archive
        bbl_members = [m for m in members if m.isfile() and m.name.lower().endswith(".bbl")]
        all_bbl_keys: set[str] = set()
        bbl_formats: set[str] = set()
        for m in bbl_members:
            f = tf.extractfile(m)
            if f is None:
                continue
            keys, fmt = extract_bbl_keys(decode(f.read()))
            all_bbl_keys |= keys
            if fmt != "none":
                bbl_formats.add(fmt)
        if bbl_members:
            result["bbl_present"] = True
            result["bbl_format"] = "+".join(sorted(bbl_formats)) if bbl_formats else "none"
            result["bbl_keys"] = sorted(all_bbl_keys)

        # .tex citation commands and \nocite, pooled over all .tex files
        tex_members = [m for m in members if m.isfile() and m.name.lower().endswith(".tex")]
        result["n_tex_files"] = len(tex_members)
        all_cite_keys: set[str] = set()
        all_nocite_keys: set[str] = set()
        any_nocite_star = False
        for m in tex_members:
            f = tf.extractfile(m)
            if f is None:
                continue
            cite_keys, nocite_keys, nocite_star = extract_cite_keys(decode(f.read()))
            all_cite_keys |= cite_keys
            all_nocite_keys |= nocite_keys
            any_nocite_star = any_nocite_star or nocite_star
        result["tex_cite_keys"] = sorted(all_cite_keys)
        result["nocite_explicit_keys"] = sorted(all_nocite_keys)
        result["nocite_star"] = any_nocite_star

        # .bib files: suffix = 1-based position in archive order (not sorted), the numbering of
        # {month}/{arxiv_id}-{suffix}.bib in pre-check/arxiv_bib_extractor.py
        bib_members = [m for m in members if m.isfile() and m.name.lower().endswith(".bib")]
        for idx, m in enumerate(bib_members, start=1):
            f = tf.extractfile(m)
            if f is None:
                continue
            bib_keys = sorted(set(BIB_ENTRY_PATTERN.findall(decode(f.read()))))
            result["bib_files"].append({"suffix": idx, "raw_keys": bib_keys})
    finally:
        tf.close()

    return result


def iter_submissions(source_root: Path, submissions_csv: Path | None, limit: int | None):
    if submissions_csv is not None:
        import csv
        with submissions_csv.open(newline="") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                yield row[0].strip(), row[1].strip()
        return

    month_pattern = re.compile(r"^\d{4}$")
    count = 0
    for month_dir in sorted(source_root.iterdir()):
        if not month_dir.is_dir() or not month_pattern.match(month_dir.name):
            continue
        for tgz in sorted(month_dir.glob("*.tar.gz")):
            arxiv_id = tgz.stem.replace(".tar", "")
            yield month_dir.name, arxiv_id
            count += 1
            if limit is not None and count >= limit:
                return


def process_one(source_root: Path, month: str, arxiv_id: str) -> tuple[str, str, dict]:
    """Reads and extracts one archive; returns (month, arxiv_id, result). Only the main process writes the output file."""
    tgz_path = source_root / month / f"{arxiv_id}.tar.gz"
    if not tgz_path.exists():
        return month, arxiv_id, {"file_type": "missing"}
    try:
        with tgz_path.open("rb") as fh:
            archive_bytes = fh.read()
        result = process_archive(archive_bytes)
    except Exception as e:
        return month, arxiv_id, {"file_type": "error", "error": str(e)}
    return month, arxiv_id, result


# Self-check on a synthetic archive (made-up .tex, .bbl and .bib text) with a known key set per signal.
SYNTHETIC_TEX = r"""
\cite{alpha2001a, beta2002b} \citep[see][p.~3]{gamma2003c} \shortcite{delta2004d}
\combocite{epsilon2005e}{zeta2006f} \nocite{eta2007g} \nocite{*}
% \cite{commented2008h}
"""
SYNTHETIC_BBL = r"""
\bibitem[Alpha(2001)]{alpha2001a} A. Alpha.
\bibitem{beta2002b} B. Beta.
\entry{gamma2003c}{article}{}
"""
SYNTHETIC_BIB = "@article{alpha2001a, title={A}}\n@book{beta2002b,\n title={B}}\n"
EXPECTED_TEX_CITE_KEYS = {"alpha2001a", "beta2002b", "gamma2003c", "delta2004d", "epsilon2005e", "zeta2006f"}
EXPECTED_BBL_KEYS = {"alpha2001a", "beta2002b", "gamma2003c"}


def run_self_checks() -> bool:
    """Runs process_archive() on a synthetic .tar.gz and compares the extracted keys with the expected sets."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in (("main.tex", SYNTHETIC_TEX), ("main.bbl", SYNTHETIC_BBL), ("refs.bib", SYNTHETIC_BIB)):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))
    result = process_archive(buf.getvalue())
    checks = {
        "tex cite keys (cite, citep, shortcite, combocite)": set(result["tex_cite_keys"]) == EXPECTED_TEX_CITE_KEYS,
        "explicit nocite keys": result["nocite_explicit_keys"] == ["eta2007g"],
        "nocite{*}": result["nocite_star"] is True,
        "bbl keys (bibitem and biblatex entry)": set(result["bbl_keys"]) == EXPECTED_BBL_KEYS,
        "bbl format": result["bbl_format"] == "both",
        "bib entry keys": result["bib_files"] == [{"suffix": 1, "raw_keys": ["alpha2001a", "beta2002b"]}],
    }
    for description, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {description}", file=sys.stderr, flush=True)
    return all(checks.values())


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def already_done(output_path: Path) -> set[tuple[str, str]]:
    done = set()
    if not output_path.exists():
        return done
    with output_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add((rec["month"], rec["arxiv_id"]))
            except Exception:
                continue
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--submissions", type=Path, default=None)
    ap.add_argument("--output-path", type=Path, default=None)
    ap.add_argument("--write-citation-keys", action="store_true",
                     help="write the per-submission keys to --output-path")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3,
                     help="parallel worker processes (default 3)")
    ap.add_argument("--skip-self-check", action="store_true",
                     help="skip the self-check")
    args = ap.parse_args()

    if args.write_citation_keys:
        if args.output_path is None:
            print("ERROR: --write-citation-keys needs --output-path", file=sys.stderr)
            sys.exit(1)
        if args.output_path.resolve().is_relative_to(args.source_root.resolve()):
            print("ERROR: --output-path must be outside --source-root", file=sys.stderr)
            sys.exit(1)

    if not args.skip_self_check:
        print("Running self-check on a synthetic archive ...", file=sys.stderr, flush=True)
        if not run_self_checks():
            print("\nABORTING: the self-check failed. Use --skip-self-check to skip it.", file=sys.stderr)
            sys.exit(1)
        print("Self-check passed.\n", file=sys.stderr, flush=True)

    skip = already_done(args.output_path) if args.write_citation_keys else set()
    if skip:
        print(f"Resuming: {len(skip)} submissions already in output, skipping them", file=sys.stderr, flush=True)

    n_processed = 0
    n_missing = 0
    n_errors = 0
    all_submissions = list(iter_submissions(args.source_root, args.submissions, args.limit))
    targets = [(month, arxiv_id) for month, arxiv_id in all_submissions if (month, arxiv_id) not in skip]
    n_skipped = len(all_submissions) - len(targets)
    print(f"{len(targets)} submissions to process with {args.workers} parallel workers "
          f"({n_skipped} resumed-skipped)", file=sys.stderr, flush=True)

    start_time = time.time()
    output_file = args.output_path.open("a") if args.write_citation_keys else contextlib.nullcontext()
    with output_file as out, ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process_one, args.source_root, month, arxiv_id) for month, arxiv_id in targets]
        for fut in as_completed(futures):
            month, arxiv_id, result = fut.result()
            if result.get("file_type") == "missing":
                n_missing += 1
            elif result.get("file_type") == "error":
                n_errors += 1
            result["month"] = month
            result["arxiv_id"] = arxiv_id
            if out is not None:
                out.write(json.dumps(result) + "\n")
            n_processed += 1
            if n_processed % 500 == 0:
                if out is not None:
                    out.flush()
                elapsed = time.time() - start_time
                rate = n_processed / elapsed if elapsed > 0 else 0
                remaining = len(targets) - n_processed
                eta_seconds = remaining / rate if rate > 0 else None
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                eta_clock = (datetime.fromtimestamp(time.time() + eta_seconds).strftime("%Y-%m-%d %H:%M:%S")
                             if eta_seconds is not None else "unknown")
                print(f"[{now}] {n_processed}/{len(targets)} processed "
                      f"({100.0*n_processed/len(targets):.1f}%), {n_missing} missing, {n_errors} errors, "
                      f"rate={rate:.1f}/s, elapsed={format_duration(elapsed)}, "
                      f"ETA={format_duration(eta_seconds) if eta_seconds is not None else 'unknown'} "
                      f"(~{eta_clock})", file=sys.stderr, flush=True)

    total_elapsed = time.time() - start_time
    print(f"done: {n_processed} processed, {n_skipped} resumed-skipped, {n_missing} missing, {n_errors} errors "
          f"in {format_duration(total_elapsed)}", file=sys.stderr)
    if args.write_citation_keys:
        print(f"output written to {args.output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
