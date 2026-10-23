# Rebuilds numbers/*.numbers.json and numbers.tex from the databases and result files that your own run provides. Run it inside
# post-processing/ (the rules use relative paths). The databases and result files themselves are built before (README.md,
# "Running order"); this Makefile does not build them.
#
# What triggers a rebuild: a numbers file is rebuilt when its script, a helper module (HELPER_MODULES below) or a file named in its
# rule is newer. The databases that the analyses read through the helper modules (EVAL_DB_PATH_BBL_TEX, EVAL_CACHE_DB_PATH) and
# the directories EVAL_RESULTS_ROOT and EVAL_SECOND_RUN_ROOT are not tracked (the modification time of a directory does not change
# when a file inside it changes): after rebuilding one of them, run `make -B`. Use GNU make 4.3 or later (grouped targets).
#
# The Makefile sets no defaults for the EVAL_* variables: export them first (README.md lists them).

PYTHON ?= python3

# Variables that every build needs; make stops before it builds anything if one is unset. A rule that needs a further variable
# checks it itself (NEED_VARS).
REQUIRED_VARS := EVAL_OUTPUT_DIR EVAL_DB_PATH EVAL_DB_PATH_BBL_TEX EVAL_RESULTS_ROOT EVAL_CACHE_DB_PATH \
	EVAL_SNAPSHOT_CATEGORIES_DB EVAL_CATEGORIES_CSV_PATH EVAL_BBL_JSONL_PATH EVAL_BIB_ROOT \
	EVAL_SECOND_RUN_ROOT EVAL_DOAJ_CURRENT_LISTING_CSV
$(foreach v,$(REQUIRED_VARS),$(if $($(v)),,$(error $(v) is not set)))

# $(call NEED_VARS,A B): stops the recipe when one of the variables is not exported.
NEED_VARS = @for v in $(1); do [ -n "$$(printenv $$v)" ] || { echo "$$v is not set"; exit 1; }; done

# Modules that the numbers scripts import; a change of one rebuilds every file that is made with them.
HELPER_MODULES := stats_common.py identity_vote_rules.py decision_rule.py retraction_tiers.py leave_one_out_helpers.py full_corpus_identity_mismatch_audit.py \
	identity_check_headline_analyses.py identity_check_table_analyses.py disciplinary_classification.py load_assessment_records.py \
	leave_one_out_backend_sweep.py result_line.py analyze_hijacked_doaj_benchmark.py analyze_identity_and_negative_control.py \
	watchlist_split_and_math_broad_rate.py stats_retraction_risk_basis.py build_entry_table.py

# =========================================================================
# Files that other scripts read (intermediate files). Those that list individual citations are written only with the flag
# --write-citation-keys (or --write-venue-table) and are not released.
# =========================================================================

AUDIT_JSON      := $(EVAL_OUTPUT_DIR)/output_identity_mismatch_audit/full_corpus_identity_mismatch_audit.json
AUDIT_KEYS_JSON := $(EVAL_OUTPUT_DIR)/output_identity_mismatch_audit/full_corpus_identity_mismatch_audit_keys.json
CITATION_DETAIL_JSON := $(EVAL_OUTPUT_DIR)/identity_audit_citation_detail.json
HEADLINE_JSON   := $(EVAL_OUTPUT_DIR)/output_identity_check_headline_analyses/identity_check_headline_analyses.json
RETRACTION_BASIS_CSV := $(EVAL_OUTPUT_DIR)/retraction_risk_basis_all_entries.csv
CITATION_LOG_SHARE_JSON := $(EVAL_OUTPUT_DIR)/cited_entry_counts.json
DISC_SUBCOUNT_JSON := $(EVAL_OUTPUT_DIR)/output_disciplinary_submission_counts/disciplinary_submission_counts.json
ISSN_L_JSON := $(EVAL_OUTPUT_DIR)/output_issn_l_family_check/issn_l_family_check.json
MISSED_KEYS_ROWS_JSON := $(EVAL_OUTPUT_DIR)/output_rates_with_missed_keys_rows/rates_with_missed_keys_rows.json
# the ACL Anthology venue list, built by analyze_bulk_database_assessment.py from the assessment of the corpus
ACL_VENUES_CSV := $(EVAL_OUTPUT_DIR)/bulk_database_venues_acl.csv
BENCH := $(EVAL_OUTPUT_DIR)/hijacked_doaj_benchmark
MASS := $(BENCH)/mass_eval_output

