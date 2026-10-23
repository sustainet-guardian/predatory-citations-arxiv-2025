#!/usr/bin/env python3
"""Fetches the subject categories of every submission with a confirmed-cited entry from arXiv's public API
(https://export.arxiv.org/api/query, 300 ids per request) and stores them in the SQLite database EVAL_ARXIV_CATEGORIES_DB, table
`categories` (columns month, arxiv_id, full_id (primary key), primary_category, all_categories (comma-separated), found (1 if the
API returned the id, else 0)). Reads EVAL_DB_PATH (view entries_confirmed) and needs EVAL_CONTACT_EMAIL (your own contact address, sent in the user agent to arXiv);
an interrupted run resumes where it stopped.
Request schedule: curl runs without its own retry; every request starts at least 3.5 s (plus up to 0.5 s of jitter) after the
previous one ended, a failed attempt included, and after HTTP 429 or 503 the script waits 60 s and tries the same batch again
(three attempts in all).
This is the API database: primary categories reported by arXiv for the confirmed-cited submissions only. The snapshot database
EVAL_SNAPSHOT_CATEGORIES_DB (build_snapshot_categories_db.py) holds every 2025 submission of the metadata snapshot and has no
full_id column. The API database is read by disciplinary_classification, watchlist_split_and_math_broad_rate and
math_jsc_broad_rate; the other disciplinary scripts read the snapshot database.
"""
from __future__ import annotations

import random
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET

from stats_common import contact_user_agent, get_conn, required_path_env

BATCH_SIZE = 300
DELAY_SECONDS = 3.5
BACKOFF_SECONDS = 60  # wait after HTTP 429 or 503 and after a failed batch
MAX_ATTEMPTS = 3
OUT_DB = required_path_env("EVAL_ARXIV_CATEGORIES_DB")

_last_request_end = float("-inf")

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def get_out_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(OUT_DB))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            month TEXT, arxiv_id TEXT, full_id TEXT PRIMARY KEY,
            primary_category TEXT, all_categories TEXT, found INTEGER
        )
        """
    )
    conn.commit()
    return conn


def already_fetched(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT full_id FROM categories").fetchall()}


def request_api(url: str, user_agent: str) -> tuple[int, str]:
    """One curl request, started at least DELAY_SECONDS (plus jitter) after the end of the previous one.
    Returns (HTTP status, body); raises RuntimeError when curl itself fails (no connection, timeout)."""
    global _last_request_end
    wait = _last_request_end + DELAY_SECONDS + random.uniform(0, 0.5) - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    result = subprocess.run(
        ["curl", "-sS", "-A", user_agent, "--max-time", "30", "-w", "\n%{http_code}", url],
        capture_output=True, text=True,
    )
    _last_request_end = time.monotonic()
    if result.returncode != 0:
        raise RuntimeError(f"curl exit code {result.returncode}: {result.stderr.strip()}")
    body, _, status = result.stdout.rpartition("\n")
    return int(status), body


def fetch_batch(full_ids: list[str], user_agent: str) -> dict[str, tuple[str, str]]:
    """Returns {full_id: (primary_category, comma_joined_all_categories)} for
    every ID the API actually returned an entry for."""
    id_list = ",".join(full_ids)
    url = f"https://export.arxiv.org/api/query?id_list={id_list}&max_results={len(full_ids)}"
    # Requests are spaced by request_api, in line with arXiv's API terms (one request every 3 s at most).
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, body = request_api(url, user_agent)
        if status == 200:
            break
        if status not in (429, 503) or attempt == MAX_ATTEMPTS:
            raise RuntimeError(f"HTTP status {status}")
        print(f"HTTP status {status}; waiting {BACKOFF_SECONDS} s before attempt {attempt + 1}")
        time.sleep(BACKOFF_SECONDS)
    root = ET.fromstring(body)
    out = {}
    for entry in root.findall(f"{ATOM_NS}entry"):
        entry_id = entry.findtext(f"{ATOM_NS}id") or ""
        # http://arxiv.org/abs/YYMM.NNNNNvN -> YYMM.NNNNN
        full_id = entry_id.rsplit("/", 1)[-1].rsplit("v", 1)[0]
        primary = entry.find(f"{ARXIV_NS}primary_category")
        primary_cat = primary.get("term") if primary is not None else None
        all_cats = [c.get("term") for c in entry.findall(f"{ATOM_NS}category")]
        out[full_id] = (primary_cat, ",".join(all_cats))
    return out


def main() -> None:
    user_agent = contact_user_agent("fetch_arxiv_categories.py")  # stops here when EVAL_CONTACT_EMAIL is not set
    conn = get_conn()
    submissions = conn.execute("SELECT DISTINCT month, arxiv_id FROM entries_confirmed").fetchall()
    print(f"{len(submissions)} distinct submissions to fetch categories for")

    out_conn = get_out_conn()
    done = already_fetched(out_conn)
    print(f"{len(done)} already fetched, resuming")

    pending = [(r["month"], r["arxiv_id"]) for r in submissions if f"{r['month']}.{r['arxiv_id']}" not in done]
    print(f"{len(pending)} remaining")

    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start:batch_start + BATCH_SIZE]
        full_ids = [f"{month}.{arxiv_id}" for month, arxiv_id in batch]
        try:
            results = fetch_batch(full_ids, user_agent)
        except (RuntimeError, ET.ParseError) as e:
            print(f"batch at {batch_start} failed: {e}; will retry next run")
            time.sleep(BACKOFF_SECONDS)
            continue

        rows = []
        for month, arxiv_id in batch:
            full_id = f"{month}.{arxiv_id}"
            if full_id in results:
                primary_cat, all_cats = results[full_id]
                rows.append((month, arxiv_id, full_id, primary_cat, all_cats, 1))
            else:
                rows.append((month, arxiv_id, full_id, None, None, 0))

        out_conn.executemany(
            "INSERT OR REPLACE INTO categories (month, arxiv_id, full_id, primary_category, all_categories, found) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        out_conn.commit()

        n_done = batch_start + len(batch)
        print(f"{n_done}/{len(pending)} fetched this run ({len(results)}/{len(batch)} resolved in this batch)")

    print("done")


if __name__ == "__main__":
    main()
