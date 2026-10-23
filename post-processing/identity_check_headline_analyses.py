#!/usr/bin/env python3
"""Four analyses after excluding the citations whose venue identity was found misattributed (keys from
full_corpus_identity_mismatch_audit.py): venue concentration of strict-predatory citations, the verdicts of the audited
citations recomputed without their invalid votes, per-discipline submission rates, and the "any documented concern"
submission count. Also provides functions that other scripts import; each function reads only the environment variables it
needs, when it is called, and nothing is read at import.
Reads EVAL_DB_PATH_BBL_TEX, EVAL_RESULTS_ROOT, EVAL_CACHE_DB_PATH, the retraction-risk basis CSV in EVAL_OUTPUT_DIR and, for
the per-discipline rates, the snapshot category database EVAL_SNAPSHOT_CATEGORIES_DB (every 2025 submission of the arXiv
metadata snapshot, made by build_snapshot_categories_db.py; the API database EVAL_ARXIV_CATEGORIES_DB of
fetch_arxiv_categories.py holds the confirmed-cited submissions only, so the submissions without a confirmed-cited entry exist
only in the snapshot database). Populations: "before the identity check" is the view entries_confirmed_all (stored verdicts,
final_assessment = 'predatory' is strict); "after" is the view entries_confirmed (the strict population: final_assessment =
'predatory' without the strict_exclude keys), and for broad the entries_confirmed_all entries without the broad_drops. The
per-discipline shares are submissions with a strict or broad citation over the submissions of the discipline.
Writes EVAL_OUTPUT_DIR/output_identity_check_headline_analyses/identity_check_headline_analyses.json (no numbers file).
With --write-citation-keys it also writes EVAL_OUTPUT_DIR/identity_audit_citation_detail.json (directly in EVAL_OUTPUT_DIR,
because other scripts read it by path): keys and recomputed verdicts of the audited citations, read by
identity_check_table_analyses.py, leave_one_out_backend_sweep.py and further scripts."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import stats_common
from disciplinary_classification import classify, snapshot_categories_db
from decision_rule import weighted_vote_assessment
from identity_vote_rules import build_any_row_has_issn_index, classify_votes, invalid_backends, normalize_issn
from retraction_tiers import tier_without_recent_rate_below_min_publications


def get_conn_with_exclusions() -> tuple[sqlite3.Connection, int]:
    conn = sqlite3.connect(str(stats_common.required_path_env("EVAL_DB_PATH_BBL_TEX")))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP VIEW entries_confirmed_all AS SELECT * FROM entries_confirmed_bbl_tex")

    audit_keys_json = (
        stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit"
        / "full_corpus_identity_mismatch_audit_keys.json"
    )
    keys_data = json.loads(audit_keys_json.read_text())
    keys = keys_data["confirmed_misattribution_keys"]  # all confirmed-misattribution citations
    conn.execute("CREATE TEMP TABLE confirmed_misattributions (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO confirmed_misattributions VALUES (?, ?, ?, ?)", keys)

    # strict_exclude_keys = confirmed_misattribution_keys minus the second-vote survivors (citations that still
    # resolve to predatory after their own invalid vote is removed from the decision rule).
    strict_exclude = keys_data["strict_exclude_keys"]
    conn.execute("CREATE TEMP TABLE strict_exclude (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    conn.executemany("INSERT INTO strict_exclude VALUES (?, ?, ?, ?)", strict_exclude)

    # entries_confirmed = population without the strict-excluded citations; entries_confirmed_all = all confirmed entries.
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
    return conn, len(strict_exclude)


def pct(n, total) -> float | None:
    return round(100.0 * n / total, 3) if total else None


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(100 * max(0.0, center - half), 3), round(100 * min(1.0, center + half), 3))


# ---------------------------------------------------------------------------
# Venue concentration
# ---------------------------------------------------------------------------

def venue_counts(conn, table: str, where_clause: str) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT COUNT(*) n FROM {table}
        WHERE {where_clause} AND COALESCE(matched_venue_name, venue_raw) != ''
        GROUP BY COALESCE(matched_venue_name, venue_raw) ORDER BY n DESC
        """
    ).fetchall()
    return [r["n"] for r in rows]


def gini(counts_desc: list[int]) -> float | None:
    x = sorted(counts_desc)
    n = len(x)
    total = sum(x)
    if not total or not n:
        return None
    numerator = sum((2 * (i + 1) - n - 1) * xi for i, xi in enumerate(x))
    return round(numerator / (n * total), 4)


