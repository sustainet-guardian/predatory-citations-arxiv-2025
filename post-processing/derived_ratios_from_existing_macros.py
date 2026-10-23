#!/usr/bin/env python3
"""Derives ratios, sums, multiples and sample sizes from keys that already exist in the other numbers files, so the printed
figures follow from them. Reads numbers/*.numbers.json through make_numbers_tex.py's loader; writes
numbers/derived_ratios_from_existing_macros.numbers.json (keys `derived_*`). Denominators: derived_audit_mismatch_pct_of_original and
derived_correction_size_pct use the strict-predatory citations before the identity check (audit_original_strict_total: the rows of
entries_confirmed with final_assessment = 'predatory' that full_corpus_identity_mismatch_audit.py audits); derived_legit_*_multiple
divide the legitimate count of the verdict recomputed without the invalid votes (headline_legitimate_n) by strict_count (strict after
the identity check); derived_doi_gap_points subtracts doi_strict_predatory_pct (strict after the identity check) from
doi_legitimate_pct (stored verdict). The coverage shares divide the covered submissions of a discipline by all arXiv submissions of that
discipline (covered plus excluded); the validation-asymmetry counts are 10% of strict_count and 0.001% to 1% of the legitimate plus
unknown count, and their multiples divide by the 10% count; the leave-one-out multiples divide the strict count after leaving out one
backend by strict_count; the venue-type multiple divides the journal broad rate by the conference broad rate.
"""
from __future__ import annotations

import make_numbers_tex as mnt
from stats_common import write_numbers


