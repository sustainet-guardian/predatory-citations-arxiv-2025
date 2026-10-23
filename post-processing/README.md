# post-processing

Statistical analyses that run after Aletheia-Probe's own assessment, against its cached backend results: the identity audit of the strict-predatory references, the ISSN-L check, leave-one-backend-out sweeps, the retraction-risk tiers, the disciplinary tables, and the aggregation of all figures into `numbers.tex`.

**This is not production code.** The scripts were written for one-off research runs and have no test suite. See the top-level README for the repository-wide note.

`stats_common.py`, `identity_vote_rules.py`, `decision_rule.py`, `retraction_tiers.py` and `result_line.py` are shared helper modules. `stats_common.py` holds the SQLite connection, the JSON/CSV writers, the percentage helpers, `write_numbers()`, `read_number()`, `submission_of()` and `contact_user_agent()`; `identity_vote_rules.py` holds the backend lists and the ISSN rules of the identity check; `decision_rule.py` holds the tool's weighted-vote category rule that three scripts re-apply; `retraction_tiers.py` holds the retraction-risk tier thresholds and functions; `result_line.py` derives the columns of an `entries` row from one line of the assessment results (`build_entry_table.py` and `insert_missed_keys_rows.py` both use it). It reads `EVAL_DB_PATH` and `EVAL_OUTPUT_DIR` when it first needs them, so a script needs exactly the variables it uses.

## Before running anything