# Benchmark inputs from public lists (retrieved 2026-07-29), stored in $(BENCH)/raw_data/:
#   retraction_watch_hijacked_checker.csv    the Retraction Watch Hijacked Journal Checker (the sheet that Retraction Watch's page
#                                            on the checker links to)
#   doaj_withdrawn_until_2024_01.csv         DOAJ's change log of withdrawn journals, 19 March 2014 to 31 January 2024
#   doaj_withdrawn_from_2024_02.csv          DOAJ's change log of withdrawn journals since 1 February 2024
#   doaj_full_journal_list.csv               DOAJ's public journal list (CSV), https://doaj.org/csv
# build_benchmark_bib_files.py selects from the two change logs the rows whose reason contains "Journal not adhering to Best
# practice" (case-insensitive) and removes duplicates on (title, ISSN, date, reason).

# Grouped targets: one run of the script makes both files.
$(AUDIT_JSON) $(AUDIT_KEYS_JSON) &: full_corpus_identity_mismatch_audit.py
	$(PYTHON) full_corpus_identity_mismatch_audit.py --write-citation-keys

$(CITATION_DETAIL_JSON) $(HEADLINE_JSON) &: identity_check_headline_analyses.py $(AUDIT_KEYS_JSON) $(RETRACTION_BASIS_CSV)
	$(PYTHON) identity_check_headline_analyses.py --write-citation-keys

$(EVAL_CATEGORIES_CSV_PATH): extract_arxiv_categories_from_local_snapshot.py
	$(call NEED_VARS,EVAL_ARXIV_SNAPSHOT_JSON)
	$(PYTHON) extract_arxiv_categories_from_local_snapshot.py

$(EVAL_SNAPSHOT_CATEGORIES_DB): build_snapshot_categories_db.py $(EVAL_CATEGORIES_CSV_PATH)
	$(PYTHON) build_snapshot_categories_db.py

$(RETRACTION_BASIS_CSV): stats_retraction_risk_basis.py $(EVAL_DB_PATH_BBL_TEX)
	$(PYTHON) stats_retraction_risk_basis.py --write-venue-table

$(CITATION_LOG_SHARE_JSON): cited_entry_counts.py $(EVAL_DB_PATH) $(EVAL_BBL_JSONL_PATH)
	$(PYTHON) cited_entry_counts.py

$(DISC_SUBCOUNT_JSON): disciplinary_submission_counts.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) disciplinary_submission_counts.py

$(ISSN_L_JSON): issn_l_family_check.py $(AUDIT_KEYS_JSON)
	$(call NEED_VARS,EVAL_CONTACT_EMAIL)
	$(PYTHON) issn_l_family_check.py --write-citation-keys

$(MISSED_KEYS_ROWS_JSON): rates_with_missed_keys_rows.py $(ISSN_L_JSON) numbers/strict_predatory_partition_tables.numbers.json numbers/missed_keys_flag_counts.numbers.json
	$(call NEED_VARS,EVAL_DB_PATH_MISSED_KEYS_ROWS)
	$(PYTHON) rates_with_missed_keys_rows.py

$(ACL_VENUES_CSV) numbers/analyze_bulk_database_assessment.numbers.json &: analyze_bulk_database_assessment.py $(EVAL_DB_PATH)
	$(PYTHON) analyze_bulk_database_assessment.py