def venues_for_share(counts_desc: list[int], share: float, total: int) -> tuple[int, float]:
    target = share * total
    cum = 0
    for i, c in enumerate(counts_desc, start=1):
        cum += c
        if cum >= target:
            return i, round(100.0 * i / len(counts_desc), 4)
    return len(counts_desc), 100.0


def concentration_measures(conn, table: str, where_clause: str, label: str) -> dict:
    counts = venue_counts(conn, table, where_clause)
    total = sum(counts)
    n_venues = len(counts)
    hhi = sum((c / total) ** 2 for c in counts) if total else None
    effective = round(1.0 / hhi, 2) if hhi else None
    n50, pct50 = venues_for_share(counts, 0.5, total)
    n90, pct90 = venues_for_share(counts, 0.9, total)
    top10 = pct(sum(counts[:10]), total)
    top50 = pct(sum(counts[:50]), total)
    return {
        "label": label, "distinct_venues": n_venues, "total_citations": total,
        "top10_pct_of_total": top10, "top50_pct_of_total": top50,
        "hhi": round(hhi, 4) if hhi else None, "effective_venues": effective,
        "gini": gini(counts),
        "venues_for_50pct": {"n": n50, "pct_of_distinct_venues": pct50},
        "venues_for_90pct": {"n": n90, "pct_of_distinct_venues": pct90},
    }


def strict_venue_concentration(conn) -> dict:
    """Concentration of the strict-predatory citations over venues, before and after the identity-check exclusions."""
    before = concentration_measures(conn, "entries_confirmed_all", "final_assessment='predatory'", "strict_predatory_all_entries")
    after = concentration_measures(conn, "entries_confirmed", "final_assessment='predatory'", "strict_predatory_after_exclusion")
    return {"before_identity_check": before, "after_identity_check": after}


# ---------------------------------------------------------------------------
# Verdicts of the audited citations recomputed without their invalid votes
# ---------------------------------------------------------------------------

def load_jsonl_records(targets: list[tuple[str, str, str, str]]) -> dict[tuple, dict]:
    results_root = stats_common.required_path_env("EVAL_RESULTS_ROOT")
    by_file: dict[Path, set[str]] = defaultdict(set)
    file_of: dict[Path, tuple[str, str, str]] = {}
    for month, arxiv_id, suffix, entry_key in targets:
        p = results_root / f"{month}__{arxiv_id}-{suffix}.jsonl"
        by_file[p].add(entry_key)
        file_of[p] = (month, arxiv_id, suffix)
    out: dict[tuple, dict] = {}
    for path, wanted in by_file.items():
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ek = rec.get("entry_key")
                if ek in wanted:
                    out[(*file_of[path], ek)] = rec
    return out


def recompute_assessment_excluding(backend_results: list[dict], exclude_backends: set[str]) -> str:
    """Category of the tool's weighted-vote rule (decision_rule.py) from the backend results with status "found",
    leaving out the backends in exclude_backends."""
    found = [r for r in backend_results if r.get("status") == "found" and r.get("backend_name") not in exclude_backends]
    return weighted_vote_assessment(found)[0]


def recompute_audited_verdicts(conn, any_row_has_issn: set[str]) -> dict:
    """Recomputes the verdict of every confirmed-misattribution citation with its invalid-vote backends removed."""
    rows = conn.execute(
        "SELECT e.month AS month, e.arxiv_id AS arxiv_id, e.suffix AS suffix, e.entry_key AS entry_key, "
        "e.issn AS issn, e.predatory_list_hits AS predatory_list_hits, "
        "COALESCE(e.matched_venue_name, e.venue_raw) AS venue "
        "FROM confirmed_misattributions x "
        "JOIN entries_confirmed_all e ON e.month=x.month AND e.arxiv_id=x.arxiv_id "
        "AND e.suffix=x.suffix AND e.entry_key=x.entry_key"
    ).fetchall()
    print(f"recomputing the verdicts of {len(rows)} misattributed citations", flush=True)

    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    records = load_jsonl_records(targets)
    print(f"  loaded {len(records)} jsonl records", flush=True)

    verdict_counts = Counter()
    per_citation = []
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        rec = records.get(key)
        if rec is None:
            verdict_counts["missing_record"] += 1
            continue
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]
        citing_issn = normalize_issn(r["issn"])
        invalid = set()
        if citing_issn is not None:
            per_backend, _ = classify_votes(rec, winners, citing_issn, any_row_has_issn)
            invalid = invalid_backends(per_backend)
        recomputed_verdict = recompute_assessment_excluding(rec.get("backend_results", []), invalid)
        verdict_counts[recomputed_verdict] += 1
        broad_survives = bool(set(winners) - invalid)
        per_citation.append({
            "month": r["month"], "arxiv_id": r["arxiv_id"], "suffix": r["suffix"], "entry_key": r["entry_key"],
            "verdict_valid_votes": recomputed_verdict, "broad_survives": broad_survives,
        })

    n_strict_remaining = sum(1 for c in per_citation if c["verdict_valid_votes"] == "predatory")
    n_broad_remaining = sum(1 for c in per_citation if c["broad_survives"])

    return {
        "n_citations": len(rows),
        "n_audited_that_remain_predatory_without_invalid_votes": n_strict_remaining,
        "n_audited_that_remain_broad_flagged_without_invalid_votes": n_broad_remaining,
        "redistribution": dict(verdict_counts),
        "_per_citation": per_citation,
    }


