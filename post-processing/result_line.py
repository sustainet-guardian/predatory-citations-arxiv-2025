#!/usr/bin/env python3
"""The columns of an `entries` row that come from one line of the assessment results (a JSONL line of the tool): one function,
result_line_columns, used by build_entry_table.py and insert_missed_keys_rows.py, and has_hijacked_vote, which analyze_bulk_database_assessment.py
and analyze_hijacked_doaj_benchmark.py use too. A line has the fields of the entry (venue_raw, final_assessment, ...) and the list
backend_results; it has no field is_hijacked, is_retracted, retraction_risk_level, matched_venue_name or matched_publisher: these come from
backend_results. Reads no environment variable and no file."""
from __future__ import annotations

from typing import Any


def has_hijacked_vote(rec: dict) -> bool:
    """True when some backend result of the line has assessment "hijacked" (the KSCIEN hijacked-journal list)."""
    return any(isinstance(b, dict) and b.get("assessment") == "hijacked" for b in rec.get("backend_results") or [])


def _scalarize(value: Any) -> str | None:
    """Crossref represents title/publisher as JSON arrays; returns the first element as a string."""
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)


def best_venue_identity(backend_results: list[dict]) -> tuple[str | None, str | None]:
    by_name = {b.get("backend_name"): b for b in backend_results if isinstance(b, dict)}

    oa = by_name.get("openalex_analyzer", {})
    oa_data = (oa.get("data") or {}).get("openalex_data") or {}
    if oa_data.get("display_name"):
        return _scalarize(oa_data.get("display_name")), _scalarize(oa_data.get("publisher"))

    cr = by_name.get("crossref_analyzer", {})
    cr_data = (cr.get("data") or {}).get("crossref_data") or {}
    if cr_data.get("title"):
        return _scalarize(cr_data.get("title")), _scalarize(cr_data.get("publisher"))

    for b in backend_results:
        if not isinstance(b, dict):
            continue
        sd = (b.get("data") or {}).get("source_data")
        if sd and sd.get("display_name"):
            return _scalarize(sd.get("display_name")), _scalarize(sd.get("publisher"))

    return None, None


def result_line_columns(rec: dict) -> dict[str, Any]:
    """The columns of an `entries` row that come from one line of the results file: the fields of the line, the number and the
    list of predatory-list hits, is_hijacked (some backend result has assessment "hijacked"), is_retracted and
    retraction_risk_level (the found result of the retraction_watch backend: total_retractions and risk_level) and
    matched_venue_name and matched_publisher (best_venue_identity of the backend results). build_entry_table.py and
    insert_missed_keys_rows.py both build their rows from it. A results line has no field for these columns; reading rec["is_hijacked"]
    and the like gives nothing."""
    backend_results = rec.get("backend_results") or []
    predatory_hits = rec.get("predatory_list_hits") or []

    is_hijacked = has_hijacked_vote(rec)

    is_retracted = False
    retraction_risk = None
    for b in backend_results:
        if isinstance(b, dict) and b.get("backend_name") == "retraction_watch" and b.get("status") == "found":
            data = b.get("data") or {}
            is_retracted = bool(data.get("total_retractions"))
            retraction_risk = data.get("risk_level")
            break

    venue_name, publisher = best_venue_identity(backend_results)
    return {
        "venue_raw": rec.get("venue_raw"), "venue_type": rec.get("venue_type"),
        "issn": rec.get("issn"), "eissn": rec.get("eissn"), "doi": rec.get("doi"),
        "state": rec.get("state"), "final_assessment": rec.get("final_assessment"), "confidence": rec.get("confidence"),
        "overall_score": rec.get("overall_score"), "is_suspicious": rec.get("is_suspicious"),
        "has_conflict": rec.get("has_conflict"),
        "predatory_votes": rec.get("predatory_votes"), "legitimate_votes": rec.get("legitimate_votes"),
        "n_predatory_list_hits": len(predatory_hits),
        "predatory_list_hits": ",".join(predatory_hits) if predatory_hits else None,
        "is_hijacked": int(is_hijacked), "is_retracted": int(is_retracted), "retraction_risk_level": retraction_risk,
        "matched_venue_name": venue_name, "matched_publisher": publisher,
    }
