#!/usr/bin/env python3
"""Collects every numbers/*.numbers.json into numbers.tex: one \\newcommand per key, snake_case to CamelCase. Aborts on a key
collision between files and checks the cross-file invariants below whose inputs exist before writing: an invariant whose numbers
key is missing is printed as `SKIPPED <name>`, the summary line lists their number and names, the last line printed is
"<n> skipped", and the exit status stays 0 so that a partial numbers/ directory is visible. The output is deterministic (sorted
keys, fixed formatting); set NUMBERS_TEX_OUTPUT to write it elsewhere than next to this script.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NUMBERS_DIR = Path(__file__).parent / "numbers"
OUTPUT_PATH = Path(os.environ.get("NUMBERS_TEX_OUTPUT", str(Path(__file__).parent / "numbers.tex")))
RESERVED_KEYS = {"_source", "_generated_at"}


def snake_to_camel(key: str) -> str:
    """LaTeX control names consist of letters only, so digits are stripped from each underscore-separated part before
    capitalizing; a part made only of digits disappears. Two keys can therefore map to the same macro name; main() checks that."""
    parts = []
    for part in key.split("_"):
        letters_only = "".join(c for c in part if c.isalpha())
        if letters_only:
            parts.append(letters_only.capitalize())
    return "".join(parts)


def format_int(value: int) -> str:
    s = f"{abs(value):,}".replace(",", "{,}")
    return f"$-${s}" if value < 0 else s


def format_float(value: float, decimals: int) -> str:
    rounded = round(value, decimals)
    s = f"{abs(rounded):.{decimals}f}"
    return f"$-${s}" if rounded < 0 else s


def format_ratio(num: float, den: float, decimals: int) -> str:
    if den == 0:
        raise ValueError("zero denominator")
    return format_float(100.0 * num / den, decimals)


def render_value(key: str, entry: dict) -> str:
    kind = entry["kind"]
    if kind == "int":
        return format_int(entry["value"])
    if kind == "float":
        return format_float(entry["value"], entry["decimals"])
    if kind == "ratio":
        return format_ratio(entry["num"], entry["den"], entry["decimals"])
    raise ValueError(f"{key}: unknown kind {kind!r}")


def load_all() -> dict[str, tuple[dict, str, str]]:
    """key -> (entry, source_script, source_file)"""
    merged: dict[str, tuple[dict, str, str]] = {}
    for path in sorted(NUMBERS_DIR.glob("*.numbers.json")):
        data = json.loads(path.read_text())
        source = data.get("_source", path.stem)
        for key, entry in data.items():
            if key in RESERVED_KEYS:
                continue
            if key in merged:
                _prev_entry, prev_source, prev_file = merged[key]
                raise SystemExit(
                    f"KEY COLLISION: '{key}' is written by both {prev_source!r} "
                    f"({prev_file}) and {source!r} ({path.name}); a key must belong to one script."
                )
            merged[key] = (entry, source, path.name)
    return merged


INVARIANTS: list[tuple[str, "callable"]] = []


def invariant(name: str):
    def deco(fn):
        INVARIANTS.append((name, fn))
        return fn
    return deco


# --- Registered invariants -------------------------------------------------
# An invariant is skipped (not failed) if a key it needs is not in numbers/.

@invariant("corpus partition: excluded + unverified + confirmed-cited = candidate population")
def _inv_corpus_partition(merged):
    excluded = merged["corpus_excluded_entries"][0]["value"]
    unverified = merged["corpus_unverified_entries"][0]["value"]
    confirmed_cited = merged["corpus_confirmed_cited_entries"][0]["value"]
    candidate = merged["corpus_candidate_population"][0]["value"]
    assert excluded + unverified + confirmed_cited == candidate, (
        f"{excluded} + {unverified} + {confirmed_cited} != {candidate}"
    )


@invariant("log-having entries: identified-as-cited minus materialization gap minus residual = confirmed-cited")
def _inv_log_having_reconciliation(merged):
    identified = merged["a1_log_having_identified_as_cited"][0]["value"]
    materialization_gap = merged["a1_log_having_materialization_gap"][0]["value"]
    residual = merged["a1_log_having_residual_x"][0]["value"]
    confirmed_cited = merged["corpus_confirmed_cited_entries"][0]["value"]
    assert identified - materialization_gap - residual == confirmed_cited, (
        f"{identified} - {materialization_gap} - {residual} != {confirmed_cited}"
    )


@invariant("strict partition: identity-verified + identity-unverified + no-ISSN + undecided = strict_count")
def _inv_strict_partition(merged):
    verified = merged["strict_identity_verified"][0]["value"]
    unverified = merged["strict_identity_unverified"][0]["value"]
    no_issn = merged["strict_no_issn"][0]["value"]
    undecided = merged["strict_undecided"][0]["value"]
    strict_count = merged["strict_count"][0]["value"]
    assert verified + unverified + no_issn + undecided == strict_count, (
        f"{verified} + {unverified} + {no_issn} + {undecided} != {strict_count}"
    )


@invariant("audit partition: mismatch + confirmed-correct + no-ISSN + structurally-undecidable = strict total before the identity check")
def _inv_audit_partition_before_identity_check(merged):
    mismatch = merged["audit_mismatch"][0]["value"]
    confirmed_correct = merged["audit_confirmed_correct"][0]["value"]
    no_issn = merged["audit_no_issn"][0]["value"]
    undecidable = merged["audit_structurally_undecidable"][0]["value"]
    strict_before_identity_check = merged["audit_original_strict_total"][0]["value"]
    assert mismatch + confirmed_correct + no_issn + undecidable == strict_before_identity_check, (
        f"{mismatch} + {confirmed_correct} + {no_issn} + {undecidable} != {strict_before_identity_check}"
    )


@invariant("Retraction Watch hijacked-journal benchmark: the four ISSN categories partition the entries")
def _inv_rwclone_partition(merged):
    parts = [merged[k][0]["value"] for k in (
        "rwclone_exact_string_match_n", "rwclone_overlapping_normalized_match_n",
        "rwclone_hijacked_issn_missing_n", "rwclone_different_issn_n")]
    total = merged["rwclone_n_total"][0]["value"]
    assert sum(parts) == total, f"{' + '.join(str(p) for p in parts)} != {total}"


@invariant("nocite bbl-only partition: attributable + non-attributable = total")
def _inv_nocite_bbl_only_partition(merged):
    attributable = merged["nocite_bbl_only_attributable"][0]["value"]
    non_attributable = merged["nocite_bbl_only_non_attributable"][0]["value"]
    total = merged["nocite_bbl_only_total"][0]["value"]
    assert attributable + non_attributable == total, f"{attributable} + {non_attributable} != {total}"


@invariant("nocite: promoted-and-attributable is a subset of the bbl-only-attributable superset")
def _inv_nocite_promoted_subset_of_attributable(merged):
    promoted_attributable = merged["nocite_n_promoted_attributable"][0]["value"]
    bbl_only_attributable = merged["nocite_bbl_only_attributable"][0]["value"]
    assert promoted_attributable <= bbl_only_attributable, (
        f"{promoted_attributable} > {bbl_only_attributable}"
    )


@invariant("nocite exclusion: excluded denominator/strict/broad match confirmed_cited and the strict/broad counts after the identity check minus the nocite-attributable promotions")
def _inv_nocite_exclusion_arithmetic(merged):
    confirmed_cited = merged["corpus_confirmed_cited_entries"][0]["value"]
    strict_after_identity_check = merged["multibib_current_strict_rate"][0]["num"]
    broad_after_identity_check = merged["multibib_current_broad_rate"][0]["num"]
    promoted_attributable = merged["nocite_n_promoted_attributable"][0]["value"]
    promoted_strict = merged["nocite_n_promoted_attributable_strict"][0]["value"]
    promoted_broad = merged["nocite_n_promoted_attributable_broad"][0]["value"]
    excluded_denominator = merged["nocite_excluded_denominator"][0]["value"]
    excluded_strict = merged["nocite_excluded_strict_count"][0]["value"]
    excluded_broad = merged["nocite_excluded_broad_count"][0]["value"]
    assert excluded_denominator == confirmed_cited - promoted_attributable, (
        f"{excluded_denominator} != {confirmed_cited} - {promoted_attributable}"
    )
    assert excluded_strict == strict_after_identity_check - promoted_strict, (
        f"{excluded_strict} != {strict_after_identity_check} - {promoted_strict}"
    )
    assert excluded_broad == broad_after_identity_check - promoted_broad, (
        f"{excluded_broad} != {broad_after_identity_check} - {promoted_broad}"
    )
    assert merged["nocite_pct_strict_of_attributable"][0]["num"] == promoted_strict
    assert merged["nocite_pct_broad_of_attributable"][0]["num"] == promoted_broad
    assert merged["nocite_pct_strict_of_attributable"][0]["den"] == merged["nocite_pct_broad_of_attributable"][0]["den"] == promoted_attributable
    assert merged["nocite_excluded_strict_rate"][0]["num"] == excluded_strict
    assert merged["nocite_excluded_broad_rate"][0]["num"] == excluded_broad
    assert merged["nocite_excluded_strict_rate"][0]["den"] == excluded_denominator
    assert merged["nocite_excluded_broad_rate"][0]["den"] == excluded_denominator


@invariant("watchlist_split_and_math_broad_rate: its recomputed broad-predatory total equals broad_count")
def _inv_broad_count_matches_live_recompute(merged):
    live = merged["backends_split_n_broad_total"][0]["value"]
    broad_count = merged["broad_count"][0]["value"]
    assert live == broad_count, f"{live} != {broad_count}"


@invariant("leave-one-out independent citations: the recomputed strict-predatory basis equals strict_count")
def _inv_leave_one_out_strict_basis_matches_strict_count(merged):
    lon_basis = merged["l1o69_n_strict_basis"][0]["value"]
    strict_count = merged["strict_count"][0]["value"]
    assert lon_basis == strict_count, f"{lon_basis} != {strict_count}"


@invariant("leave-one-out with invalid votes removed: its strict/broad baselines equal strict_count/broad_count")
def _inv_leave_one_out_baselines_match(merged):
    lon_strict_baseline = merged["leave_one_out_strict_baseline"][0]["value"]
    lon_broad_baseline = merged["leave_one_out_broad_baseline"][0]["value"]
    strict_count = merged["strict_count"][0]["value"]
    broad_count = merged["broad_count"][0]["value"]
    assert lon_strict_baseline == strict_count, f"{lon_strict_baseline} != {strict_count}"
    assert lon_broad_baseline == broad_count, f"{lon_broad_baseline} != {broad_count}"


@invariant("submission flag counts: before = after + lost - gained, and after equals the disciplinary submission counts (strict and broad)")
def _inv_submission_flag_counts_partition(merged):
    for crit in ("strict", "broad"):
        before = merged[f"correction_before_{crit}_submissions_n"][0]["value"]
        after = merged[f"correction_after_{crit}_submissions_n"][0]["value"]
        lost = merged[f"correction_lost_{crit}_submissions_n"][0]["value"]
        gained = merged[f"correction_gained_{crit}_submissions_n"][0]["value"]
        assert before == after + lost - gained, f"{crit}: {before} != {after} + {lost} - {gained}"
        disc = merged[f"discsubcount_n_{crit}"][0]["value"]
        assert after == disc, f"{crit}: after {after} != discsubcount_n_{crit} {disc}"


@invariant("submission flag counts: the citation-level chain equals the audit partition (strict count before the identity check, strict_count, "
           "mismatches minus second-vote survivors) and the broad basis (broad_count)")
def _inv_submission_flag_counts_citation_chain(merged):
    before_s = merged["correction_before_strict_citations_n"][0]["value"]
    after_s = merged["correction_after_strict_citations_n"][0]["value"]
    after_b = merged["correction_after_broad_citations_n"][0]["value"]
    assert before_s == merged["audit_original_strict_total"][0]["value"], (
        f"{before_s} != audit_original_strict_total"
    )
    assert after_s == merged["strict_count"][0]["value"], f"{after_s} != strict_count"
    removed = merged["audit_mismatch"][0]["value"] - merged["strict_n_second_vote_survivors"][0]["value"]
    assert before_s - after_s == removed, f"{before_s} - {after_s} != {removed}"
    assert after_b == merged["broad_count"][0]["value"], f"{after_b} != broad_count"


@invariant("submission flag counts: their number cannot exceed the citations removed by the identity check (strict and broad)")
def _inv_submission_flag_counts_bounded_by_citations(merged):
    for crit in ("strict", "broad"):
        lost = merged[f"correction_lost_{crit}_submissions_n"][0]["value"]
        removed = (merged[f"correction_before_{crit}_citations_n"][0]["value"]
                   - merged[f"correction_after_{crit}_citations_n"][0]["value"])
        assert 0 <= lost <= removed, f"{crit}: lost {lost} not within [0, {removed}]"


# --- Derived values and report numbers ---

def _v(merged, key):
    """Value of an int-kind key (KeyError -> the invariant is skipped)."""
    return merged[key][0]["value"]


@invariant("derived arithmetic: submissions without a confirmed-cited entry, strict references lost without the Algerian list, "
           "venue multiple, DOI gap")
def _inv_derived_arithmetic(merged):
    assert (_v(merged, "derived_submissions_without_confirmed_cited_n") + _v(merged, "discsubcount_n_total")
            == _v(merged, "corpus_covered_submissions")), "submissions without + with a confirmed-cited entry != covered"
    assert (_v(merged, "derived_algerian_exclusion_strict_loss_n")
            + _v(merged, "leave_one_out_algerian_ministry_strict_after_exclusion") == _v(merged, "strict_count"))
    venues = _v(merged, "derived_legit_plus_unknown_distinct_venues")
    assert _v(merged, "derived_legit_unknown_venues_per_strict_venue") == round(venues / _v(merged, "conc_strict_distinct_venues"))
    strict_doi = merged["doi_strict_predatory_pct"][0]
    assert strict_doi["den"] == _v(merged, "strict_count"), f"DOI strict denominator {strict_doi['den']} != strict_count"
    legit_doi = merged["doi_legitimate_pct"][0]
    gap = 100.0 * legit_doi["num"] / legit_doi["den"] - 100.0 * strict_doi["num"] / strict_doi["den"]
    assert abs(merged["derived_doi_gap_points"][0]["value"] - gap) < 1e-9, "DOI gap does not follow from the two rates"


@invariant("citation-log shares: files with a citation log + files without = assessed files, and the share is built from exactly those counts")
def _inv_citation_log_share(merged):
    assessed = _v(merged, "corpus_assessed_files")
    assert _v(merged, "corpus_sidecar_files") + _v(merged, "corpus_nosidecar_files") == assessed
    share = merged["derived_sidecar_pct_of_assessed_files"][0]
    assert (share["num"], share["den"]) == (_v(merged, "corpus_sidecar_files"), assessed)


@invariant("sibling inference: no-log files with a log-having sibling + isolated ones = all no-log files")
def _inv_sibling_split(merged):
    assert (_v(merged, "corpus_nosidecar_sibling_files_n") + _v(merged, "corpus_nosidecar_isolated_files_n")
            == _v(merged, "corpus_nosidecar_files"))
    for key in ("corpus_nosidecar_sibling_pct", "corpus_nosidecar_isolated_pct"):
        assert merged[key][0]["den"] == _v(merged, "corpus_nosidecar_files"), key


@invariant("multi-.bib files: files in multi-.bib submissions are a subset of the assessed files")
def _inv_multibib_files(merged):
    files = _v(merged, "corpus_multi_bib_files_n")
    assert 0 < files <= _v(merged, "corpus_assessed_files")
    assert merged["corpus_multi_bib_files_pct"][0]["den"] == _v(merged, "corpus_assessed_files")
    assert files >= 2 * _v(merged, "corpus_multi_bib_submissions"), "a multi-.bib submission has at least two files"


@invariant("negative control: drawn = clean + excluded for a watchlist hit")
def _inv_negctrl_total(merged):
    assert (_v(merged, "negctrl_n_total_drawn")
            == _v(merged, "negctrl_n_clean") + _v(merged, "negctrl_n_excluded_watchlist_hit"))


@invariant("DOAJ crosstab: legitimate-resolving + other = disjoint journals, "
           "journals in the current listing counted within their groups")
def _inv_doaj_crosstab(merged):
    assert _v(merged, "doajxtab_legit_n") + _v(merged, "doajxtab_nonlegit_n") == _v(merged, "doajxtab_n_disjoint")
    assert 0 <= _v(merged, "doajxtab_legit_n_reinstated") <= _v(merged, "doajxtab_legit_n")
    assert 0 <= _v(merged, "doajxtab_nonlegit_n_reinstated") <= _v(merged, "doajxtab_nonlegit_n")


@invariant("add-one sources: combined affected references = the three per-source counts summed, minus the "
           "references that match more than one source")
def _inv_addone_combined(merged):
    per_source = sum(_v(merged, f"addone_{s}_n_affected") for s in ("acl", "rw", "doaj"))
    combined, multi = _v(merged, "addone_combined_n_affected"), _v(merged, "addone_combined_n_multi_source")
    assert combined == per_source - multi, f"{combined} != {per_source} - {multi}"
    for s in ("rw", "doaj"):
        assert _v(merged, f"addone_{s}_n_predatory_kept") <= _v(merged, f"addone_{s}_n_affected"), s


@invariant("vote-threshold alternatives: the audited citations equal the audit mismatches; the exported strictalt_* counts equal the "
           "counts before the identity check; the counts after the identity check are positive and at most the counts before it; "
           "the resolved-eligible population equals the headline one and holds all strict_count references")
def _inv_strictvote(merged):
    assert _v(merged, "strictvote_n_audited_citations") == _v(merged, "audit_mismatch")
    for rule, exported in (("ge2", "strictalt_ge2_votes_n"), ("votes_ge_legit", "strictalt_votes_ge_legit_n")):
        assert _v(merged, f"strictvote_{rule}_all_precorr_n") == _v(merged, exported), rule
        for pop in ("all", "resolved"):
            pre, post = _v(merged, f"strictvote_{rule}_{pop}_precorr_n"), _v(merged, f"strictvote_{rule}_{pop}_corrected_n")
            assert 0 < post <= pre, f"{rule}/{pop}: after the identity check {post} vs before {pre}"
        assert _v(merged, f"strictvote_{rule}_audited_corrected_n") <= _v(merged, f"strictvote_{rule}_audited_precorr_n")
    assert _v(merged, "strictvote_resolved_population_n") == _v(merged, "resolved_eligible_population_new")
    assert _v(merged, "strictvote_strict_in_resolved_n") == _v(merged, "strict_count"), "strict references after the identity check not all inside the resolved-eligible population"


@invariant("missed keys: population equal in both exports; counts after the identity check within the counts before it and the headline; strict equals missedkeys_n_strict")
def _inv_missed_keys(merged):
    assert _v(merged, "missedkeys_current_n_population") == _v(merged, "missedkeys_n_population")
    for crit, headline in (("strict", "strict_count"), ("broad", "broad_count")):
        before, after = _v(merged, f"missedkeys_current_{crit}_before_n"), _v(merged, f"missedkeys_current_{crit}_after_n")
        assert 0 < after <= before, crit
        assert after <= _v(merged, headline), crit
    assert _v(merged, "missedkeys_current_strict_after_n") == _v(merged, "missedkeys_n_strict")


# --- Confirmed-cited populations and their descriptions ---

@invariant("confirmed-cited composition: own-log + missed keys + promoted in no-log files = total = "
           "corpus_confirmed_cited_entries; population without the missed-keys rows + missed keys = total; nothing else")
def _inv_confirmed_cited_composition(merged):
    total = _v(merged, "confpop_n_total")
    assert total == _v(merged, "corpus_confirmed_cited_entries"), "total != corpus_confirmed_cited_entries"
    assert (_v(merged, "confpop_n_own_log") + _v(merged, "confpop_n_missed_keys")
            + _v(merged, "confpop_n_promoted_no_log")) == total
    assert _v(merged, "confpop_n_other_origin") == 0, "confirmed-cited entries of an unlisted origin"
    assert (_v(merged, "confpop_n_promoted_no_log_isolated") + _v(merged, "confpop_n_promoted_no_log_sibling")
            == _v(merged, "confpop_n_promoted_no_log"))
    assert _v(merged, "confpop_n_original_population") + _v(merged, "confpop_n_missed_keys") == total
    assert _v(merged, "confpop_n_missed_keys") == _v(merged, "missedkeys_n_population")


@invariant("confirmed-cited composition: promoted = missed keys + promoted in no-log files = nocite_n_promoted_total; "
           "entries by file class add up and the file counts match the corpus table")
def _inv_promoted_entries_and_file_classes(merged):
    assert (_v(merged, "confpop_n_missed_keys") + _v(merged, "confpop_n_promoted_no_log")
            == _v(merged, "confpop_n_promoted_total") == _v(merged, "nocite_n_promoted_total"))
    assert (_v(merged, "confpop_n_entries_in_log_files") + _v(merged, "confpop_n_entries_in_no_log_files")
            == _v(merged, "confpop_n_total"))
    assert _v(merged, "confpop_n_entries_in_no_log_files") == _v(merged, "confpop_n_promoted_no_log")
    assert _v(merged, "confpop_n_files_with_log") == _v(merged, "corpus_sidecar_files")
    assert _v(merged, "confpop_n_files_without_log") == _v(merged, "corpus_nosidecar_files")


@invariant("promotion signal: the .bbl-only keys and the nocite-attributable keys match nocite_quantification; "
           "the bbl-only keys partition; promoted keys partition by signal")
def _inv_bbl_only_keys_and_promotion_signal(merged):
    assert _v(merged, "confpop_n_bbl_only_keys") == _v(merged, "nocite_bbl_only_total")
    assert _v(merged, "confpop_n_nocite_attributable_keys") == _v(merged, "nocite_bbl_only_attributable")
    assert _v(merged, "confpop_n_promoted_entries_nocite_attributable") == _v(merged, "nocite_n_promoted_attributable")
    assert (_v(merged, "confpop_n_bbl_only_keys_with_promoted_entry") + _v(merged, "confpop_n_bbl_only_keys_already_confirmed")
            + _v(merged, "confpop_n_bbl_only_keys_only_non_confirmed_entries")
            + _v(merged, "confpop_n_bbl_only_keys_no_entry_row")) == _v(merged, "confpop_n_bbl_only_keys")
    assert (_v(merged, "confpop_n_promoted_keys_bbl_only") + _v(merged, "confpop_n_promoted_keys_bbl_and_tex")
            + _v(merged, "confpop_n_promoted_keys_tex_scan_only")
            + _v(merged, "confpop_n_promoted_keys_no_usable_signal")) == _v(merged, "confpop_n_promoted_distinct_keys")
    assert _v(merged, "confpop_n_promoted_keys_bbl_only") == _v(merged, "confpop_n_bbl_only_keys_with_promoted_entry")
    assert _v(merged, "confpop_n_promoted_distinct_keys") <= _v(merged, "confpop_n_promoted_total")


@invariant("retraction tiers: the citations flagged by the recorded tier split into rate path + count path; the "
           "moderate-or-higher set of the recomputed tier splits the same way")
def _inv_retraction_tier_paths(merged):
    assert (_v(merged, "retraction_rate_based_n_citations") + _v(merged, "retraction_count_fallback_n_citations")
            == _v(merged, "retraction_originally_flagged_n_citations"))
    assert (_v(merged, "retraction_moderate_or_higher_rate_based_n") + _v(merged, "retraction_moderate_or_higher_count_based_n")
            == _v(merged, "retraction_moderate_or_higher_n"))
    # the count-based path is the same citations in both sets
    assert _v(merged, "retraction_moderate_or_higher_count_based_n") == _v(merged, "retraction_count_fallback_n_citations")
    assert _v(merged, "retraction_moderate_or_higher_n") <= _v(merged, "retraction_originally_flagged_n_citations")


@invariant("identity check population: with + without a citing ISSN = strict count before the identity check; the three winner "
           "classes add up to the citations with an ISSN; mismatches match the audit")
def _inv_identity_check_population(merged):
    assert _v(merged, "auditpop_n_original_strict") == _v(merged, "audit_original_strict_total")
    assert (_v(merged, "auditpop_n_with_citing_issn") + _v(merged, "auditpop_n_without_citing_issn")
            == _v(merged, "auditpop_n_original_strict"))
    classes = ("no_extract", "extract", "mixed")
    assert sum(_v(merged, f"auditpop_{c}_n_with_issn") for c in classes) == _v(merged, "auditpop_n_with_citing_issn")
    assert sum(_v(merged, f"auditpop_{c}_n_mismatch") for c in classes) == _v(merged, "auditpop_n_mismatch") == _v(merged, "audit_mismatch")
    assert sum(_v(merged, f"auditpop_{c}_n_undecidable") for c in classes) == _v(merged, "auditpop_n_structurally_undecidable")
    assert (_v(merged, "auditpop_n_mismatch") + _v(merged, "auditpop_n_structurally_undecidable")
            + _v(merged, "auditpop_n_confirmed_correct")) == _v(merged, "auditpop_n_with_citing_issn")
    for c in classes:
        assert (_v(merged, f"auditpop_{c}_n_mismatch") + _v(merged, f"auditpop_{c}_n_undecidable")
                + _v(merged, f"auditpop_{c}_n_confirmed_correct")) == _v(merged, f"auditpop_{c}_n_with_issn"), c


@invariant("second-vote survivors by discipline: the survivors split over the four groups; the group denominators and "
           "strict counts match the disciplinary table; removing the survivors lowers each group's count by the "
           "submissions that lose the flag")
def _inv_second_vote_survivor_groups(merged):
    groups = ("cs", "physics", "math", "other")
    assert sum(_v(merged, f"survivorgroups_{g}_n_citations") for g in groups) == _v(merged, "strict_n_second_vote_survivors")
    for g in groups:
        assert _v(merged, f"survivorgroups_{g}_n_submissions") == _v(merged, f"disc_{g}_n_submissions"), g
        assert _v(merged, f"survivorgroups_{g}_strict_n_with") == _v(merged, f"disc_{g}_strict_n"), g
        assert (_v(merged, f"survivorgroups_{g}_strict_n_with") - _v(merged, f"survivorgroups_{g}_strict_n_without")
                == _v(merged, f"survivorgroups_{g}_n_lose_strict_flag")), g
        assert _v(merged, f"survivorgroups_{g}_n_survivor_submissions") <= _v(merged, f"survivorgroups_{g}_n_citations"), g


# --- Re-evaluation sample ---

@invariant("re-evaluation sample: same files, joined entries and flips as the flip-rate export; "
           "flips among flagged entries cannot exceed the flagged entries")
def _inv_second_run_sample_flagged_flips(merged):
    assert _v(merged, "reevalsample_n_files") == _v(merged, "reprod_n_files")
    assert _v(merged, "reevalsample_n_joined") == _v(merged, "reprod_n_entries_joined")
    assert _v(merged, "reevalsample_n_flips") == _v(merged, "reprod_n_flips")
    flagged = _v(merged, "reevalsample_flagged_either_n")
    for k in ("flagged_flip_final_assessment", "flagged_flip_strict_status", "flagged_flip_broad_status", "flagged_flip_any_status"):
        assert 0 <= _v(merged, f"reevalsample_{k}_n") <= flagged, k
    assert _v(merged, "reevalsample_flagged_flip_any_status_n") >= _v(merged, "reevalsample_flagged_flip_final_assessment_n")
    assert _v(merged, "reevalsample_to_or_from_predatory_flips_n") <= _v(merged, "reevalsample_flagged_flip_final_assessment_n")
    assert flagged <= _v(merged, "reevalsample_n_joined")


@invariant("re-evaluation sample: strict and broad counts are consistent (union = sum - both; strict within broad; "
           "after the identity check within before; confirmed-cited within joined, resolved-eligible within confirmed-cited)")
def _inv_second_run_sample_counts(merged):
    for crit in ("strict", "broad"):
        main, reeval = _v(merged, f"reevalsample_{crit}_main_n"), _v(merged, f"reevalsample_{crit}_reeval_n")
        both, either = _v(merged, f"reevalsample_{crit}_both_n"), _v(merged, f"reevalsample_{crit}_either_n")
        assert either == main + reeval - both, crit
        assert both <= min(main, reeval), crit
    # a `predatory` verdict requires predatory-list evidence, so strict is within broad in each run
    assert _v(merged, "reevalsample_strict_main_n") <= _v(merged, "reevalsample_broad_main_n")
    assert _v(merged, "reevalsample_strict_reeval_n") <= _v(merged, "reevalsample_broad_reeval_n")
    assert _v(merged, "reevalsample_flagged_either_n") >= _v(merged, "reevalsample_broad_either_n")
    for crit in ("strict", "broad"):
        for run in ("main", "reeval", "both"):
            assert (_v(merged, f"reevalsample_conf_{crit}_{run}_corrected_n")
                    <= _v(merged, f"reevalsample_conf_{crit}_{run}_raw_n")
                    <= _v(merged, f"reevalsample_{crit}_{run}_n")), (crit, run)
    assert _v(merged, "reevalsample_n_resolved_eligible") <= _v(merged, "reevalsample_n_confirmed_cited") <= _v(merged, "reevalsample_n_joined")
    assert merged["reevalsample_confirmed_share_of_population"][0]["den"] == _v(merged, "corpus_confirmed_cited_entries")
    assert merged["reevalsample_resolved_share_of_population"][0]["den"] == _v(merged, "resolved_eligible_population_new")


@invariant("re-evaluation sampling frame: strict and broad totals equal the headline counts after the identity check; "
           "the part inside the size band cannot exceed the total")
def _inv_second_run_sampling_frame(merged):
    assert _v(merged, "reevalsample_frame_strict_n") == _v(merged, "strict_count")
    assert _v(merged, "reevalsample_frame_broad_n") == _v(merged, "broad_count")
    assert _v(merged, "reevalsample_frame_strict_in_band_n") <= _v(merged, "reevalsample_frame_strict_n")
    assert _v(merged, "reevalsample_frame_broad_in_band_n") <= _v(merged, "reevalsample_frame_broad_n")
    assert _v(merged, "reevalsample_frame_files_in_band") <= _v(merged, "reevalsample_frame_files_total") == _v(merged, "corpus_assessed_files")
    assert _v(merged, "reevalsample_conf_strict_main_corrected_n") <= _v(merged, "strict_count")


# --- Population without a citation log ---

@invariant("no-citation-log populations: (a) + (b) = (c) for entries, strict and broad; (a) equals the "
           "headline; the entry denominators match the corpus table")
def _inv_no_citation_log_population_sum(merged):
    for what in ("n_total", "strict_n", "broad_n"):
        a, b, c = (_v(merged, f"nosidecarcorr_{p}_{what}") for p in ("a", "b", "c"))
        assert a + b == c, f"{what}: {a} + {b} != {c}"
    assert _v(merged, "nosidecarcorr_a_n_total") == _v(merged, "corpus_confirmed_cited_entries")
    assert _v(merged, "nosidecarcorr_a_strict_n") == _v(merged, "strict_count")
    assert _v(merged, "nosidecarcorr_a_broad_n") == _v(merged, "broad_count")
    assert _v(merged, "nosidecarcorr_b_n_total") == _v(merged, "corpus_unverified_entries")


@invariant("no-citation-log populations: the unverified-isolated audit partitions (with + without a "
           "citing ISSN = strict entries; mismatch + undecidable + correct = with ISSN) and the counts "
           "after the identity check follow from the counts before it")
def _inv_no_citation_log_population_audit(merged):
    before = _v(merged, "nosidecarcorr_b_strict_raw_n")
    assert _v(merged, "nosidecarcorr_b_strict_with_issn_n") + _v(merged, "nosidecarcorr_b_strict_without_issn_n") == before
    assert (_v(merged, "nosidecarcorr_b_issn_mismatch_n") + _v(merged, "nosidecarcorr_b_structurally_undecidable_n")
            + _v(merged, "nosidecarcorr_b_confirmed_correct_n")) == _v(merged, "nosidecarcorr_b_strict_with_issn_n")
    assert (_v(merged, "nosidecarcorr_b_strict_n")
            == before - (_v(merged, "nosidecarcorr_b_issn_mismatch_n") - _v(merged, "nosidecarcorr_b_second_vote_survivor_n")))
    assert _v(merged, "nosidecarcorr_b_broad_n") == _v(merged, "nosidecarcorr_b_broad_raw_n") - _v(merged, "nosidecarcorr_b_broad_dropped_n")
    assert _v(merged, "nosidecarcorr_b_broad_dropped_n") <= _v(merged, "nosidecarcorr_b_issn_mismatch_n")
    for p in ("a", "b", "c"):
        for crit in ("strict", "broad"):
            r = merged[f"nosidecarcorr_{p}_{crit}_pct"][0]
            assert (r["num"], r["den"]) == (_v(merged, f"nosidecarcorr_{p}_{crit}_n"), _v(merged, f"nosidecarcorr_{p}_n_total")), (p, crit)


# --- UGC-CARE clone-pair votes ---

@invariant("UGC-CARE clone-pair votes: the groups partition the entries with a cloned vote; the "
           "same-group, other-group and no included-vote cases add up; the verdicts of the entries with both "
           "votes partition them")
def _inv_ugccare_clone_pair_partition(merged):
    cloned = _v(merged, "uccdv_n_with_cloned_vote")
    assert _v(merged, "uccdv_groupi_n_cloned") + _v(merged, "uccdv_groupii_n_cloned") - _v(merged, "uccdv_n_cloned_both_groups") == cloned
    assert (_v(merged, "uccdv_n_with_cloned_and_same_group_included")
            + _v(merged, "uccdv_n_with_cloned_and_other_group_only_included")
            + _v(merged, "uccdv_n_no_included_vote")) == cloned
    assert _v(merged, "uccdv_n_with_cloned_and_any_included") <= cloned
    both = _v(merged, "uccdv_n_with_cloned_and_same_group_included")
    assert sum(_v(merged, f"uccdv_both_votes_n_{v}") for v in ("predatory", "suspicious", "legitimate", "unknown", "other_verdict")) == both
    assert _v(merged, "uccdv_groupi_n_cloned_and_included_same_group") + _v(merged, "uccdv_groupii_n_cloned_and_included_same_group") >= both
    assert _v(merged, "uccdv_n_population") == _v(merged, "corpus_confirmed_cited_entries")


@invariant("UGC-CARE clone-pair votes: the strict and broad counts before equal the headline; after = before - lost + gained; "
           "entries with a cloned vote lie within the headline counts; the second-vote survivors all hold a cloned vote")
def _inv_ugccare_clone_pair_counts(merged):
    assert _v(merged, "uccdv_strict_before_n") == _v(merged, "strict_count")
    assert _v(merged, "uccdv_broad_before_n") == _v(merged, "broad_count")
    assert (_v(merged, "uccdv_strict_after_n")
            == _v(merged, "uccdv_strict_before_n") - _v(merged, "uccdv_n_strict_lost") + _v(merged, "uccdv_n_strict_gained"))
    assert _v(merged, "uccdv_n_strict_with_cloned") <= _v(merged, "strict_count")
    assert _v(merged, "uccdv_n_broad_with_cloned") <= _v(merged, "broad_count")
    assert _v(merged, "uccdv_n_strict_with_cloned_and_same_group_included") <= _v(merged, "uccdv_n_strict_with_cloned")
    assert _v(merged, "uccdv_n_survivors_with_cloned") == _v(merged, "strict_n_second_vote_survivors")
    assert _v(merged, "uccdv_n_strict_gained_audited_entries") <= _v(merged, "uccdv_n_strict_gained")
    assert _v(merged, "uccdv_n_verdict_changed") >= _v(merged, "uccdv_n_strict_lost") + _v(merged, "uccdv_n_strict_gained")
    # the strict count of the entries that hold both votes is the verdict table's predatory count
    assert _v(merged, "uccdv_n_strict_with_cloned_and_same_group_included") == _v(merged, "uccdv_both_votes_n_predatory")


# --- Promoted sibling entries, ISSN-L check, Scopus list ---

@invariant("sibling-inferred files: entries, files and strict/broad flags agree with the composition and corpus numbers; "
           "sibling files minus files checked = files without an excluded entry")
def _inv_sibling_promoted_entries(merged):
    assert _v(merged, "sisf_sib_n_promoted_entries") == _v(merged, "confpop_n_promoted_no_log_sibling")
    assert _v(merged, "sisf_sib_n_files_total") == _v(merged, "corpus_nosidecar_sibling_files_n")
    assert _v(merged, "sisf_sib_n_files_total") - _v(merged, "siblingexhaustive_n_files_checked") == _v(merged, "sisf_sib_n_files_without_excluded_entry")
    assert _v(merged, "sisf_sib_n_promoted_submissions") <= _v(merged, "sisf_sib_n_promoted_files") <= _v(merged, "sisf_sib_n_files_total")
    assert _v(merged, "sisf_sib_n_promoted_files") <= _v(merged, "sisf_sib_n_promoted_entries")
    assert _v(merged, "sisf_sib_n_promoted_strict") <= _v(merged, "sisf_sib_n_promoted_broad") <= _v(merged, "sisf_sib_n_promoted_entries")
    assert _v(merged, "sisf_sib_n_promoted_entries") + _v(merged, "confpop_n_promoted_no_log_isolated") == _v(merged, "confpop_n_promoted_no_log")


@invariant("ISSN-L check and cache state: the two kinds of invalid vote add up to the mismatches; the pair resolutions add up to the pairs; "
           "strict and broad counts with the references included again and with the cache-only references; the largest movement of the strict count")
def _inv_issnl_and_cache_state(merged):
    assert _v(merged, "sisf_issnl_n_refs_cache_issn_differs") + _v(merged, "sisf_issnl_n_refs_null_cache_only") == _v(merged, "sisf_issnl_n_mismatch_refs")
    assert _v(merged, "sisf_issnl_n_mismatch_refs") == _v(merged, "audit_mismatch")
    assert (_v(merged, "sisf_issnl_n_pairs_same_family") + _v(merged, "sisf_issnl_n_pairs_distinct_family")
            + _v(merged, "sisf_issnl_n_pairs_unresolved")) == _v(merged, "sisf_issnl_n_pairs")
    assert _v(merged, "sisf_strict_base_n") == _v(merged, "strict_count")
    assert _v(merged, "sisf_broad_base_n") == _v(merged, "broad_count")
    for crit in ("strict", "broad"):
        base = _v(merged, f"sisf_{crit}_base_n")
        assert _v(merged, f"sisf_issnl_{crit}_with_restored_n") == base + _v(merged, "sisf_issnl_n_restored")
        assert _v(merged, f"sisf_issnl_{crit}_without_later_cache_only_n") == base + _v(merged, "sisf_issnl_n_later_cache_only")
        assert _v(merged, f"sisf_issnl_{crit}_with_both_n") == base + _v(merged, "sisf_issnl_n_restored") + _v(merged, "sisf_issnl_n_later_cache_only")
    assert _v(merged, "sisf_issnl_strict_max_movement_n") == _v(merged, "sisf_issnl_n_restored") + _v(merged, "sisf_issnl_n_later_cache_only")
    r = merged["sisf_issnl_strict_max_movement_pct"][0]
    assert (r["num"], r["den"]) == (_v(merged, "sisf_issnl_strict_max_movement_n"), _v(merged, "sisf_strict_base_n"))
    assert _v(merged, "sisf_issnl_n_restored") + _v(merged, "sisf_issnl_n_later_cache_only") <= _v(merged, "audit_mismatch") - _v(merged, "strict_n_second_vote_survivors")


@invariant("Scopus workbook: loaded as active + inactive excluded = rows; no quality-flagged "
           "row is loaded; the loaded titles equal the cache's own load record")
def _inv_scopus_workbook_counts(merged):
    assert _v(merged, "sisf_scopus_n_active") + _v(merged, "sisf_scopus_n_inactive") == _v(merged, "sisf_scopus_n_rows")
    assert _v(merged, "sisf_scopus_n_active") == _v(merged, "sisf_scopus_n_loaded_in_cache_record")
    assert _v(merged, "sisf_scopus_n_quality_flagged_loaded") <= _v(merged, "sisf_scopus_n_quality_flagged_rows") <= _v(merged, "sisf_scopus_n_inactive") + _v(merged, "sisf_scopus_n_quality_flagged_loaded")


# --------------------------------------------------------------------------


def check_invariants(merged: dict) -> list[tuple[str, str]]:
    """Runs every invariant; returns (name, missing key) of those skipped because a numbers key they need is absent."""
    skipped = []
    for name, fn in INVARIANTS:
        try:
            fn(merged)
        except KeyError as e:
            skipped.append((name, str(e.args[0])))
        except AssertionError as e:
            raise SystemExit(f"INVARIANT FAILED: {name}: {e}")
    return skipped


def main() -> int:
    merged = load_all()
    skipped = check_invariants(merged)

    macro_owners: dict[str, str] = {}
    for key in sorted(merged):
        macro = snake_to_camel(key)
        if macro in macro_owners:
            raise SystemExit(
                f"MACRO-NAME COLLISION: keys '{macro_owners[macro]}' and '{key}' "
                f"both produce \\{macro} after digit-stripping, so the second \\newcommand "
                f"would overwrite the first."
            )
        macro_owners[macro] = key

    lines = [
        "% Generated by make_numbers_tex.py -- DO NOT EDIT BY HAND.",
        "% Re-running against the unchanged numbers/*.json inputs must",
        "% reproduce this file byte-for-byte.",
        "",
    ]
    for key in sorted(merged):
        entry, _source, _file = merged[key]
        macro = snake_to_camel(key)
        value = render_value(key, entry)
        lines.append(f"\\newcommand{{\\{macro}}}{{{value}}}")
    lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines))
    print(f"wrote {len(merged)} macros to {OUTPUT_PATH}")
    for name, missing_key in skipped:
        print(f"SKIPPED {name}")
        print(f"  (numbers key {missing_key} is missing)")
    print(f"invariants: {len(INVARIANTS) - len(skipped)} checked, {len(skipped)} skipped"
          + (": " + "; ".join(name for name, _key in skipped) if skipped else ""))
    print(f"{len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
