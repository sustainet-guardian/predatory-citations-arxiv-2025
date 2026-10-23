#!/usr/bin/env python3
"""The tool's weighted-vote category rule, applied to stored backend votes. Reads no environment variable and no file.
weighted_vote_assessment(votes) takes the backend results with status "found" that count (the caller leaves out the
backends it excludes and adds simulated votes) and returns (assessment, confidence); every backend weight is 1.0, votes of
evidence type quality_indicator are skipped, and the confidence has no agreement bonus. Used by leave_one_out_helpers.py,
identity_check_headline_analyses.py and add_one_new_sources_impact.py."""
from __future__ import annotations


def weighted_vote_assessment(votes: list[dict]) -> tuple[str, float]:
    """Category and confidence from the votes: with at least one predatory-list vote the category is predatory when the
    predatory weight exceeds the legitimate weight and legitimate otherwise; without one it is legitimate when any
    legitimate weight exists, suspicious when only predatory weight exists, and unknown when there is no vote."""
    predatory_weight = 0.0
    legitimate_weight = 0.0
    total_weight = 0.0
    predatory_list_evidence = 0

    for r in votes:
        if r.get("evidence_type") == "quality_indicator":
            continue
        weight = 1.0
        conf = r.get("confidence") or 0.0
        total_weight += weight
        if r.get("assessment") == "predatory":
            predatory_weight += conf * weight
        elif r.get("assessment") == "legitimate":
            legitimate_weight += conf * weight
        if r.get("evidence_type") == "predatory_list" and r.get("assessment") == "predatory":
            predatory_list_evidence += 1

    if total_weight == 0:
        return ("unknown", 0.1)

    if predatory_list_evidence > 0:
        if predatory_weight > legitimate_weight:
            return ("predatory", min(0.95, predatory_weight / total_weight))
        return ("legitimate", min(0.9, legitimate_weight / total_weight))

    if legitimate_weight > 0:
        return ("legitimate", min(0.9, legitimate_weight / total_weight))

    if predatory_weight > 0:
        return ("suspicious", min(0.85, predatory_weight / total_weight))

    return ("unknown", 0.1)