# =========================================================================
# One rule per script that writes a numbers file (numbers/<script>.numbers.json).
# =========================================================================

numbers/add_one_new_sources_impact.numbers.json: add_one_new_sources_impact.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON) $(ACL_VENUES_CSV)
	EVAL_ACL_VENUES_CSV=$(ACL_VENUES_CSV) $(PYTHON) add_one_new_sources_impact.py

numbers/analyze_excluded_submissions.numbers.json: analyze_excluded_submissions.py $(EVAL_DB_PATH_BBL_TEX) $(EVAL_SNAPSHOT_CATEGORIES_DB) $(EVAL_BBL_JSONL_PATH)
	$(PYTHON) analyze_excluded_submissions.py

numbers/analyze_hijacked_doaj_benchmark.numbers.json: analyze_hijacked_doaj_benchmark.py $(MASS)/retraction_watch__retraction_watch.jsonl $(MASS)/doaj_best_practice__doaj_best_practice.jsonl $(MASS)/negative_control__negative_control.jsonl
	$(PYTHON) analyze_hijacked_doaj_benchmark.py

numbers/any_documented_concern_decomposition.numbers.json: any_documented_concern_decomposition.py $(CITATION_DETAIL_JSON) $(RETRACTION_BASIS_CSV) $(AUDIT_KEYS_JSON)
	$(PYTHON) any_documented_concern_decomposition.py

numbers/bibliometric_pairwise_agreement.numbers.json: bibliometric_pairwise_agreement.py $(EVAL_DB_PATH)
	$(PYTHON) bibliometric_pairwise_agreement.py

numbers/build_retraction_watch_clone_identity_bib.numbers.json: build_retraction_watch_clone_identity_bib.py $(BENCH)/raw_data/retraction_watch_hijacked_checker.csv
	$(PYTHON) build_retraction_watch_clone_identity_bib.py --write-citation-keys

numbers/citation_level_disciplinary_rates_by_group.numbers.json: citation_level_disciplinary_rates_by_group.py $(EVAL_SNAPSHOT_CATEGORIES_DB) $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) citation_level_disciplinary_rates_by_group.py

numbers/confirmed_cited_composition.numbers.json: confirmed_cited_composition.py $(EVAL_BBL_JSONL_PATH)
	$(PYTHON) confirmed_cited_composition.py

numbers/corpus_share_ratios.numbers.json: corpus_share_ratios.py numbers/disciplinary_submission_counts.numbers.json numbers/any_documented_concern_decomposition.numbers.json
	$(PYTHON) corpus_share_ratios.py

numbers/corpus_size_partition.numbers.json: corpus_size_partition.py $(EVAL_DB_PATH_BBL_TEX) $(CITATION_LOG_SHARE_JSON)
	$(PYTHON) corpus_size_partition.py

numbers/acronym_table_variant_counts.numbers.json: acronym_table_variant_counts.py
	$(call NEED_VARS,EVAL_CACHE_DB_PATH)
	$(PYTHON) acronym_table_variant_counts.py

numbers/core_name_legitimate_issn_check.numbers.json: core_name_legitimate_issn_check.py $(EVAL_DB_PATH_BBL_TEX)
	$(PYTHON) core_name_legitimate_issn_check.py

numbers/disciplinary_composition_included_vs_excluded.numbers.json: disciplinary_composition_included_vs_excluded.py $(EVAL_SNAPSHOT_CATEGORIES_DB)
	$(PYTHON) disciplinary_composition_included_vs_excluded.py

# written together with $(DISC_SUBCOUNT_JSON) by disciplinary_submission_counts.py
numbers/disciplinary_submission_counts.numbers.json: $(DISC_SUBCOUNT_JSON)
	@true

numbers/disciplinary_table_and_venue_concentration.numbers.json: disciplinary_table_and_venue_concentration.py $(DISC_SUBCOUNT_JSON) $(AUDIT_KEYS_JSON) $(EVAL_SNAPSHOT_CATEGORIES_DB)
	$(PYTHON) disciplinary_table_and_venue_concentration.py