# ---------------------------------------------------------------------------
# Per-discipline submission rates
# ---------------------------------------------------------------------------

def setup_broad_drops_table(conn: sqlite3.Connection, per_citation: list[dict]) -> None:
    """Fills temp table broad_drops with the audited citations that lose their broad status (broad_survives=False)."""
    conn.execute("CREATE TEMP TABLE broad_drops (month TEXT, arxiv_id TEXT, suffix TEXT, entry_key TEXT)")
    rows = [
        (c["month"], c["arxiv_id"], c["suffix"], c["entry_key"])
        for c in per_citation if not c["broad_survives"]
    ]
    conn.executemany("INSERT INTO broad_drops VALUES (?, ?, ?, ?)", rows)
    print(f"broad_drops temp table: {len(rows)} of {len(per_citation)} audited citations lose their broad flag "
          f"(the others keep a valid vote from another watchlist)", flush=True)


def discipline_submission_rates(conn, cat_conn: sqlite3.Connection) -> dict:
    """Per discipline group: submissions with at least one strict / broad citation before the identity-check exclusions
    (keys *_before_identity_check) and after them (keys strict_after_*, broad_after_*), with Wilson intervals."""
    def per_submission(strict_table: str, use_broad_drops: bool) -> dict[tuple, dict]:
        broad_expr = (
            "SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS ("
            "  SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id "
            "  AND b.suffix=e.suffix AND b.entry_key=e.entry_key"
            ") THEN 1 ELSE 0 END)"
            if use_broad_drops else
            "SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 THEN 1 ELSE 0 END)"
        )
        rows = conn.execute(
            f"""
            SELECT e.month AS month, e.arxiv_id AS arxiv_id,
                   SUM(CASE WHEN e.final_assessment='predatory' THEN 1 ELSE 0 END) n_strict,
                   {broad_expr} n_broad
            FROM {strict_table} e GROUP BY e.month, e.arxiv_id
            """
        ).fetchall()
        return {(r["month"], r["arxiv_id"]): {"n_strict": r["n_strict"], "n_broad": r["n_broad"]} for r in rows}

    counts_before = per_submission("entries_confirmed_all", use_broad_drops=False)
    # Strict counts after exclusion come from entries_confirmed; broad counts after exclusion from entries_confirmed_all
    # without the citations in broad_drops.
    counts_after = per_submission("entries_confirmed_all", use_broad_drops=True)
    strict_after = per_submission("entries_confirmed", use_broad_drops=False)
    for key, v in counts_after.items():
        v["n_strict"] = strict_after[key]["n_strict"]

    category_by_key = {
        (r["month"], r["arxiv_id"]): r["primary_category"]
        for r in cat_conn.execute("SELECT month, arxiv_id, primary_category FROM categories WHERE found=1").fetchall()
    }

    def get_category(month: str, arxiv_id: str) -> str:
        return classify(category_by_key.get((month, arxiv_id)))

    groups = defaultdict(lambda: {"n_submissions": 0, "strict_before": 0, "strict_after": 0, "broad_before": 0, "broad_after": 0})
    for key, before in counts_before.items():
        after = counts_after[key]
        g = get_category(*key)
        groups[g]["n_submissions"] += 1
        groups[g]["strict_before"] += 1 if before["n_strict"] > 0 else 0
        groups[g]["strict_after"] += 1 if after["n_strict"] > 0 else 0
        groups[g]["broad_before"] += 1 if before["n_broad"] > 0 else 0
        groups[g]["broad_after"] += 1 if after["n_broad"] > 0 else 0

    n_total = sum(g["n_submissions"] for g in groups.values())
    out = {}
    for g, v in groups.items():
        n = v["n_submissions"]
        out[g] = {
            "n_submissions": n, "pct_of_corpus": pct(n, n_total),
            "strict_n_before_identity_check": v["strict_before"], "strict_pct_before_identity_check": pct(v["strict_before"], n),
            "strict_ci_before_identity_check": wilson_ci(v["strict_before"], n),
            "strict_after_n": v["strict_after"], "strict_after_pct": pct(v["strict_after"], n),
            "strict_after_ci": wilson_ci(v["strict_after"], n),
            "broad_n_before_identity_check": v["broad_before"], "broad_pct_before_identity_check": pct(v["broad_before"], n),
            "broad_ci_before_identity_check": wilson_ci(v["broad_before"], n),
            "broad_after_n": v["broad_after"], "broad_after_pct": pct(v["broad_after"], n),
            "broad_after_ci": wilson_ci(v["broad_after"], n),
        }

    strict_before_total = sum(v["strict_before"] for v in groups.values())
    strict_after_total = sum(v["strict_after"] for v in groups.values())
    broad_before_total = sum(v["broad_before"] for v in groups.values())
    broad_after_total = sum(v["broad_after"] for v in groups.values())
    out["Corpus-wide"] = {
        "n_submissions": n_total, "pct_of_corpus": 100.0,
        "strict_n_before_identity_check": strict_before_total, "strict_pct_before_identity_check": pct(strict_before_total, n_total),
        "strict_ci_before_identity_check": wilson_ci(strict_before_total, n_total),
        "strict_after_n": strict_after_total, "strict_after_pct": pct(strict_after_total, n_total),
        "strict_after_ci": wilson_ci(strict_after_total, n_total),
        "broad_n_before_identity_check": broad_before_total, "broad_pct_before_identity_check": pct(broad_before_total, n_total),
        "broad_ci_before_identity_check": wilson_ci(broad_before_total, n_total),
        "broad_after_n": broad_after_total, "broad_after_pct": pct(broad_after_total, n_total),
        "broad_after_ci": wilson_ci(broad_after_total, n_total),
    }
    return out


