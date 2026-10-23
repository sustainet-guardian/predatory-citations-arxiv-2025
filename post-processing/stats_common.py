"""Shared helpers of the analysis scripts: required paths from environment variables, a SQLite connection to the entries database
(EVAL_DB_PATH unless a script sets DB_PATH; only set_citation_status.py writes through it), JSON/CSV writers for EVAL_OUTPUT_DIR
(unless a script sets OUTPUT_DIR), write_numbers() for numbers/*.numbers.json and read_number() for the values in them,
submission_of() for the submission id of a .bib file name, LARGE_FILE_ENTRIES, and contact_user_agent() for the scripts that
query public web services.
The environment variables are read when they are first needed, so a script needs exactly the variables it uses.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def required_path_env(name: str) -> Path:
    """Read a required path from the environment; there are no defaults."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Point it at your own locally-built path "
            f"-- see post-processing/README.md. There is no default."
        )
    return Path(value)


DB_PATH: Path | None = None
OUTPUT_DIR: Path | None = None

# A .bib file with at least this many entries is a large file (the scripts that separate or leave out large files use this value).
LARGE_FILE_ENTRIES = 1000

REPOSITORY_URL = "https://github.com/sustainet-guardian/predatory-citations-arxiv-2025"


def contact_user_agent(script_name: str) -> str:
    """The user agent of a script that queries a public web service: the script name, the repository and the contact address
    in EVAL_CONTACT_EMAIL (arXiv's API and OpenAlex ask for a contact address). There is no default; the variable is read when
    the user agent is built."""
    email = os.environ.get("EVAL_CONTACT_EMAIL")
    if not email:
        raise RuntimeError(
            "EVAL_CONTACT_EMAIL is not set. arXiv's API and OpenAlex ask for a contact address in the user agent of a client; "
            "set it to your own address -- see post-processing/README.md. There is no default."
        )
    return f"{script_name} ({REPOSITORY_URL}; mailto:{email})"


def submission_of(file_name: str) -> str:
    """The submission id of a .bib file name '<NNNNN>-<k>' (file k of submission NNNNN): the part before the hyphen. A name
    must contain exactly one hyphen; anything else raises ValueError."""
    submission, hyphen, number = file_name.partition("-")
    if not hyphen or not submission or not number or "-" in number:
        raise ValueError(f"file name {file_name!r} is not '<submission>-<number>' with exactly one hyphen")
    return submission


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH if DB_PATH is not None else required_path_env("EVAL_DB_PATH")))
    conn.row_factory = sqlite3.Row
    return conn


def percentiles(values: list[float], ps: tuple[int, ...] = (5, 25, 50, 75, 95)) -> dict[int, float | None]:
    values = sorted(v for v in values if v is not None)
    n = len(values)
    if n == 0:
        return {p: None for p in ps}
    out = {}
    for p in ps:
        idx = min(n - 1, max(0, round(p / 100 * (n - 1))))
        out[p] = values[idx]
    return out


def output_dir() -> Path:
    return OUTPUT_DIR if OUTPUT_DIR is not None else required_path_env("EVAL_OUTPUT_DIR")


def write_json(name: str, data: Any) -> None:
    directory = output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {path}")


def write_csv(name: str, rows: list[dict], fieldnames: list[str]) -> None:
    directory = output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")


def pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 3) if total else 0.0


NUMBERS_DIR = Path(__file__).parent / "numbers"


def write_numbers(script_stem: str, numbers: dict[str, dict]) -> None:
    """Write a script's numbers to numbers/<script_stem>.numbers.json, plus "_source" and "_generated_at".

    Every entry is one of three kinds:
      {"kind": "ratio", "num": 1, "den": 4, "decimals": 1}  -- num/den as a percentage, rounded once, in make_numbers_tex.py
      {"kind": "int",   "value": 1234}
      {"kind": "float", "value": -15.3, "decimals": 1}
    Aggregates only: no per-citation values.
    """
    import datetime

    NUMBERS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "_source": script_stem,
        "_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **numbers,
    }
    path = NUMBERS_DIR / f"{script_stem}.numbers.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(numbers)} numbers to {path}")


def read_number(script_stem: str, key: str) -> int | float:
    """The value of an int or float key of numbers/<script_stem>.numbers.json, written by <script_stem>.py."""
    entry = _read_numbers_entry(script_stem, key)
    if entry["kind"] not in ("int", "float"):
        raise ValueError(f"{script_stem}.numbers.json: key {key} has kind {entry['kind']}; read_number reads int and float keys")
    return entry["value"]


def _read_numbers_entry(script_stem: str, key: str) -> dict:
    path = NUMBERS_DIR / f"{script_stem}.numbers.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run {script_stem}.py first")
    data = json.loads(path.read_text())
    if key not in data:
        raise KeyError(f"{path} has no key {key}")
    return data[key]