numbers/doaj_current_reinclusion_check.numbers.json: doaj_current_reinclusion_check.py $(MASS)/doaj_best_practice__doaj_best_practice.jsonl $(EVAL_DOAJ_CURRENT_LISTING_CSV)
	$(PYTHON) doaj_current_reinclusion_check.py

numbers/doaj_withdrawn_legitimate_crosstab.numbers.json: doaj_withdrawn_legitimate_crosstab.py $(MASS)/doaj_best_practice__doaj_best_practice.jsonl $(EVAL_DOAJ_CURRENT_LISTING_CSV)
	$(PYTHON) doaj_withdrawn_legitimate_crosstab.py

numbers/headline_distribution_and_vote_rules.numbers.json: headline_distribution_and_vote_rules.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) headline_distribution_and_vote_rules.py

numbers/identity_audit_population_split.numbers.json: identity_audit_population_split.py $(AUDIT_KEYS_JSON)
	$(PYTHON) identity_audit_population_split.py

numbers/identity_check_table_analyses.numbers.json: identity_check_table_analyses.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) identity_check_table_analyses.py

numbers/issn_presence_by_population.numbers.json: issn_presence_by_population.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) issn_presence_by_population.py

numbers/jsc_citation_breakdown.numbers.json: jsc_citation_breakdown.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON) numbers/headline_distribution_and_vote_rules.numbers.json
	$(PYTHON) jsc_citation_breakdown.py

numbers/large_file_families.numbers.json: large_file_families.py $(EVAL_DB_PATH)
	$(PYTHON) large_file_families.py

numbers/leave_one_out_backend_sweep.numbers.json: leave_one_out_backend_sweep.py $(AUDIT_KEYS_JSON) $(CITATION_DETAIL_JSON)
	$(PYTHON) leave_one_out_backend_sweep.py

numbers/leave_one_out_flip_rates.numbers.json: leave_one_out_flip_rates.py $(EVAL_DB_PATH_BBL_TEX)
	$(PYTHON) leave_one_out_flip_rates.py

numbers/leave_one_out_independent_citations.numbers.json: leave_one_out_independent_citations.py $(AUDIT_KEYS_JSON) $(CITATION_DETAIL_JSON)
	$(PYTHON) leave_one_out_independent_citations.py

numbers/math_jsc_broad_rate.numbers.json: math_jsc_broad_rate.py numbers/watchlist_split_and_math_broad_rate.numbers.json
	$(call NEED_VARS,EVAL_ARXIV_CATEGORIES_DB)
	$(PYTHON) math_jsc_broad_rate.py

numbers/missed_keys_flag_counts.numbers.json: missed_keys_flag_counts.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) missed_keys_flag_counts.py

numbers/missed_keys_flag_rates.numbers.json: missed_keys_flag_rates.py $(MISSED_KEYS_ROWS_JSON)
	$(PYTHON) missed_keys_flag_rates.py

numbers/multibib_duplicates_within_strict_broad.numbers.json: multibib_duplicates_within_strict_broad.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) multibib_duplicates_within_strict_broad.py

numbers/negative_control_disposition.numbers.json: negative_control_disposition.py $(MASS)/negative_control__negative_control.jsonl
	$(PYTHON) negative_control_disposition.py

numbers/no_citation_log_population_breakdown.numbers.json: no_citation_log_population_breakdown.py $(AUDIT_KEYS_JSON)
	$(PYTHON) no_citation_log_population_breakdown.py

numbers/nocite_quantification.numbers.json: nocite_quantification.py $(AUDIT_JSON) $(AUDIT_KEYS_JSON) $(EVAL_BBL_JSONL_PATH)
	$(PYTHON) nocite_quantification.py