# ---------------------------------------------------------------------------
# Submissions with any documented concern
# ---------------------------------------------------------------------------

# Retraction-risk tiers that count as a documented concern.
FLAGGED_RETRACTION_TIERS = {"moderate", "high", "critical"}


def venue_retraction_basis() -> dict[str, dict]:
    """Per venue, the retraction counts and publication counts of the retraction-risk basis CSV; venues whose rows
    disagree with each other are left out."""
    by_venue_keys: dict[str, set] = {}
    retraction_basis_csv = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "retraction_risk_basis_all_entries.csv"
    with retraction_basis_csv.open() as f:
        for r in csv.DictReader(f):
            key = (r["basis"], r["total_retractions"], r["recent_retractions"], r["total_publications"], r["recent_publications"])
            by_venue_keys.setdefault(r["venue"], set()).add(key)
    venue_basis: dict[str, dict] = {}
    for venue, keys in by_venue_keys.items():
        if len(keys) == 1:
            _, tr, rr, tp, rp = next(iter(keys))
            venue_basis[venue] = {"total_retractions": tr, "recent_retractions": rr, "total_publications": tp, "recent_publications": rp}
    return venue_basis


def any_concern_submissions(conn) -> dict:
    """Submissions with at least one broad-predatory, hijacked or retraction-concern citation, before and after the
    identity-check exclusions (broad drops; retraction tier recomputed by tier_without_recent_rate_below_min_publications)."""
    # Retraction tier recomputed per flagged citation from the per-venue retraction basis.
    venue_basis = venue_retraction_basis()

    flagged = conn.execute(
        "SELECT month, arxiv_id, retraction_risk_level, COALESCE(matched_venue_name, venue_raw) AS venue "
        "FROM entries_confirmed_all WHERE retraction_risk_level IN ('moderate','high','critical')"
    ).fetchall()

    tier_dropped_by_sub: Counter = Counter()
    for r in flagged:
        venue = r["venue"]
        basis = venue_basis.get(venue)
        if basis is None:
            continue
        tier_without_recent_rate = tier_without_recent_rate_below_min_publications(**basis)
        if r["retraction_risk_level"] in FLAGGED_RETRACTION_TIERS and tier_without_recent_rate not in FLAGGED_RETRACTION_TIERS:
            tier_dropped_by_sub[(r["month"], r["arxiv_id"])] += 1

    papers = conn.execute(
        """
        SELECT month, arxiv_id,
               SUM(CASE WHEN CAST(n_predatory_list_hits AS INTEGER) > 0 THEN 1 ELSE 0 END) n_broad,
               SUM(CASE WHEN is_hijacked='1' THEN 1 ELSE 0 END) n_hijacked,
               SUM(CASE WHEN retraction_risk_level IN ('critical','high','moderate') THEN 1 ELSE 0 END) n_retraction_concern
        FROM entries_confirmed_all GROUP BY month, arxiv_id
        """
    ).fetchall()
    broad_after_drops_by_sub = {
        (r["month"], r["arxiv_id"]): r["n_broad_after_drops"]
        for r in conn.execute(
            """
            SELECT e.month AS month, e.arxiv_id AS arxiv_id,
                   SUM(CASE WHEN CAST(e.n_predatory_list_hits AS INTEGER) > 0 AND NOT EXISTS (
                       SELECT 1 FROM broad_drops b WHERE b.month=e.month AND b.arxiv_id=e.arxiv_id
                       AND b.suffix=e.suffix AND b.entry_key=e.entry_key
                   ) THEN 1 ELSE 0 END) n_broad_after_drops
            FROM entries_confirmed_all e GROUP BY e.month, e.arxiv_id
            """
        ).fetchall()
    }

    n_papers = len(papers)
    any_flag_before = sum(1 for p in papers if p["n_broad"] > 0 or p["n_hijacked"] > 0 or p["n_retraction_concern"] > 0)

    any_flag_after = 0
    for p in papers:
        key = (p["month"], p["arxiv_id"])
        n_broad_after = broad_after_drops_by_sub.get(key, 0)
        n_concern_after = p["n_retraction_concern"] - tier_dropped_by_sub.get(key, 0)
        if n_broad_after > 0 or p["n_hijacked"] > 0 or n_concern_after > 0:
            any_flag_after += 1

    return {
        "n_papers": n_papers,
        "any_flag_n_before_identity_check": any_flag_before,
        "any_flag_pct_before_identity_check": pct(any_flag_before, n_papers),
        "any_flag_after_n": any_flag_after, "any_flag_pct_after_identity_check": pct(any_flag_after, n_papers),
        "n_submissions_losing_any_flag": any_flag_before - any_flag_after,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write-citation-keys", action="store_true",
                    help="also write identity_audit_citation_detail.json to EVAL_OUTPUT_DIR")
    args = ap.parse_args()

    conn, n_excluded = get_conn_with_exclusions()
    print(f"excluding {n_excluded} confirmed-misattribution citations", flush=True)

    cache_conn = sqlite3.connect(str(stats_common.required_path_env("EVAL_CACHE_DB_PATH")))
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    print("=== venue concentration ===", flush=True)
    venue_concentration = strict_venue_concentration(conn)
    print(json.dumps(venue_concentration, indent=2, default=str), flush=True)

    print("=== recomputed verdicts of the audited citations ===", flush=True)
    verdict_summary = recompute_audited_verdicts(conn, any_row_has_issn)
    per_citation = verdict_summary.pop("_per_citation")
    print(json.dumps(verdict_summary, indent=2, default=str), flush=True)
    if args.write_citation_keys:
        (stats_common.required_path_env("EVAL_OUTPUT_DIR") / "identity_audit_citation_detail.json").write_text(
            json.dumps(per_citation, indent=2), encoding="utf-8"
        )

    setup_broad_drops_table(conn, per_citation)

    print("=== per-discipline submission rates ===", flush=True)
    cat_conn = sqlite3.connect(str(snapshot_categories_db()))
    cat_conn.row_factory = sqlite3.Row
    discipline_rates = discipline_submission_rates(conn, cat_conn)
    print(json.dumps(discipline_rates, indent=2, default=str), flush=True)

    print("=== submissions with any documented concern ===", flush=True)
    any_concern = any_concern_submissions(conn)
    print(json.dumps(any_concern, indent=2, default=str), flush=True)

    output_dir = stats_common.required_path_env("EVAL_OUTPUT_DIR") / "output_identity_check_headline_analyses"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "identity_check_headline_analyses.json"
    out_path.write_text(json.dumps({"venue_concentration": venue_concentration, "audited_verdicts": verdict_summary,
                                     "discipline_submission_rates": discipline_rates, "any_documented_concern": any_concern},
                                    indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
