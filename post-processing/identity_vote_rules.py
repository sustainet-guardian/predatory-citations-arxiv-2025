#!/usr/bin/env python3
"""The backend lists and the ISSN rules of the identity check, in one place; it reads no environment variable and writes nothing.

A vote of a predatory-list backend is checked against the ISSN of the citing .bib entry: it is valid when the ISSN on the
cache row the vote is attached to equals the citing ISSN, and invalid when that cache ISSN differs, or when the cache row has
no ISSN while another cache row carries the citing ISSN. The remaining vote of the two lists in
CLONE_DETECTION_BACKENDS cannot be decided by ISSN. classify_votes gives the per-backend types and the verdict of a
citation, invalid_backends the backends whose votes are invalid, build_invalid_backends_map the latter for many citations.
Also: ALL_BACKENDS (the 22 backends of the tool's ensemble), WATCHLIST_BACKENDS (its nine predatory-list backends),
WATCHLIST_ISSN_CAPABLE (whether the loader of a list extracts an ISSN) and the ISSN helpers.
WATCHLIST_ISSN_CAPABLE has ten keys and WATCHLIST_BACKENDS nine: kscien_hijacked_journals is one of the 22 backends but not
one of the nine predatory-list backends, because it never adds a predatory or a legitimate vote.
"""
from __future__ import annotations

ALL_BACKENDS = [
    "algerian_ministry", "bealls", "kscien_publishers", "kscien_predatory_conferences",
    "predatoryjournals", "kscien_standalone_journals", "ugc_care_cloned",
    "ugc_care_cloned_group2", "ugc_care_delisted_group2",
    "kscien_hijacked_journals",
    "doaj", "dblp_venues", "core_journals", "core_conferences", "scopus", "pubmed_nlm",
    "ugc_care_included_from_clone_group1", "ugc_care_included_from_clone_group2",
    "openalex_analyzer", "crossref_analyzer", "opencitations_analyzer",
    "retraction_watch",
]

WATCHLIST_BACKENDS = [
    "bealls",
    "predatoryjournals",
    "kscien_publishers",
    "kscien_standalone_journals",
    "kscien_predatory_conferences",
    "algerian_ministry",
    "ugc_care_cloned",
    "ugc_care_cloned_group2",
    "ugc_care_delisted_group2",
]

# Whether the tool's loader for each list extracts an ISSN from the list's source.
WATCHLIST_ISSN_CAPABLE = {
    "bealls": False, "algerian_ministry": False,
    "kscien_standalone_journals": False, "kscien_publishers": False,
    "kscien_predatory_conferences": False, "kscien_hijacked_journals": False,
    "predatoryjournals": True,
    "ugc_care_cloned": True, "ugc_care_cloned_group2": True, "ugc_care_delisted_group2": True,
}

CLONE_DETECTION_BACKENDS = {"ugc_care_cloned", "ugc_care_cloned_group2"}


def normalize_issn(value: str | None) -> str | None:
    """Strip dashes and whitespace and uppercase, so '1234-5678' and '12345678' compare equal."""
    if not value:
        return None
    cleaned = "".join(ch for ch in value.upper() if ch.isalnum())
    return cleaned or None


def format_issn(value: str | None) -> str | None:
    """NNNNNNNX -> NNNN-NNNX."""
    if not value or len(value) != 8:
        return value
    return f"{value[:4]}-{value[4:]}"


def matched_cache_issn(rec: dict, backend_name: str) -> str | None:
    """The ISSN of the cache row that the 'found' result of `backend_name` in the record is attached to."""
    for b in rec.get("backend_results", []):
        if b.get("backend_name") != backend_name or b.get("status") != "found":
            continue
        data = b.get("data") or {}
        sd = data.get("source_data")
        return sd.get("issn") if sd else None
    return None