numbers/per_submission_broad_count_distribution.numbers.json: per_submission_broad_count_distribution.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) per_submission_broad_count_distribution.py

numbers/reassessment_sample_composition.numbers.json: reassessment_sample_composition.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON) $(EVAL_SECOND_RUN_ROOT)
	$(PYTHON) reassessment_sample_composition.py

numbers/retraction_risk_tiers.numbers.json: retraction_risk_tiers.py $(RETRACTION_BASIS_CSV)
	$(PYTHON) retraction_risk_tiers.py

numbers/second_vote_survivors_by_discipline.numbers.json: second_vote_survivors_by_discipline.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON) $(EVAL_SNAPSHOT_CATEGORIES_DB)
	$(PYTHON) second_vote_survivors_by_discipline.py

numbers/sibling_inference_validation.numbers.json: sibling_inference_validation.py $(EVAL_DB_PATH_BBL_TEX)
	$(PYTHON) sibling_inference_validation.py

numbers/sibling_issnl_scopus_figures.numbers.json: sibling_issnl_scopus_figures.py $(AUDIT_KEYS_JSON) $(ISSN_L_JSON) numbers/stats_corpus.numbers.json numbers/confirmed_cited_composition.numbers.json
	$(call NEED_VARS,EVAL_ASSESSMENT_RUN_START EVAL_CACHE_DB_PATH_EARLIER EVAL_SCOPUS_XLSX_PATH)
	EVAL_ISSN_L_CHECK_JSON=$(ISSN_L_JSON) $(PYTHON) sibling_issnl_scopus_figures.py

numbers/stats_bbl_recall_full_corpus.numbers.json: stats_bbl_recall_full_corpus.py $(EVAL_DB_PATH) $(EVAL_BBL_JSONL_PATH)
	$(PYTHON) stats_bbl_recall_full_corpus.py

numbers/stats_corpus.numbers.json: stats_corpus.py $(EVAL_DB_PATH_BBL_TEX)
	$(PYTHON) stats_corpus.py

numbers/stats_reproducibility_flip_rate.numbers.json: stats_reproducibility_flip_rate.py $(EVAL_SECOND_RUN_ROOT)
	$(PYTHON) stats_reproducibility_flip_rate.py

numbers/strict_broad_share_ratios.numbers.json: strict_broad_share_ratios.py numbers/strict_predatory_partition_tables.numbers.json numbers/identity_check_table_analyses.numbers.json numbers/multibib_duplicates_within_strict_broad.numbers.json
	$(PYTHON) strict_broad_share_ratios.py

numbers/strict_predatory_partition_tables.numbers.json: strict_predatory_partition_tables.py $(AUDIT_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) strict_predatory_partition_tables.py

numbers/strict_vote_rule_alternatives.numbers.json: strict_vote_rule_alternatives.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) strict_vote_rule_alternatives.py

numbers/submission_flag_counts_by_identity_check.numbers.json: submission_flag_counts_by_identity_check.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) submission_flag_counts_by_identity_check.py

numbers/ugc_care_clone_pair_votes.numbers.json: ugc_care_clone_pair_votes.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) ugc_care_clone_pair_votes.py

numbers/unknown_venue_counts.numbers.json: unknown_venue_counts.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON)
	$(PYTHON) unknown_venue_counts.py

numbers/watchlist_split_and_math_broad_rate.numbers.json: watchlist_split_and_math_broad_rate.py $(CITATION_DETAIL_JSON) $(AUDIT_KEYS_JSON) $(EVAL_ARXIV_CATEGORIES_DB)
	$(call NEED_VARS,EVAL_ARXIV_CATEGORIES_DB)
	$(PYTHON) watchlist_split_and_math_broad_rate.py

