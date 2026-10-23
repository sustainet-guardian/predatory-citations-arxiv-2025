#!/usr/bin/env python3
"""Decomposes the confirmed-cited entries by origin and relates the promoted entries to the compiled-bibliography signal.

Reads EVAL_DB_PATH_BBL_TEX (entries, files) and EVAL_BBL_JSONL_PATH. Splits the entries with citation_status_bbl_tex =
'confirmed_cited' into own-log, missed-key and promoted-no-log entries, by file class; then, on distinct (month, arxiv_id,
key), the promoted keys by presence in the .bbl and the TeX scan, the .bbl-only keys, and the \\nocite-attributable keys.
Writes numbers/confirmed_cited_composition.numbers.json (keys confpop_*) and
EVAL_OUTPUT_DIR/output_confirmed_cited_composition/confirmed_cited_composition.json.
All values are counts of the confirmed-cited entries (the view entries_confirmed_bbl_tex: citation_status_bbl_tex =
'confirmed_cited') or of their keys and files; there is no ratio and no legitimate or strict denominator.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import stats_common
from stats_common import required_path_env

DB_PATH = required_path_env("EVAL_DB_PATH_BBL_TEX")
JSONL_PATH = required_path_env("EVAL_BBL_JSONL_PATH")
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_confirmed_cited_composition"
stats_common.OUTPUT_DIR = OUTPUT_DIR

ORIGIN_SQL = """
CASE
  WHEN citation_status = 'confirmed_cited' AND citation_status_bbl_tex_changed = '0' AND added_missed_key_row IS NULL THEN 'own_log'
  WHEN added_missed_key_row = '1' AND citation_status IS NULL THEN 'missed_key'
  WHEN citation_status_bbl_tex_changed = '1' AND citation_status = 'unverified_isolated' THEN 'promoted_no_log_isolated'
  WHEN citation_status_bbl_tex_changed = '1' AND citation_status = 'excluded_sibling_inferred_uncited' THEN 'promoted_no_log_sibling'
  ELSE 'other'