def build_any_row_has_issn_index(conn) -> set[str]:
    """The normalized ISSNs that occur on at least one row of the cache table `journals`."""
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT issn FROM journals WHERE issn IS NOT NULL AND issn != ''")
    return {normalize_issn(r[0]) for r in cur.fetchall()}


def classify_votes(rec: dict, winners: list[str], citing_issn: str, any_row_has_issn: set[str]) -> tuple[list[dict], str]:
    """Checks the votes of the winning backends against the normalized citing ISSN (not None).
    Returns (per_backend, verdict). Each entry of per_backend has the type
      valid_issn_match         the cache ISSN equals the citing ISSN;
      issn_mismatch            the cache ISSN differs;
      null_cache_issn          the cache row has no ISSN; `separate_row_with_citing_issn_exists` tells whether another cache
                               row carries the citing ISSN;
      not_decidable_by_issn    the backend is one of CLONE_DETECTION_BACKENDS.
    The verdict is confirmed_misattribution if some vote is an issn_mismatch or a corroborated null_cache_issn (see
    invalid_backends), else structurally_undecidable if some vote is not decidable, else confirmed_correct."""
    per_backend = []
    any_misattribution = False
    any_not_decidable = False
    for backend in winners:
        cache_issn = normalize_issn(matched_cache_issn(rec, backend))
        if backend in CLONE_DETECTION_BACKENDS:
            per_backend.append({"backend": backend, "type": "not_decidable_by_issn", "cache_issn": cache_issn})
            any_not_decidable = True
            continue
        if cache_issn == citing_issn:
            per_backend.append({"backend": backend, "type": "valid_issn_match", "cache_issn": cache_issn})
        elif cache_issn is not None:
            per_backend.append({"backend": backend, "type": "issn_mismatch", "cache_issn": cache_issn})
            any_misattribution = True
        else:
            corroborated_elsewhere = citing_issn in any_row_has_issn
            per_backend.append({
                "backend": backend, "type": "null_cache_issn",
                "cache_issn": None, "separate_row_with_citing_issn_exists": corroborated_elsewhere,
            })
            if corroborated_elsewhere:
                any_misattribution = True

    if any_misattribution:
        verdict = "confirmed_misattribution"
    elif any_not_decidable:
        verdict = "structurally_undecidable"
    else:
        verdict = "confirmed_correct"
    return per_backend, verdict


def invalid_backends(per_backend: list[dict]) -> set[str]:
    """The backends whose vote is invalid: the cache ISSN differs from the citing ISSN (`issn_mismatch`), or the cache row has
    no ISSN and another cache row carries the citing ISSN (`null_cache_issn` with `separate_row_with_citing_issn_exists`)."""
    return {
        pb["backend"] for pb in per_backend
        if pb["type"] == "issn_mismatch"
        or (pb["type"] == "null_cache_issn" and pb["separate_row_with_citing_issn_exists"])
    }


def build_invalid_backends_map(conn, cache_conn, exclude_keys: set) -> dict[tuple, set]:
    """For every strict-predatory citation of entries_confirmed_all that is in `exclude_keys` and has a citing ISSN and a
    stored backend record, the set of backends whose vote is invalid (see invalid_backends). `conn` returns sqlite3.Row rows;
    `cache_conn` is the connection to the cache database. Reads the records under EVAL_RESULTS_ROOT."""
    from leave_one_out_helpers import load_records  # imported here: the helper module reads environment variables at import

    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits "
        "FROM entries_confirmed_all WHERE final_assessment='predatory'"
    ).fetchall()
    targets = [
        (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        for r in rows if (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) in exclude_keys
    ]
    records = load_records(targets)
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    invalid_map: dict[tuple, set] = {}
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        if key not in exclude_keys:
            continue
        citing_issn = normalize_issn(r["issn"])
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]
        rec = records.get(key)
        if rec is None or citing_issn is None:
            continue
        per_backend, _ = classify_votes(rec, winners, citing_issn, any_row_has_issn)
        invalid_map[key] = invalid_backends(per_backend)
    return invalid_map