# derived_ratios_from_existing_macros.py reads the other numbers files (through make_numbers_tex.load_all()).
ALL_NUMBERS_JSON := \
	numbers/add_one_new_sources_impact.numbers.json \
	numbers/acronym_table_variant_counts.numbers.json \
	numbers/analyze_bulk_database_assessment.numbers.json \
	numbers/analyze_excluded_submissions.numbers.json \
	numbers/analyze_hijacked_doaj_benchmark.numbers.json \
	numbers/any_documented_concern_decomposition.numbers.json \
	numbers/bibliometric_pairwise_agreement.numbers.json \
	numbers/build_retraction_watch_clone_identity_bib.numbers.json \
	numbers/citation_level_disciplinary_rates_by_group.numbers.json \
	numbers/confirmed_cited_composition.numbers.json \
	numbers/core_name_legitimate_issn_check.numbers.json \
	numbers/corpus_share_ratios.numbers.json \
	numbers/corpus_size_partition.numbers.json \
	numbers/disciplinary_composition_included_vs_excluded.numbers.json \
	numbers/disciplinary_submission_counts.numbers.json \
	numbers/disciplinary_table_and_venue_concentration.numbers.json \
	numbers/doaj_current_reinclusion_check.numbers.json \
	numbers/doaj_withdrawn_legitimate_crosstab.numbers.json \
	numbers/headline_distribution_and_vote_rules.numbers.json \
	numbers/identity_audit_population_split.numbers.json \
	numbers/identity_check_table_analyses.numbers.json \
	numbers/issn_presence_by_population.numbers.json \
	numbers/jsc_citation_breakdown.numbers.json \
	numbers/large_file_families.numbers.json \
	numbers/leave_one_out_backend_sweep.numbers.json \
	numbers/leave_one_out_flip_rates.numbers.json \
	numbers/leave_one_out_independent_citations.numbers.json \
	numbers/math_jsc_broad_rate.numbers.json \
	numbers/missed_keys_flag_counts.numbers.json \
	numbers/missed_keys_flag_rates.numbers.json \
	numbers/multibib_duplicates_within_strict_broad.numbers.json \
	numbers/negative_control_disposition.numbers.json \
	numbers/no_citation_log_population_breakdown.numbers.json \
	numbers/nocite_quantification.numbers.json \
	numbers/per_submission_broad_count_distribution.numbers.json \
	numbers/reassessment_sample_composition.numbers.json \
	numbers/retraction_risk_tiers.numbers.json \
	numbers/second_vote_survivors_by_discipline.numbers.json \
	numbers/sibling_inference_validation.numbers.json \
	numbers/sibling_issnl_scopus_figures.numbers.json \
	numbers/stats_bbl_recall_full_corpus.numbers.json \
	numbers/stats_corpus.numbers.json \
	numbers/stats_reproducibility_flip_rate.numbers.json \
	numbers/strict_broad_share_ratios.numbers.json \
	numbers/strict_predatory_partition_tables.numbers.json \
	numbers/strict_vote_rule_alternatives.numbers.json \
	numbers/submission_flag_counts_by_identity_check.numbers.json \
	numbers/ugc_care_clone_pair_votes.numbers.json \
	numbers/unknown_venue_counts.numbers.json \
	numbers/watchlist_split_and_math_broad_rate.numbers.json

numbers/derived_ratios_from_existing_macros.numbers.json: derived_ratios_from_existing_macros.py make_numbers_tex.py $(ALL_NUMBERS_JSON)
	$(PYTHON) derived_ratios_from_existing_macros.py

ALL_NUMBERS_JSON += numbers/derived_ratios_from_existing_macros.numbers.json

$(ALL_NUMBERS_JSON) $(AUDIT_JSON) $(AUDIT_KEYS_JSON) $(CITATION_DETAIL_JSON) $(HEADLINE_JSON): $(HELPER_MODULES)

numbers.tex: make_numbers_tex.py $(ALL_NUMBERS_JSON)
	$(PYTHON) make_numbers_tex.py

.PHONY: all
all: numbers.tex

.DEFAULT_GOAL := all