def main() -> None:
    merged = mnt.load_all()

    def value(key: str) -> int:
        entry = merged[key][0]
        assert entry["kind"] == "int", f"{key} is kind={entry['kind']!r}, not int"
        return entry["value"]

    def ratio_parts(key: str) -> tuple[int, int]:
        """The (num, den) behind a ratio-kind key, so a complement (den - num) is computed without rounding twice."""
        entry = merged[key][0]
        assert entry["kind"] == "ratio", f"{key} is kind={entry['kind']!r}, not ratio"
        return entry["num"], entry["den"]

    out: dict[str, dict] = {}

    # Shares of the headline total by corpus state, at one decimal.
    out["derived_assessed_pct_display"] = {
        "kind": "ratio", "num": value("corpusstate_assessed_n"), "den": value("headline_total"), "decimals": 1,
    }
    out["derived_preprint_pct_display"] = {
        "kind": "ratio", "num": value("corpusstate_preprint_n"), "den": value("headline_total"), "decimals": 1,
    }
    out["derived_unsupported_type_pct_display"] = {
        "kind": "ratio", "num": value("corpusstate_unsupported_type_n"), "den": value("headline_total"), "decimals": 1,
    }
    out["derived_no_venue_pct_display"] = {
        "kind": "ratio", "num": value("corpusstate_no_venue_n"), "den": value("headline_total"), "decimals": 1,
    }

    # Share of the headline total covered by the six assessment rows above the total line (strict is part of the rows; the
    # hijacked flag overlaps with them and is not a row).
    headline_rows = sum(
        value(key) for key in (
            "headline_legitimate_n", "headline_not_applicable_n", "headline_unknown_n", "headline_suspicious_n",
            "strict_count", "headline_insufficient_data_n",
        )
    )
    out["derived_headline_rows_sum_pct"] = {
        "kind": "ratio", "num": headline_rows, "den": value("headline_total"), "decimals": 1,
    }

    # Share of arXiv's 2025 submissions of a discipline that the corpus covers: covered / (covered + excluded).
    for discipline in ("math", "physics", "cs"):
        covered_n, _covered_den = ratio_parts(f"disccompincl_{discipline}_pct")
        excluded_n, _excluded_den = ratio_parts(f"disccompexcl_{discipline}_pct")
        out[f"derived_coverage_{discipline}_pct"] = {
            "kind": "ratio", "num": covered_n, "den": covered_n + excluded_n, "decimals": 1,
        }

    # Share of assessed files without a citation log, at two decimals.
    out["derived_nosidecar_pct_display"] = {
        "kind": "ratio", "num": value("corpus_nosidecar_files"), "den": value("corpus_assessed_files"), "decimals": 2,
    }

    # Unverified-entries share of the candidate population.
    out["derived_unverified_pct_of_candidate"] = {
        "kind": "ratio", "num": value("corpus_unverified_entries"), "den": value("corpus_candidate_population"), "decimals": 2,
    }

    # Excluded and confirmed-cited shares of the candidate population.
    out["derived_excluded_pct_of_candidate_only"] = {
        "kind": "ratio", "num": value("corpus_excluded_entries"), "den": value("corpus_candidate_population"), "decimals": 2,
    }
    out["derived_confirmed_cited_pct_of_candidate"] = {
        "kind": "ratio", "num": value("corpus_confirmed_cited_entries"), "den": value("corpus_candidate_population"), "decimals": 2,
    }

    # Share of large files (at least 1,000 entries) among all files of the pairwise-agreement comparison.
    pairwise_total = value("pairwise_n_bloated_files") + value("pairwise_n_normal_files")
    out["derived_bloated_pct_of_pairwise_total"] = {
        "kind": "ratio", "num": value("pairwise_n_bloated_files"), "den": pairwise_total, "decimals": 1,
    }

    # Share of submissions with any documented-concern flag.
    out["derived_concern_pct_of_papers"] = {
        "kind": "ratio", "num": value("concern_n_any_flag"), "den": value("concern_n_papers"), "decimals": 2,
    }

    # Entries excluded or unverified outright: their sum and its share of the candidate population.
    excluded_plus_unverified = value("corpus_excluded_entries") + value("corpus_unverified_entries")
    out["derived_excluded_plus_unverified_n"] = {"kind": "int", "value": excluded_plus_unverified}
    out["derived_excluded_pct_of_candidate"] = {
        "kind": "ratio", "num": excluded_plus_unverified, "den": value("corpus_candidate_population"), "decimals": 1,
    }

    # Combined non-bibliography (macro-only + empty) share of all .bib files.
    out["derived_nonbibliography_pct_of_bib_files"] = {
        "kind": "ratio", "num": value("corpus_nonbibliography_files"), "den": value("corpus_total_bib_files"), "decimals": 2,
    }

    # Shares of the three .bib file types among all .bib files.
    out["derived_assessed_pct_of_bib_files"] = {
        "kind": "ratio", "num": value("corpus_assessed_files"), "den": value("corpus_total_bib_files"), "decimals": 2,
    }
    out["derived_macroonly_pct_of_bib_files"] = {
        "kind": "ratio", "num": value("corpus_macroonly_files"), "den": value("corpus_total_bib_files"), "decimals": 2,
    }
    out["derived_empty_pct_of_bib_files"] = {
        "kind": "ratio", "num": value("corpus_empty_files"), "den": value("corpus_total_bib_files"), "decimals": 2,
    }

    # Integer multiples of the strict count (stored as "int", rounded here, because the "ratio" kind renders a percentage and
    # "float" has no thousands separator).
    legit_n = value("headline_legitimate_n")
    unknown_n = value("headline_unknown_n")
    strict_n = value("strict_count")
    out["derived_legit_plus_unknown_strict_multiple"] = {
        "kind": "int", "value": round((legit_n + unknown_n) / strict_n),
    }
    out["derived_unknown_strict_multiple"] = {"kind": "int", "value": round(unknown_n / strict_n)}
    out["derived_legit_strict_multiple"] = {"kind": "int", "value": round(legit_n / strict_n)}

    # Distinct venues of legitimate and unknown references combined.
    out["derived_legit_plus_unknown_distinct_venues"] = {
        "kind": "int", "value": value("conc_legit_distinct_venues") + value("unknownvenues_n_distinct"),
    }

    # 10% of the strict count and 0.01% of the legitimate + unknown count, each rounded to an integer.
    out["derived_validation_fp_ten_pct_n"] = {"kind": "int", "value": round(strict_n * 0.10)}
    out["derived_validation_fn_hundredth_pct_n"] = {"kind": "int", "value": round((legit_n + unknown_n) * 0.0001)}

    # The other false-negative rows of the validation-asymmetry table (0.001%, 0.1%, 1% of the legitimate + unknown count) and
    # each row's multiple of the 10% false-positive count, at one decimal.
    fp_ten_pct_n = out["derived_validation_fp_ten_pct_n"]["value"]
    out["derived_validation_fn_thousandth_pct_n"] = {"kind": "int", "value": round((legit_n + unknown_n) / 100_000)}
    out["derived_validation_fn_tenth_pct_n"] = {"kind": "int", "value": round((legit_n + unknown_n) / 1_000)}
    out["derived_validation_fn_one_pct_n"] = {"kind": "int", "value": round((legit_n + unknown_n) / 100)}
    out["derived_validation_fp_ten_pct_multiple"] = {"kind": "float", "value": fp_ten_pct_n / fp_ten_pct_n, "decimals": 1}
    for label in ("thousandth", "hundredth", "tenth", "one"):
        out[f"derived_validation_fn_{label}_pct_multiple"] = {
            "kind": "float", "value": out[f"derived_validation_fn_{label}_pct_n"]["value"] / fp_ten_pct_n, "decimals": 1,
        }

    # Identity mismatches that lose the strict verdict: mismatches minus second-vote survivors.
    out["derived_audit_mismatch_minus_survivors"] = {
        "kind": "int", "value": value("audit_mismatch") - value("strict_n_second_vote_survivors"),
    }

    # Identity-mismatch rate among the strict references before the identity check.
    out["derived_audit_mismatch_pct_of_original"] = {
        "kind": "ratio", "num": value("audit_mismatch"), "den": value("audit_original_strict_total"), "decimals": 1,
    }

    # Size of the identity correction: strict references removed, relative to the strict references before it.
    out["derived_correction_size_pct"] = {
        "kind": "ratio",
        "num": value("audit_original_strict_total") - value("strict_count"),
        "den": value("audit_original_strict_total"),
        "decimals": 1,
    }

    # Share of the list's flags that disappear when the list is left out: the complement of the share that remains, from the
    # remaining share's own num/den.
    algerian_retained_num, algerian_retained_den = ratio_parts("reliability_algerian_ministry_leave_focal_out_pct")
    out["derived_algerian_ministry_not_retained_pct"] = {
        "kind": "ratio",
        "num": algerian_retained_den - algerian_retained_num,
        "den": algerian_retained_den,
        "decimals": 1,
    }

    # Strict and broad totals with each single source added, and with all three combined: the count after the identity check plus the delta.
    out["derived_addone_rw_strict_total"] = {"kind": "int", "value": value("strict_count") + value("addone_rw_strict_delta")}
    out["derived_addone_rw_broad_total"] = {"kind": "int", "value": value("broad_count") + value("addone_rw_broad_delta")}
    out["derived_addone_doaj_strict_total"] = {"kind": "int", "value": value("strict_count") + value("addone_doaj_strict_delta")}
    out["derived_addone_doaj_broad_total"] = {"kind": "int", "value": value("broad_count") + value("addone_doaj_broad_delta")}
    out["derived_addone_combined_strict_total"] = {"kind": "int", "value": value("strict_count") + value("addone_combined_strict_delta")}
    out["derived_addone_combined_broad_total"] = {"kind": "int", "value": value("broad_count") + value("addone_combined_broad_delta")}

    # Submissions of the covered set that have no confirmed-cited entry.
    out["derived_submissions_without_confirmed_cited_n"] = {
        "kind": "int", "value": value("corpus_covered_submissions") - value("concern_n_papers"),
    }

    # Strict references that lose their verdict when the Algerian list is left out.
    out["derived_algerian_exclusion_strict_loss_n"] = {
        "kind": "int",
        "value": value("strict_count") - value("leave_one_out_algerian_ministry_strict_after_exclusion"),
    }

    # Strict count after leaving out the Algerian list (largest decrease) and Scopus (largest increase), as multiples of strict_count.
    out["derived_algerian_exclusion_strict_multiple"] = {
        "kind": "float",
        "value": value("leave_one_out_algerian_ministry_strict_after_exclusion") / value("strict_count"),
        "decimals": 2,
    }
    out["derived_scopus_exclusion_strict_multiple"] = {
        "kind": "float",
        "value": value("leave_one_out_scopus_strict_after_exclusion") / value("strict_count"),
        "decimals": 2,
    }

    # Mathematics' submission-level broad share as a multiple of the corpus-wide submission-level share.
    math_broad_n, math_broad_d = ratio_parts("disc_math_broad_pct")
    corpus_broad_n, corpus_broad_d = ratio_parts("submission_broad_pct")
    out["derived_math_submission_broad_multiple"] = {
        "kind": "float", "value": (math_broad_n / math_broad_d) / (corpus_broad_n / corpus_broad_d), "decimals": 1,
    }

    # Broad rate of journal-type cited references as a multiple of the conference-type rate (num/den of both rates).
    journal_broad_n, journal_broad_d = ratio_parts("venuetype_journal_broad_pct")
    conference_broad_n, conference_broad_d = ratio_parts("venuetype_conference_broad_pct")
    out["derived_venuetype_journal_conference_broad_multiple"] = {
        "kind": "float",
        "value": (journal_broad_n / journal_broad_d) / (conference_broad_n / conference_broad_d),
        "decimals": 1,
    }

    # Largest share by which the strict count can move through the ISSN-L effects (num/den as stored), at one decimal.
    movement_n, movement_d = ratio_parts("sisf_issnl_strict_max_movement_pct")
    out["derived_sisf_issnl_strict_max_movement_pct"] = {"kind": "ratio", "num": movement_n, "den": movement_d, "decimals": 1}

    # How many times larger the legitimate+unknown venue space is than the strict one.
    out["derived_legit_unknown_venues_per_strict_venue"] = {
        "kind": "int",
        "value": round(out["derived_legit_plus_unknown_distinct_venues"]["value"] / value("conc_strict_distinct_venues")),
    }

    # How many times more distinct venues the legitimate side has than the strict one.
    out["derived_legit_venues_per_strict_venue"] = {
        "kind": "int", "value": round(value("conc_legit_distinct_venues") / value("conc_strict_distinct_venues")),
    }

    # Reciprocal of the legitimate HHI as displayed at three decimals (the effective-venues key divides by the unrounded HHI).
    out["derived_legit_hhi_displayed_reciprocal"] = {
        "kind": "int", "value": round(1 / round(merged["conc_legit_hhi"][0]["value"], merged["conc_legit_hhi"][0]["decimals"])),
    }

    # DOI-presence gap in percentage points, from the num/den of both rates.
    doi_legit_n, doi_legit_d = ratio_parts("doi_legitimate_pct")
    doi_strict_n, doi_strict_d = ratio_parts("doi_strict_predatory_pct")
    out["derived_doi_gap_points"] = {
        "kind": "float", "value": 100.0 * doi_legit_n / doi_legit_d - 100.0 * doi_strict_n / doi_strict_d, "decimals": 1,
    }

    # Share of assessed files with a citation log.
    out["derived_sidecar_pct_of_assessed_files"] = {
        "kind": "ratio", "num": value("corpus_sidecar_files"), "den": value("corpus_assessed_files"), "decimals": 2,
    }

    # Journal and conference references in millions.
    out["derived_venuetype_journal_millions"] = {"kind": "float", "value": value("venuetype_journal_n") / 1e6, "decimals": 2}
    out["derived_venuetype_conference_millions"] = {"kind": "float", "value": value("venuetype_conference_n") / 1e6, "decimals": 2}

    # Vote-threshold alternatives among the resolved-eligible citations, as multiples of the strict count after the identity
    # check (see strict_vote_rule_alternatives.py).
    strict_after_check = value("strict_count")
    for label in ("ge2", "votes_ge_legit"):
        out[f"derived_strictvote_{label}_resolved_corrected_multiple"] = {
            "kind": "float", "value": value(f"strictvote_{label}_resolved_corrected_n") / strict_after_check, "decimals": 2,
        }

    # Sample sizes from the strict rate p = strict_count / resolved-eligible population, rounded to two significant figures:
    # 3/p rounded to tens (observations for an expected strict count of three) and 25/p rounded to thousands (observations
    # for a 20% relative standard error of the rate).
    rate_n, rate_d = ratio_parts("primary_strict_rate_pct")
    p_exact = rate_n / rate_d
    p_printed = float(f"{p_exact:.2g}")
    out["derived_primary_strict_rate_printed"] = {"kind": "float", "value": p_printed, "decimals": 5}
    out["derived_rule_of_three_at_printed_rate_n"] = {"kind": "int", "value": int(round(3 / p_printed, -1))}
    out["derived_precision_sample_at_printed_rate_n"] = {"kind": "int", "value": int(round(25 / p_printed, -3))}

    write_numbers("derived_ratios_from_existing_macros", out)


if __name__ == "__main__":
    main()
