#!/usr/bin/env python3
"""Five analyses after the identity check, one function each: (1) per-watchlist leave-focal-out and co-flag rates (the share of
a list's flags that stay predatory when that list is left out, and the share where another source also votes predatory);
(2) the eligible and resolved-eligible populations; (3) broad rates by venue type and entry type; (4) DOI presence;
(5) citation intensity. Strict = final_assessment 'predatory' minus strict_exclude (view entries_confirmed, the strict
population after the identity check); legitimate = the stored verdict final_assessment 'legitimate' of entries_confirmed_all;
the resolved-eligible population is the entries whose verdict recomputed without the invalid votes is legitimate, predatory,
suspicious or insufficient_data (view entries_confirmed_valid_votes).
Also a library for other scripts (strict_vote_rule_alternatives.py, missed_keys_flag_counts.py and others): it provides
get_conn_after_identity_check() (EVAL_DB_PATH_BBL_TEX with the identity-check exclusions as temporary views and tables, built
from the audit-keys JSON and identity_audit_citation_detail.json in EVAL_OUTPUT_DIR), get_cache_conn() and BROAD_NOT_DROPPED.
Reads EVAL_CACHE_DB_PATH and EVAL_RESULTS_ROOT (via leave_one_out_helpers). Writes
numbers/identity_check_table_analyses.numbers.json and, in EVAL_OUTPUT_DIR/output_identity_check_table_analyses/,
identity_check_table_analyses.json and one CSV (the reliability table). The numbers keys are
reliability_<backend>_flags, _leave_focal_out_pct and _co_flag_pct (per watchlist backend);
eligible_population (state 'assessed'); resolved_eligible_population_new (the resolved-eligible population);
primary_strict_rate_pct (strict / resolved-eligible) and secondary_eligible_rate_pct (strict / eligible);
coverage_unknown_of_eligible_pct (entries of state 'assessed' whose stored verdict is 'unknown'; the count of unknown verdicts on the
valid votes in unknown_venue_counts.py is 2 higher, the audited citations whose verdict recomputed without the invalid votes
is 'unknown'); venuetype_<conference|journal>_n and _broad_pct and entrytype_<article|inproceedings>_broad_pct
(broad after the broad drops / all entries of the type); doi_strict_predatory_pct (strict after the check) and
doi_legitimate_pct (stored legitimate), the share of entries with a DOI; intensity_predatory_mean and intensity_legitimate_mean
(mean cited_count of entries that have a citation log).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import stats_common
from stats_common import pct, write_csv, write_json
import leave_one_out_helpers as loo
from identity_check_headline_analyses import recompute_assessment_excluding
from identity_vote_rules import WATCHLIST_BACKENDS, build_invalid_backends_map

DB_PATH_BBL_TEX = stats_common.required_path_env("EVAL_DB_PATH_BBL_TEX")
OUTPUT_DIR = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_check_table_analyses"
CITATION_DETAIL_PATH = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "identity_audit_citation_detail.json"
AUDIT_KEYS_JSON = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"


def get_conn_after_identity_check():
    conn = sqlite3.connect(str(DB_PATH_BBL_TEX))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed_all AS SELECT * FROM entries_confirmed_bbl_tex")

    audit_keys_data = json.loads(AUDIT_KEYS_JSON.read_text())
    strict_exclude_keys = [tuple(k) for k in audit_keys_data["strict_exclude_keys"]]

    conn.execute("CREATE TEMP TABLE strict_exclude (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO strict_exclude VALUES (?, ?, ?, ?)", strict_exclude_keys)
    # Index for the NOT EXISTS subquery below.
    conn.execute("CREATE INDEX idx_strict_exclude ON strict_exclude(month, arxiv_id, suffix, entry_key)")
    conn.execute(
        """
        CREATE TEMP VIEW entries_confirmed AS
        SELECT e.* FROM entries_confirmed_all e
        WHERE NOT EXISTS (
            SELECT 1 FROM strict_exclude x
            WHERE x.month = e.month AND x.arxiv_id = e.arxiv_id
              AND x.suffix = e.suffix AND x.entry_key = e.entry_key
        )
        """
    )

    per_citation = json.loads(CITATION_DETAIL_PATH.read_text())
    broad_drop_rows = [
        (c["month"], c["arxiv_id"], c["suffix"], c["entry_key"])
        for c in per_citation if not c["broad_survives"]
    ]
    conn.execute("CREATE TEMP TABLE broad_drops (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO broad_drops VALUES (?, ?, ?, ?)", broad_drop_rows)
    conn.execute("CREATE INDEX idx_broad_drops ON broad_drops(month, arxiv_id, suffix, entry_key)")

    # Verdict of every audited citation recomputed without its invalid vote. The resolved-eligible population is broader
    # than strict-predatory: most audited citations only change class within it (e.g. to legitimate or suspicious) and
    # only those recomputed to 'unknown' leave it, so the recomputed verdict is substituted for exactly the audited rows
    # instead of excluding them.
    conn.execute(
        "CREATE TEMP TABLE valid_vote_verdict (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT, verdict_valid_votes TEXT)"
    )
    conn.executemany(
        "INSERT INTO valid_vote_verdict VALUES (?, ?, ?, ?, ?)",
        [(c["month"], c["arxiv_id"], c["suffix"], c["entry_key"], c["verdict_valid_votes"]) for c in per_citation],
    )
    conn.execute("CREATE INDEX idx_valid_vote_verdict ON valid_vote_verdict(month, arxiv_id, suffix, entry_key)")
    conn.execute(
        """
        CREATE TEMP VIEW entries_confirmed_valid_votes AS
        SELECT e.*, COALESCE(rv.verdict_valid_votes, e.final_assessment) AS final_assessment_valid_votes
        FROM entries_confirmed_all e
        LEFT JOIN valid_vote_verdict rv
          ON rv.month=e.month AND rv.arxiv_id=e.arxiv_id AND rv.suffix=e.suffix AND rv.entry_key=e.entry_key
        """
    )

    return conn



# The imported modules set stats_common.DB_PATH and OUTPUT_DIR to their own values; this script's values are set here.
stats_common.DB_PATH = DB_PATH_BBL_TEX
stats_common.OUTPUT_DIR = OUTPUT_DIR
stats_common.get_conn = get_conn_after_identity_check

BROAD_NOT_DROPPED = (
    "NOT EXISTS (SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
    "AND b.suffix=e.suffix AND b.entry_key=e.entry_key)"
)


def get_cache_conn():
    return sqlite3.connect(str(stats_common.required_path_env("EVAL_CACHE_DB_PATH")))


def n_broad_without_drops(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all e "
        "WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0"
    ).fetchone()["n"]


def n_broad_after_drops(conn) -> int:
    return conn.execute(
        f"SELECT COUNT(*) n FROM entries_confirmed_all e "
        f"WHERE CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {BROAD_NOT_DROPPED}"
    ).fetchone()["n"]


# ---------------------------------------------------------------------------
# Per-watchlist leave-focal-out and co-flag rates, without and with the broad drops and the invalid-vote map.
# leave_focal_out_predatory_pct: share of a list's flags whose verdict stays predatory when that list is left out;
# other_watchlist_or_backend_co_flag_pct: share of its flags where another source also votes predatory.
# ---------------------------------------------------------------------------

def per_watchlist_reliability(conn, use_broad_drops: bool, invalid_backends_map: dict | None = None) -> list[dict]:
    invalid_backends_map = invalid_backends_map or {}
    where = "CAST(n_predatory_list_hits AS INTEGER) > 0" + (f" AND {BROAD_NOT_DROPPED}" if use_broad_drops else "")
    rows = conn.execute(
        f"SELECT e.month AS month, e.arxiv_id AS arxiv_id, e.suffix AS suffix, e.entry_key AS entry_key, "
        f"e.final_assessment AS final_assessment, e.predatory_list_hits AS predatory_list_hits "
        f"FROM entries_confirmed_all e WHERE {where}"
    ).fetchall()
    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    records = loo.load_records(targets)

    out = []
    for backend in WATCHLIST_BACKENDS:
        hits = [r for r in rows if backend in (r["predatory_list_hits"] or "").split(",")]
        n_hit = len(hits)
        if n_hit == 0:
            continue
        verdict_counts = Counter()
        n_other_source_predatory = 0
        n_no_record = 0
        for r in hits:
            key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
            rec = records.get(key)
            if rec is None:
                n_no_record += 1
                continue
            backend_results = rec.get("backend_results", [])
            # The citation's own invalid vote is removed together with the focal backend.
            own_invalid = invalid_backends_map.get(key, set())
            exclude_set = {backend} | own_invalid
            verdict_without_focal = recompute_assessment_excluding(backend_results, exclude_set)
            verdict_counts[verdict_without_focal] += 1
            other_predatory = any(
                b.get("status") == "found" and b.get("assessment") == "predatory"
                and b.get("backend_name") not in exclude_set
                for b in backend_results
            )
            if other_predatory:
                n_other_source_predatory += 1
        n_considered = n_hit - n_no_record
        out.append({
            "backend": backend,
            "n_flags": n_hit,
            "n_considered": n_considered,
            "n_leave_focal_out_predatory": verdict_counts["predatory"],
            "leave_focal_out_predatory_pct": pct(verdict_counts["predatory"], n_considered),
            "n_other_watchlist_or_backend_co_flag": n_other_source_predatory,
            "other_watchlist_or_backend_co_flag_pct": pct(n_other_source_predatory, n_considered),
        })
    out.sort(key=lambda d: -d["n_flags"])
    return out


def reliability_by_watchlist(conn) -> dict:
    """Per-watchlist leave-focal-out and co-flag rates before and after the identity check."""
    # The invalid-vote map is built from all confirmed misattribution keys (not only strict_exclude_keys): it
    # removes each citation's own invalid vote from the leave-focal-out recomputation.
    misattribution_keys = {tuple(k) for k in json.loads(AUDIT_KEYS_JSON.read_text())["confirmed_misattribution_keys"]}
    invalid_backends_map = build_invalid_backends_map(conn, get_cache_conn(), misattribution_keys)

    # Before the identity check: no broad drops and no invalid-vote map; after: both applied.
    before = per_watchlist_reliability(conn, use_broad_drops=False, invalid_backends_map=None)
    after = per_watchlist_reliability(conn, use_broad_drops=True, invalid_backends_map=invalid_backends_map)
    after_by_backend = {d["backend"]: d for d in after}
    combined = []
    for b in before:
        a = after_by_backend.get(b["backend"], {})
        combined.append({
            "backend": b["backend"],
            "n_flags_before_identity_check": b["n_flags"], "n_flags_after_identity_check": a.get("n_flags"),
            "n_considered_after_identity_check": a.get("n_considered"),
            "n_leave_focal_out_predatory_after_identity_check": a.get("n_leave_focal_out_predatory"),
            "leave_focal_out_predatory_pct_before_identity_check": b["leave_focal_out_predatory_pct"],
            "leave_focal_out_predatory_pct_after_identity_check": a.get("leave_focal_out_predatory_pct"),
            "n_other_watchlist_or_backend_co_flag_after_identity_check": a.get("n_other_watchlist_or_backend_co_flag"),
            "co_flag_pct_before_identity_check": b["other_watchlist_or_backend_co_flag_pct"],
            "co_flag_pct_after_identity_check": a.get("other_watchlist_or_backend_co_flag_pct"),
        })
    return {
        "n_broad_before_identity_check": n_broad_without_drops(conn), "n_broad_after_identity_check": n_broad_after_drops(conn),
        "per_watchlist": combined,
    }


# ---------------------------------------------------------------------------
# Eligible and resolved-eligible populations.
# ---------------------------------------------------------------------------

RESOLVED_ELIGIBLE_WHERE = "final_assessment_valid_votes IN ('legitimate','predatory','suspicious','insufficient_data')"


def eligible_populations(conn) -> dict:
    """The eligible population (entries with an extractable venue name and a supported BibTeX entry type, state='assessed';
    a property of the extraction that does not depend on the identity check) and the resolved-eligible population after the
    identity check (verdict recomputed without the invalid votes)."""
    n_eligible = conn.execute("SELECT COUNT(*) n FROM entries_confirmed_all WHERE state='assessed'").fetchone()["n"]
    n_resolved_eligible_valid_votes = conn.execute(
        f"SELECT COUNT(*) n FROM entries_confirmed_valid_votes WHERE {RESOLVED_ELIGIBLE_WHERE}"
    ).fetchone()["n"]
    return {
        "eligible_population": n_eligible,
        "resolved_eligible_population_after_identity_check": n_resolved_eligible_valid_votes,
    }


# ---------------------------------------------------------------------------
# Broad-predatory rates by venue type and entry type, with the broad drops.
# ---------------------------------------------------------------------------

def broad_rate_by_type(conn) -> dict:
    """Broad-predatory rate per venue type and per entry type after the identity check (broad drops applied)."""
    def rate_by(field: str) -> list[dict]:
        rows = conn.execute(
            f"SELECT e.{field} AS k, COUNT(*) n, "
            f"SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND {BROAD_NOT_DROPPED} THEN 1 ELSE 0 END) n_predatory "
            f"FROM entries_confirmed_all e GROUP BY e.{field} ORDER BY n DESC"
        ).fetchall()
        return [{"key": r["k"], "n": r["n"], "n_predatory": r["n_predatory"],
                 "predatory_pct": pct(r["n_predatory"], r["n"])} for r in rows]

    return {
        "by_venue_type_after_identity_check": rate_by("venue_type"),
        "by_entry_type_after_identity_check": rate_by("entry_type"),
    }


# ---------------------------------------------------------------------------
# DOI presence among strict-predatory citations (before and after the identity check) and legitimate citations.
# ---------------------------------------------------------------------------

def doi_presence(conn) -> dict:
    """Share of citations with a DOI among strict-predatory (before and after the identity check) and legitimate."""
    def doi_rate(table: str, where: str) -> dict:
        total = conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {where}").fetchone()["n"]
        with_doi = conn.execute(
            f"SELECT COUNT(*) n FROM {table} WHERE {where} AND doi IS NOT NULL AND doi != ''"
        ).fetchone()["n"]
        return {"total": total, "with_doi": with_doi, "doi_pct": pct(with_doi, total)}

    before = doi_rate("entries_confirmed_all", "final_assessment='predatory'")
    after = doi_rate("entries_confirmed", "final_assessment='predatory'")
    # Legitimate: stored verdict over all confirmed entries; the audited citations whose verdict was recomputed to
    # legitimate are not part of it.
    legitimate = doi_rate("entries_confirmed_all", "final_assessment='legitimate'")
    return {"strict_predatory_before_identity_check": before, "strict_predatory_after_identity_check": after,
            "legitimate": legitimate}


# ---------------------------------------------------------------------------
# Citation intensity (cited_count), mean, strict-predatory before and after the identity check, legitimate.
# ---------------------------------------------------------------------------

def citation_intensity(conn) -> dict:
    """Mean cited_count of strict-predatory (before and after the identity check) and legitimate citations."""
    def intensity_stats(table: str, where: str) -> dict:
        # Rows flagged as having a citation log (has_cited_sidecar='1') but a NULL cited_count are skipped.
        vals = [
            float(r["cited_count"]) for r in conn.execute(
                f"SELECT cited_count FROM {table} WHERE has_cited_sidecar='1' "
                f"AND cited_count IS NOT NULL AND {where}"
            )
        ]
        if not vals:
            return {"n": 0}
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 3)}

    before = intensity_stats("entries_confirmed_all", "final_assessment='predatory'")
    after = intensity_stats("entries_confirmed", "final_assessment='predatory'")
    legitimate = intensity_stats("entries_confirmed_all", "final_assessment='legitimate'")
    return {"predatory_before_identity_check": before, "predatory_after_identity_check": after, "legitimate": legitimate}


VENUE_TYPES_REPORTED = ("conference", "journal")
ENTRY_TYPES_REPORTED = ("article", "inproceedings")


def main() -> None:
    conn = stats_common.get_conn()
    out = {
        "reliability_by_watchlist": reliability_by_watchlist(conn),
        "eligible_populations": eligible_populations(conn),
        "broad_rate_by_type": broad_rate_by_type(conn),
        "doi_presence": doi_presence(conn),
        "citation_intensity": citation_intensity(conn),
    }
    write_json("identity_check_table_analyses", out)
    write_csv(
        "identity_check_table_analyses_reliability",
        out["reliability_by_watchlist"]["per_watchlist"],
        list(out["reliability_by_watchlist"]["per_watchlist"][0].keys()),
    )
    print(json.dumps(out, indent=2, default=str))

    # The numbers use the columns after the identity check; ratios are written as num/den so that rounding happens once,
    # in the collector.
    reliability = out["reliability_by_watchlist"]
    populations = out["eligible_populations"]
    numbers_payload = {}
    for row in reliability["per_watchlist"]:
        backend = row["backend"]
        numbers_payload[f"reliability_{backend}_flags"] = {"kind": "int", "value": row["n_flags_after_identity_check"]}
        numbers_payload[f"reliability_{backend}_leave_focal_out_pct"] = {
            "kind": "ratio", "num": row["n_leave_focal_out_predatory_after_identity_check"],
            "den": row["n_considered_after_identity_check"], "decimals": 1,
        }
        numbers_payload[f"reliability_{backend}_co_flag_pct"] = {
            "kind": "ratio", "num": row["n_other_watchlist_or_backend_co_flag_after_identity_check"],
            "den": row["n_considered_after_identity_check"], "decimals": 1,
        }
    numbers_payload["eligible_population"] = {"kind": "int", "value": populations["eligible_population"]}
    numbers_payload["resolved_eligible_population_new"] = {
        "kind": "int", "value": populations["resolved_eligible_population_after_identity_check"],
    }
    n_strict_after_identity_check = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed WHERE final_assessment='predatory'"
    ).fetchone()["n"]
    numbers_payload["primary_strict_rate_pct"] = {
        "kind": "ratio", "num": n_strict_after_identity_check,
        "den": populations["resolved_eligible_population_after_identity_check"], "decimals": 3,
    }
    numbers_payload["secondary_eligible_rate_pct"] = {
        "kind": "ratio", "num": n_strict_after_identity_check,
        "den": populations["eligible_population"], "decimals": 3,
    }
    # Share of eligible entries that no backend resolves: state='assessed' AND final_assessment='unknown', independent of
    # the identity check.
    n_unknown_eligible = conn.execute(
        "SELECT COUNT(*) n FROM entries_confirmed_all WHERE state='assessed' AND final_assessment='unknown'"
    ).fetchone()["n"]
    numbers_payload["coverage_unknown_of_eligible_pct"] = {
        "kind": "ratio", "num": n_unknown_eligible, "den": populations["eligible_population"], "decimals": 1,
    }

    # Venue types and entry types are free-text fields with many distinct values; only the types below get macros.
    broad_by_type = out["broad_rate_by_type"]
    for row in broad_by_type["by_venue_type_after_identity_check"]:
        k = row["key"] or "unknown"
        if k not in VENUE_TYPES_REPORTED:
            continue
        numbers_payload[f"venuetype_{k}_n"] = {"kind": "int", "value": row["n"]}
        numbers_payload[f"venuetype_{k}_broad_pct"] = {"kind": "ratio", "num": row["n_predatory"], "den": row["n"], "decimals": 3}
    for row in broad_by_type["by_entry_type_after_identity_check"]:
        k = row["key"] or "unknown"
        if k not in ENTRY_TYPES_REPORTED:
            continue
        numbers_payload[f"entrytype_{k}_broad_pct"] = {"kind": "ratio", "num": row["n_predatory"], "den": row["n"], "decimals": 3}

    doi = out["doi_presence"]
    numbers_payload["doi_strict_predatory_pct"] = {
        "kind": "ratio", "num": doi["strict_predatory_after_identity_check"]["with_doi"],
        "den": doi["strict_predatory_after_identity_check"]["total"], "decimals": 1,
    }
    numbers_payload["doi_legitimate_pct"] = {
        "kind": "ratio", "num": doi["legitimate"]["with_doi"], "den": doi["legitimate"]["total"], "decimals": 1,
    }

    intensity = out["citation_intensity"]
    numbers_payload["intensity_predatory_mean"] = {"kind": "float", "value": intensity["predatory_after_identity_check"]["mean"], "decimals": 2}
    numbers_payload["intensity_legitimate_mean"] = {"kind": "float", "value": intensity["legitimate"]["mean"], "decimals": 2}

    stats_common.write_numbers("identity_check_table_analyses", numbers_payload)


if __name__ == "__main__":
    main()