You need a locally built Aletheia-Probe cache database and the extracted `.bib` corpus: run `pre-check/` and the [aletheia-probe](https://github.com/sustainet-guardian/aletheia-probe) tool on your own copy of arXiv's 2025 LaTeX sources. Every path is an environment variable and none has a default: a script that needs an unset variable stops with an error naming it, and `make` (GNU make 4.3 or later; the Makefile uses grouped targets) checks the variables its rules use before it starts.

## The e-print archives

Both extractors (`pre-check/arxiv_bib_extractor.py` and `extract_citation_keys.py`) read one archive per submission in the layout `<YYMM>/<NNNNN>.tar.gz` below the directory you pass. Bring the e-prints of the bulk files into this layout before you run them.

## Environment variables

| Variable | What it points to |
|---|---|
| `EVAL_DB_PATH` | `entries.db`: one row per `.bib` entry and one row per `.bib` file, built by `build_entry_table.py`; `set_citation_status.py` adds the column `citation_status` (cited according to the citation log) and the view `entries_confirmed` |
| `EVAL_DB_PATH_BBL_TEX` | The database the analyses run on: a copy of `EVAL_DB_PATH` with the column `citation_status_bbl_tex` (cited according to the citation log or the `.bbl`/`.tex` signal; `set_citation_status_bbl_tex.py`) and the view `entries_confirmed_bbl_tex`, including the missed-keys rows (`insert_missed_keys_rows.py`). After step 3 below it is the file `EVAL_DB_PATH_MISSED_KEYS_ROWS` points to |
| `EVAL_DB_PATH_MISSED_KEYS_ROWS` | The copy of the database with `citation_status_bbl_tex` into which `insert_missed_keys_rows.py` inserts the missed-keys rows; `rates_with_missed_keys_rows.py` reads it |
| `EVAL_OUTPUT_DIR` | Output root. Scripts that read another script's output read it from here, so run producers before consumers |
| `EVAL_BIB_ROOT` | Root of the extracted `.bib` corpus (output of `pre-check/`) |
| `EVAL_RESULTS_ROOT` | Root of the per-submission JSONL backend results of Aletheia-Probe (`<month>__<id>-<n>.jsonl`) |
| `EVAL_SECOND_RUN_ROOT` | Root of the JSONL results of a second assessment run, same layout as `EVAL_RESULTS_ROOT` (`stats_reproducibility_flip_rate.py`, `load_assessment_records.py`, `reassessment_sample_composition.py`) |
| `EVAL_CACHE_DB_PATH` | Aletheia-Probe's `cache.db` (backend assessment cache) |
| `EVAL_CACHE_DB_PATH_EARLIER` | `sibling_issnl_scopus_figures.py` only: a copy of `cache.db` taken before the rows described in its docstring were created |
| `EVAL_ASSESSMENT_RUN_START` | `sibling_issnl_scopus_figures.py` only: start of the assessment run, `YYYY-MM-DD HH:MM:SS` (compared with the creation time of cache rows) |
| `EVAL_CONTACT_EMAIL` | Your own contact address, sent in the user agent to arXiv's API and to OpenAlex; used by `fetch_arxiv_categories.py` and `issn_l_family_check.py` (there is no default) |
| `EVAL_ISSN_L_CHECK_JSON` | `sibling_issnl_scopus_figures.py` only: the JSON written by `issn_l_family_check.py` (the Makefile sets it) |
| `EVAL_SCOPUS_XLSX_PATH` | `sibling_issnl_scopus_figures.py` only (needs `openpyxl`): the Scopus source list |
| `EVAL_ACL_VENUES_CSV` | `add_one_new_sources_impact.py` only: the ACL Anthology venue list written by `analyze_bulk_database_assessment.py` (`bulk_database_venues_acl.csv`, one column `venue`; the Makefile sets it) |
| `EVAL_BBL_JSONL_PATH` | The `.bbl`/`.tex` citation signal written by `extract_citation_keys.py --write-citation-keys` |
| `EVAL_ARXIV_SNAPSHOT_JSON` | arXiv metadata snapshot (one JSON record per line), read by `extract_arxiv_categories_from_local_snapshot.py` |
| `EVAL_CATEGORIES_CSV_PATH` | CSV written by `extract_arxiv_categories_from_local_snapshot.py`, read by `build_snapshot_categories_db.py` |
| `EVAL_SNAPSHOT_CATEGORIES_DB` | Categories database written by `build_snapshot_categories_db.py` (see "Categories databases") |
| `EVAL_ARXIV_CATEGORIES_DB` | Categories database written by `fetch_arxiv_categories.py` (see "Categories databases") |
| `EVAL_ALETHEIA_PROBE_SRC` | Optional: the `src/` directory of an Aletheia-Probe checkout, used by `build_entry_table.py` and `insert_missed_keys_rows.py` when the tool is not installed |
| `EVAL_DOAJ_CURRENT_LISTING_CSV` | DOAJ's public journal list (CSV with title, print ISSN and e-ISSN columns), read by `doaj_current_reinclusion_check.py` and `doaj_withdrawn_legitimate_crosstab.py` |

## Categories databases

Two scripts build a categories database, both with the table `categories` (columns `month`, `arxiv_id`, `primary_category`, `all_categories`, `found`):
- `build_snapshot_categories_db.py` (from arXiv's metadata snapshot, through `extract_arxiv_categories_from_local_snapshot.py`) writes `EVAL_SNAPSHOT_CATEGORIES_DB` with every 2025 record of the snapshot and an index on (`month`, `arxiv_id`). The disciplinary tables, the composition of included and excluded submissions, the survivors by discipline and the excluded-submission counts use it.
- `fetch_arxiv_categories.py` (arXiv API, rate-limited) writes `EVAL_ARXIV_CATEGORIES_DB` for the submissions that have a confirmed-cited entry, with the extra column `full_id` as primary key. `watchlist_split_and_math_broad_rate.py` and `math_jsc_broad_rate.py` (through `disciplinary_classification.py`) use it.

The two databases cover different submissions and can differ in the primary category of a shared submission, so a count per discipline (for example Mathematics) differs between the scripts that use the snapshot database and those that use the API database. One database could serve both groups of scripts only after checking such figures.

## Output locations

Each script writes into `EVAL_OUTPUT_DIR/output_<script>/`. Exceptions, because other scripts or users read the file by its path:
- `output_identity_mismatch_audit/` (written by `full_corpus_identity_mismatch_audit.py`, which many scripts read);
- directly in `EVAL_OUTPUT_DIR`: `identity_audit_citation_detail.json` (`identity_check_headline_analyses.py`), `retraction_risk_basis_all_entries.csv` (`stats_retraction_risk_basis.py`), `cited_entry_counts.json` (`cited_entry_counts.py`), `bulk_database_venues_acl.csv` and `bulk_database_assessment.json` (`analyze_bulk_database_assessment.py`), `analysis_manifest.csv` (`build_analysis_manifest.py`);
- `hijacked_doaj_benchmark/`: the benchmark inputs and the files that `build_benchmark_bib_files.py` and `build_retraction_watch_clone_identity_bib.py` write (`bib_input/`, `clone_identity_issn_categories.json`).

The benchmark inputs under `$EVAL_OUTPUT_DIR/hijacked_doaj_benchmark/raw_data/` come from public lists; the `Makefile` names each source and retrieval date. `build_benchmark_bib_files.py` and `build_retraction_watch_clone_identity_bib.py` turn them into `.bib` files; the tool's batch evaluation of those files (the mass evaluation) writes `mass_eval_output/<set>__<set>.jsonl`, which the `analyze_*`, `negative_control_disposition.py`, `doaj_*` and `add_one_new_sources_impact.py` scripts read.

## Running order

1. `build_analysis_manifest.py` writes `analysis_manifest.csv` (month, file name, category, entry count and size of every `.bib` file; a file is `assessed` when the tool wrote a result file for it) from `EVAL_BIB_ROOT` and `EVAL_RESULTS_ROOT`; `build_entry_table.py` reads it and builds `EVAL_DB_PATH` (it refuses to run on a non-empty database unless `--fresh` is given); `set_citation_status.py` adds `citation_status` to it.
2. `extract_citation_keys.py --write-citation-keys --output-path <file>` writes `EVAL_BBL_JSONL_PATH`.
3. The database copies. Each step copies the file of the previous step and runs one script on the copy:

   | Step | Copy of | Input variable | Output variable | File that is written | Script run on the copy |
   |---|---|---|---|---|---|
   | 3a | the database of step 1 | `EVAL_DB_PATH` | `EVAL_DB_PATH_BBL_TEX` | `cp $EVAL_DB_PATH $EVAL_DB_PATH_BBL_TEX` | `set_citation_status_bbl_tex.py` (adds `citation_status_bbl_tex`, `citation_status_bbl_tex_changed` and the view `entries_confirmed_bbl_tex`) |
   | 3b | the file of step 3a | `EVAL_DB_PATH_BBL_TEX` | `EVAL_DB_PATH_MISSED_KEYS_ROWS` | `cp $EVAL_DB_PATH_BBL_TEX $EVAL_DB_PATH_MISSED_KEYS_ROWS` | `insert_missed_keys_rows.py` (adds the missed-keys rows, flagged `added_missed_key_row`; every column that the results line gives is derived as in `build_entry_table.py`) |

   After step 3b, set `EVAL_DB_PATH_BBL_TEX` to the file that `EVAL_DB_PATH_MISSED_KEYS_ROWS` points to: the analyses run on that file (it holds the missed-keys rows). While you run step 3a and 3b the scripts read the variables as the table shows.
4. `extract_arxiv_categories_from_local_snapshot.py`, then `build_snapshot_categories_db.py`; `fetch_arxiv_categories.py` builds the second categories database.
5. Intermediate files (next section): `full_corpus_identity_mismatch_audit.py`, `stats_retraction_risk_basis.py`, `identity_check_headline_analyses.py` and `issn_l_family_check.py` run with their flags (`--write-citation-keys`, `--write-venue-table`), in this order (`issn_l_family_check.py` needs the audit file).
6. A numbers script that reads another script's numbers file runs after it: `corpus_share_ratios.py` after `disciplinary_submission_counts.py` and `any_documented_concern_decomposition.py`; `strict_broad_share_ratios.py` after `strict_predatory_partition_tables.py`, `identity_check_table_analyses.py` and `multibib_duplicates_within_strict_broad.py`; `rates_with_missed_keys_rows.py` after `strict_predatory_partition_tables.py`, `missed_keys_flag_counts.py` and `issn_l_family_check.py`; `jsc_citation_breakdown.py` after `headline_distribution_and_vote_rules.py`; `math_jsc_broad_rate.py` after `watchlist_split_and_math_broad_rate.py`; `sibling_issnl_scopus_figures.py` after `stats_corpus.py` and `confirmed_cited_composition.py`; `corpus_size_partition.py` after `cited_entry_counts.py`.
7. All other scripts run in any order once these inputs exist; each docstring names what it reads and which numbers it writes.
8. `derived_ratios_from_existing_macros.py` runs after all other numbers files exist; `make_numbers_tex.py` collects the numbers files into `numbers.tex`. `make numbers.tex` (GNU make 4.3 or later) runs the numbers scripts in dependency order (it does not run steps 1 to 4 and the benchmark steps).

## Intermediate files

Some files list individual citations, submissions or venues and are not released: do not commit them. They exist only as input of later scripts and are written only with the flag named here:
- `full_corpus_identity_mismatch_audit_keys.json` (`--write-citation-keys`): keys of the audited citations;
- `identity_audit_citation_detail.json` (`--write-citation-keys`): keys and replay verdicts of the audited citations;
- `retraction_risk_basis_all_entries.csv` (`--write-venue-table`): venue and basis fields of the citations with a recorded tier of moderate or higher;
- `issn_l_family_check.json` with `restored_citation_keys` and `per_citation_detail` (`--write-citation-keys`);
- `openalex_issnl_lookup_cache.json` (written by `issn_l_family_check.py`): journal titles with their ISSN-L, one entry per ISSN looked up (`issn_l_family_check.py` queries `https://api.openalex.org/sources/issn:<ISSN>` for each ISSN pair that is not in this cache, at most four requests per second, with the contact address of `EVAL_CONTACT_EMAIL` in the user agent);
- `clone_identity_issn_categories.json` (`--write-citation-keys` of `build_retraction_watch_clone_identity_bib.py`): ISSN category per benchmark entry;
- `EVAL_BBL_JSONL_PATH` (`--write-citation-keys` of `extract_citation_keys.py`): the citation keys of every submission;
- `analysis_manifest.csv` and the CSV at `EVAL_CATEGORIES_CSV_PATH`: one row per `.bib` file and one row per submission.
`bulk_database_venues_acl.csv` lists venue names of the ACL Anthology bibliography only.

## Numbers files

Each script that supplies figures for the paper writes `numbers/<script>.numbers.json` through `stats_common.write_numbers()`. A file holds aggregates only (counts, ratios, rates), one key per figure, in one of three kinds (example):

```json
{
  "pct_example":   {"kind": "ratio", "num": 1, "den": 4, "decimals": 1},
  "count_example": {"kind": "int",   "value": 1234},
  "range_example": {"kind": "float", "value": -15.3, "decimals": 1}
}
```

A ratio stores numerator and denominator; rounding happens once, in `make_numbers_tex.py`, which writes one `\newcommand` per key (`snake_case` key to `CamelCase` macro) and stops if two files define the same key. It also checks invariants, such as partitions that must sum to their total. `derived_ratios_from_existing_macros.py` derives percentages and sums from keys that already exist in other numbers files. The files hold only the keys that a macro of the paper, an invariant, a derived ratio or another script's `read_number()` needs.

## Terms

- **citation log**: the `-cited.csv` file that the extraction writes for a `.bib` file; it lists the entry keys that the submission cites. (Key names and database columns that contain `sidecar` refer to it.)
- **confirmed-cited entry**: an entry whose key is cited according to the citation log, or, in `EVAL_DB_PATH_BBL_TEX`, according to the `.bbl`/`.tex` signal. **Promoted**: an entry that becomes confirmed-cited through the `.bbl`/`.tex` signal although the citation log does not list it.
- **`citation_status_bbl_tex_changed`**: column set by `set_citation_status_bbl_tex.py`: `'1'` for the rows whose `citation_status_bbl_tex` differs from `citation_status` (including the rows promoted to confirmed-cited by the `.bbl`/`.tex` signal), `'0'` for all others. **`added_missed_key_row`**: column added by `insert_missed_keys_rows.py`: `'1'` on the rows it inserts, NULL on every other row. **`entries_confirmed_all`**: temporary view that the analysis scripts create over `entries_confirmed_bbl_tex` (every confirmed-cited row, before the identity check); `entries_confirmed` is then the temporary view without the `strict_exclude` rows (the strict population after the identity check).
- **missed keys / missed-keys rows**: keys cited in the `.bbl`/`.tex` signal but absent from the citation log of a `.bib` file that has one; `insert_missed_keys_rows.py` adds their entries as rows.
- **sibling**: another `.bib` file of the same submission. **Isolated**: a file without citation log whose submission has no other file with one (`unverified_isolated`); a file without citation log whose submission has a sibling with one is `excluded_sibling_inferred_uncited` (its entries not cited by that sibling).
- **strict verdict**: final assessment `predatory`; **broad verdict**: at least one predatory list voted `predatory`. **Winning backend**: a predatory list that voted `predatory` for an entry (`predatory_list_hits`). The code says "watchlist" and "backend" for a predatory list.
- **has_conflict**: column of the tool, set when backends disagree about the entry.
- **identity check**: the ISSN of the cache row behind a vote is compared with the ISSN of the citing entry. Result per citation: `confirmed_misattribution` (a vote is invalid: the cache ISSN differs, or is empty while another row carries the citing ISSN), `confirmed_correct`, `structurally_undecidable` (a vote of a clone-detection list, which cannot be decided by ISSN) or `unverifiable` (the entry has no ISSN). **Invalid vote**: a vote removed by the identity check. **Second-vote survivor**: a citation that is still `predatory` after its invalid votes are removed. **Undecided**: the second-vote survivors plus the structurally undecidable citations.
- **`group2` and `grouptwo`**: the tool's backend names end in `group2` (`ugc_care_cloned_group2`, `ugc_care_delisted_group2`); a numbers key becomes a LaTeX macro name and `make_numbers_tex.py` strips digits from macro names, so the keys of used macros spell it `grouptwo` (other keys keep `group2`).
- **clone group**: the lists `ugc_care_cloned` and `ugc_care_cloned_group2`, which the tool builds from the UGC-CARE pages of discontinued journals ("cloned journals", Group I and Group II). The code uses the lists as clone-detection lists.
- **recorded tier / recomputed tier**: the retraction-risk tier stored by the tool, and the same tier computed without the criterion on the recent retraction rate for venues with fewer than `MIN_RECENT_PUBLICATIONS` recent publications (the rate of a venue with very few recent publications is not meaningful).
- **any documented concern**: a submission with at least one broad-predatory citation, a hijacked-flagged citation or a citation with an elevated recomputed tier.
- **CORE**: the Computing Research and Education conference and journal rankings; `core_journals` and `core_conferences` are the tool's backends for them.
- **mass evaluation**: the tool's batch run over a directory of `.bib` files, writing one JSONL file per set.
- **Key parts**: `precorr`, `raw` or `original` means: without the identity check; `corrected`, `postcorr` or `after`: with it. `l1o69_*` count the citations that lose their strict verdict when `ugc_care_cloned_group2` is left out and are not identity-check cases (`razor_thin_tie`: the confidence of the predatory and the legitimate votes is so close that removing either vote flips the verdict).
- **`a1_*` keys** (`corpus_size_partition.py`): the reconciliation of the cited keys of the files that have a citation log with the confirmed-cited entries. `a1_log_having_identified_as_cited` is the per-file recount of the cited keys (logged keys that the `.bbl`/`.tex` signal also confirms); `a1_log_having_materialization_gap` is that recount minus the confirmed-cited entries of the same files (keys without an entries row); `a1_log_having_residual_x` is minus the confirmed-cited entries of the files without a citation log. The identity `identified - gap - residual = confirmed-cited entries` is one of the invariants of `make_numbers_tex.py`.

## Scripts

| Script | What it computes |
|---|---|
| `build_analysis_manifest.py`, `build_entry_table.py`, `set_citation_status.py`, `set_citation_status_bbl_tex.py`, `insert_missed_keys_rows.py` | The manifest of `.bib` files, the entries database and the copies with the citation statuses |
| `extract_citation_keys.py`, `sibling_inference_validation.py`, `nocite_quantification.py` | Extraction of the `.bbl`/`.tex` citation signal; the sibling inference check; the `\nocite` effect |
| `stats_corpus.py`, `corpus_size_partition.py`, `large_file_families.py`, `multibib_duplicates_within_strict_broad.py`, `cited_entry_counts.py`, `stats_bbl_recall_full_corpus.py`, `confirmed_cited_composition.py`, `no_citation_log_population_breakdown.py`, `analyze_bulk_database_assessment.py`, `analyze_excluded_submissions.py` | Corpus size, file classes and families, duplicate rows, share of entries never cited, agreement of the citation logs with the `.bbl`/`.tex` signal, composition of the confirmed-cited entries, excluded submissions |
| `headline_distribution_and_vote_rules.py`, `unknown_venue_counts.py`, `strict_predatory_partition_tables.py`, `strict_broad_share_ratios.py`, `corpus_share_ratios.py`, `per_submission_broad_count_distribution.py`, `any_documented_concern_decomposition.py`, `disciplinary_submission_counts.py`, `submission_flag_counts_by_identity_check.py` | Distribution of assessments, partition tables, share ratios, per-submission counts |
| `full_corpus_identity_mismatch_audit.py`, `identity_audit_population_split.py`, `issn_l_family_check.py`, `issn_presence_by_population.py`, `core_name_legitimate_issn_check.py`, `sibling_issnl_scopus_figures.py`, `ugc_care_clone_pair_votes.py` | Identity audit of the strict-predatory references and its ISSN checks |
| `identity_check_headline_analyses.py`, `identity_check_table_analyses.py`, `strict_vote_rule_alternatives.py`, `second_vote_survivors_by_discipline.py`, `watchlist_split_and_math_broad_rate.py`, `math_jsc_broad_rate.py`, `jsc_citation_breakdown.py` | Tables recomputed after the identity check |
| `leave_one_out_helpers.py`, `leave_one_out_flip_rates.py`, `leave_one_out_backend_sweep.py`, `leave_one_out_independent_citations.py`, `bibliometric_pairwise_agreement.py` | Leave-one-backend-out sensitivity (the first is a helper module) and agreement of the bibliometric backends |
| `stats_retraction_risk_basis.py`, `retraction_risk_tiers.py` | Retraction-risk tiers |
| `disciplinary_classification.py`, `citation_level_disciplinary_rates_by_group.py`, `disciplinary_composition_included_vs_excluded.py`, `disciplinary_table_and_venue_concentration.py`, `extract_arxiv_categories_from_local_snapshot.py`, `build_snapshot_categories_db.py`, `fetch_arxiv_categories.py` | Subject categories and disciplinary rates (`disciplinary_classification.py` is a helper module) |
| `rates_with_missed_keys_rows.py`, `missed_keys_flag_counts.py`, `missed_keys_flag_rates.py` | Counts and rates with and without the missed-keys rows |
| `stats_reproducibility_flip_rate.py`, `load_assessment_records.py`, `reassessment_sample_composition.py` | Comparison of two assessment runs (`load_assessment_records.py` is a helper module) |
| `build_benchmark_bib_files.py`, `build_retraction_watch_clone_identity_bib.py`, `analyze_hijacked_doaj_benchmark.py`, `analyze_identity_and_negative_control.py`, `negative_control_disposition.py`, `doaj_current_reinclusion_check.py`, `doaj_withdrawn_legitimate_crosstab.py`, `add_one_new_sources_impact.py`, `acronym_table_variant_counts.py` | Benchmark sets built from public lists and their evaluation; the number of name variants of one acronym in the tool's acronym table |
| `stats_common.py`, `identity_vote_rules.py`, `decision_rule.py`, `retraction_tiers.py`, `result_line.py`, `make_numbers_tex.py`, `derived_ratios_from_existing_macros.py` | Shared helper modules, collection into `numbers.tex`, derived ratios |