END
"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    # composition by origin and file class
    total = conn.execute("SELECT COUNT(*) FROM entries WHERE citation_status_bbl_tex = 'confirmed_cited'").fetchone()[0]
    assert total == conn.execute("SELECT COUNT(*) FROM entries_confirmed_bbl_tex").fetchone()[0]
    origin = dict(conn.execute(
        f"SELECT {ORIGIN_SQL} AS o, COUNT(*) FROM entries WHERE citation_status_bbl_tex = 'confirmed_cited' GROUP BY o"
    ).fetchall())
    for k in ("own_log", "missed_key", "promoted_no_log_isolated", "promoted_no_log_sibling", "other"):
        origin.setdefault(k, 0)
    assert sum(origin.values()) == total
    n_promoted_status_changed = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE citation_status_bbl_tex_changed = '1' AND citation_status_bbl_tex = 'confirmed_cited'"
    ).fetchone()[0]
    promoted_no_log = origin["promoted_no_log_isolated"] + origin["promoted_no_log_sibling"]
    assert n_promoted_status_changed == origin["missed_key"] + promoted_no_log
    confirmed_without_missed_keys = origin["own_log"] + promoted_no_log  # confirmed-cited entries without the missed keys

    # file class from the files table (not from the entries' own flag)
    by_class = dict(conn.execute(
        """SELECT f.has_cited_sidecar, COUNT(*) FROM entries_confirmed_bbl_tex e
           JOIN files f ON f.month = e.month AND f.name = e.arxiv_id || '-' || e.suffix
           WHERE f.category = 'assessed' GROUP BY f.has_cited_sidecar"""
    ).fetchall())
    assert sum(by_class.values()) == total, "some confirmed-cited entries have no assessed file row"
    n_in_log_files, n_in_no_log_files = by_class.get(1, 0), by_class.get(0, 0)
    assert n_in_log_files == origin["own_log"] + origin["missed_key"] and n_in_no_log_files == promoted_no_log
    files_by_class = dict(conn.execute("SELECT has_cited_sidecar, COUNT(*) FROM files WHERE category='assessed' GROUP BY 1").fetchall())

    # key level
    promoted_rows = conn.execute(
        f"SELECT month, arxiv_id, suffix, entry_key, {ORIGIN_SQL} AS o FROM entries "
        "WHERE citation_status_bbl_tex_changed = '1' AND citation_status_bbl_tex = 'confirmed_cited'"
    ).fetchall()
    assert len(promoted_rows) == n_promoted_status_changed
    promoted_by_sub: dict[tuple, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    entries_of_key: Counter = Counter()
    for month, arxiv_id, _, key, o in promoted_rows:
        grp = "missed_key" if o == "missed_key" else "promoted_no_log"
        promoted_by_sub[(month, arxiv_id)][key].add(grp)
        entries_of_key[(month, arxiv_id, key)] += 1
    distinct_promoted = len(entries_of_key)
    distinct_by_group = Counter(g for subs in promoted_by_sub.values() for groups in subs.values() for g in groups)
    n_both_groups = sum(1 for subs in promoted_by_sub.values() for groups in subs.values() if len(groups) == 2)

    # one derived record per submission (the last record of a submission is used)
    derived: dict[tuple, tuple] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("file_type") != "tar_gz":
                continue
            sub = (rec["month"], rec["arxiv_id"])
            bbl, tex = set(rec.get("bbl_keys") or []), set(rec.get("tex_cite_keys") or [])
            usable = bool(bbl | tex)
            if not usable:
                derived[sub] = (False, frozenset(), frozenset(), {})
                continue
            star, explicit = bool(rec.get("nocite_star")), set(rec.get("nocite_explicit_keys") or [])
            bbl_only = bbl - tex
            attributable = {k for k in bbl_only if star or k in explicit}
            flags = {k: (k in bbl, k in tex) for k in promoted_by_sub.get(sub, ())}
            derived[sub] = (True, frozenset(bbl_only), frozenset(attributable), flags)

    bbl_only_total = sum(len(d[1]) for d in derived.values())
    attributable_total = sum(len(d[2]) for d in derived.values())
    n_usable_submissions = sum(1 for d in derived.values() if d[0])

    pkey_class: Counter = Counter()          # distinct promoted keys by signal membership
    pkey_class_entries: Counter = Counter()  # ... and the entries behind them
    promoted_attrib_entries = 0
    for sub, keys in promoted_by_sub.items():
        usable, _, attributable, flags = derived.get(sub, (False, frozenset(), frozenset(), {}))
        for key in keys:
            if not usable or key not in flags:
                cls = "submission_without_usable_signal"
            else:
                in_bbl, in_tex = flags[key]
                cls = "bbl_only" if in_bbl and not in_tex else "bbl_and_tex" if in_bbl else "tex_scan_only"
            pkey_class[cls] += 1
            pkey_class_entries[cls] += entries_of_key[(*sub, key)]
            if key in attributable:
                promoted_attrib_entries += entries_of_key[(*sub, key)]

    # The .bbl-only keys: do they have a promoted entry; if not, what is their status in the database?
    other_status: Counter = Counter()
    bbl_only_with_promoted = 0
    for sub, (_, bbl_only, _, _) in derived.items():
        if not bbl_only:
            continue
        promoted_here = promoted_by_sub.get(sub, {})
        remaining = {k for k in bbl_only if k not in promoted_here}
        bbl_only_with_promoted += len(bbl_only) - len(remaining)
        if not remaining:
            continue
        statuses: dict[str, set] = defaultdict(set)
        for key, st in conn.execute(
            "SELECT entry_key, citation_status_bbl_tex FROM entries WHERE month = ? AND arxiv_id = ?", sub
        ):
            if key in remaining:
                statuses[key].add(st)
        for key in remaining:
            sts = statuses.get(key)
            if not sts:
                other_status["no_entry_row_in_database"] += 1
            elif "confirmed_cited" in sts:
                other_status["already_confirmed_cited"] += 1
            else:
                other_status["only_non_confirmed_entries"] += 1

    assert bbl_only_with_promoted + sum(other_status.values()) == bbl_only_total

    out = {
        "entries": {
            "total_confirmed_cited": total, "by_origin": origin,
            "promoted_status_changed_total": n_promoted_status_changed,
            "confirmed_cited_without_missed_keys": confirmed_without_missed_keys,
            "entries_in_files_with_a_log": n_in_log_files, "entries_in_files_without_a_log": n_in_no_log_files,
            "files_with_a_log": files_by_class.get(1), "files_without_a_log": files_by_class.get(0),
        },
        "keys": {
            "distinct_promoted_keys": distinct_promoted,
            "distinct_promoted_keys_by_group": dict(distinct_by_group),
            "keys_promoted_in_both_groups": n_both_groups,
            "distinct_promoted_keys_by_signal": dict(pkey_class),
            "promoted_entries_by_signal": dict(pkey_class_entries),
            "n_usable_signal_submissions": n_usable_submissions,
            "bbl_only_keys": bbl_only_total, "bbl_only_nocite_attributable_keys": attributable_total,
            "bbl_only_keys_with_a_promoted_entry": bbl_only_with_promoted,
            "bbl_only_keys_without_a_promoted_entry_by_status": dict(other_status),
            "promoted_entries_that_are_nocite_attributable": promoted_attrib_entries,
        },
    }
    stats_common.write_json("confirmed_cited_composition", out)
    print(json.dumps(out, indent=2))

    def i(v: int) -> dict:
        return {"kind": "int", "value": int(v)}

    stats_common.write_numbers("confirmed_cited_composition", {
        "confpop_n_total": i(total),
        "confpop_n_own_log": i(origin["own_log"]),
        "confpop_n_missed_keys": i(origin["missed_key"]),
        "confpop_n_promoted_no_log": i(promoted_no_log),
        "confpop_n_promoted_no_log_isolated": i(origin["promoted_no_log_isolated"]),
        "confpop_n_promoted_no_log_sibling": i(origin["promoted_no_log_sibling"]),
        "confpop_n_other_origin": i(origin["other"]),
        "confpop_n_promoted_total": i(n_promoted_status_changed),
        "confpop_n_original_population": i(confirmed_without_missed_keys),
        "confpop_n_entries_in_log_files": i(n_in_log_files),
        "confpop_n_entries_in_no_log_files": i(n_in_no_log_files),
        "confpop_n_files_with_log": i(files_by_class.get(1, 0)),
        "confpop_n_files_without_log": i(files_by_class.get(0, 0)),
        "confpop_n_promoted_distinct_keys": i(distinct_promoted),
        "confpop_n_promoted_keys_bbl_only": i(pkey_class["bbl_only"]),
        "confpop_n_promoted_keys_bbl_and_tex": i(pkey_class["bbl_and_tex"]),
        "confpop_n_promoted_keys_tex_scan_only": i(pkey_class["tex_scan_only"]),
        "confpop_n_promoted_keys_no_usable_signal": i(pkey_class["submission_without_usable_signal"]),
        "confpop_n_bbl_only_keys": i(bbl_only_total),
        "confpop_n_bbl_only_keys_with_promoted_entry": i(bbl_only_with_promoted),
        "confpop_n_bbl_only_keys_already_confirmed": i(other_status["already_confirmed_cited"]),
        "confpop_n_bbl_only_keys_only_non_confirmed_entries": i(other_status["only_non_confirmed_entries"]),
        "confpop_n_bbl_only_keys_no_entry_row": i(other_status["no_entry_row_in_database"]),
        "confpop_n_nocite_attributable_keys": i(attributable_total),
        "confpop_n_promoted_entries_nocite_attributable": i(promoted_attrib_entries),
    })


if __name__ == "__main__":
    main()
