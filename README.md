# predatory-citations-arxiv-2025

Scripts that build the corpus and compute the numbers of the paper
*"Measuring Citations to Predatory Journals at Scale: Lessons from arXiv's 2025 BibTeX Files"*
(Florath). The repository holds the scripts and the aggregate figures they write; no data.

**This is not production code.** The scripts were written for one-off research
runs and have no test suite. Expect research-grade code, not a maintained tool.

The journal-assessment tool used throughout the study is
[aletheia-probe](https://github.com/sustainet-guardian/aletheia-probe) (the study used commit
`9b5869b`, package 0.9.0). The venue-acronym dataset is
[venue-acronyms-2025](https://github.com/sustainet-guardian/venue-acronyms-2025).

## Structure

- `pre-check/`: corpus construction. `arxiv_bib_extractor.py` extracts the `.bib`
  files and the citation logs from the LaTeX source packages. This runs before the
  assessment by the tool. Put the packages for 2025 in the layout
  `25MM/NNNNN.tar.gz` before running the extractor.
- `post-processing/`: the statistical analyses that run after the tool's
  assessment, against its cached backend results, and the collection of every
  figure of the paper into `numbers.tex`. It holds the scripts, a `Makefile`
  that runs them in order, and `numbers/` with the aggregate figures that the paper
  prints. See `post-processing/README.md`.
- `requirements.txt`, `CITATION.cff`, `LICENSE`: dependencies, how to cite, license.

Some scripts write a file with the keys of individual citations only when called with
`--write-citation-keys` (`post-processing/Makefile` passes the flag); later scripts read these files.
Run the
scripts against your own data first; `post-processing/README.md` gives the running order.

## What is not included

Raw downloaded data (arXiv source packages, extracted `.bib` files), the
per-citation assessment results and any list of venues with verdicts are not
published. To reproduce the numbers, rebuild the data from your own copy of
arXiv's sources.

## Citation and license

See `CITATION.cff`. MIT license (see `LICENSE`).
