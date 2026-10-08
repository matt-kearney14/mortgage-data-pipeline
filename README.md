# Mortgage Clickstream Pipeline

Transforms the raw Talkument clickstream log into a **user-level behavioural
dataset**: one row per borrower, with every variable in the specification as a
column. The dataset supports research on how mortgage borrowers engage with
disclosure and education content.

> **Reconciled 2026-10-01 by an independent audit** (`docs/AUDIT_REPORT_2026-10-01.md`),
> **and brought to full coverage 2026-10-08** (DEC-AA to DEC-AF). Every figure below
> was recomputed from `data/` by `diagnostics/audit_recompute.py`, which re-implements
> the pipeline independently and agrees with it on every cell. Where a figure is
> generated in each run, this README points to that output rather than copying it.

---

## The deliverable

`output/user_level_dataset.xlsx` is regenerated on every run. It has three sheets:

| sheet | contents |
|---|---|
| **Read Me First** | The caveats that could change a conclusion. Every figure in it is computed in the run that wrote it. |
| **User Data** | 10,138 borrowers × 106 columns, one row per borrower. Filters are on and the header is frozen. |
| **Data Dictionary** | One row per column: the definition, how to read it, its coverage, and the decision behind it. |

`output/codebook.csv` holds the same dictionary in machine-readable form.
`output/user_level_dataset.parquet` holds the same data.

All of the specification's 42 variables are built. At the pageview level every
page characteristic has a value except where a document download's type cannot be
determined (0.3% of pageviews, counted in `downloads_type_unknown`).

- **Where each value comes from.** The professor's coding always wins. Where he
  left a cell blank it is coded from the page's content in
  `docs/page_template_coding.csv` and labelled `coded_by_us` (DEC-AD); every cell
  carries its source in a `__prov` column. His corrections are an edit to that
  file and a re-run.
- **Decided per event** (DEC-AC): a download's document type (from the page it was
  clicked from, then the same document elsewhere, then document number order), and
  the Dashboard's CD flags (1 once the borrower has a Closing Disclosure).
- **Documented deviations:** `Audio` counts the clips that play on a page (DEC-AE);
  `webpages_visited` excludes browser assets and the language files a page loads
  (DEC-P, DEC-Z, DEC-AB); `ProcessRelated` keeps the professor's provisional values
  (DEC-E); pre-pilot test accounts are removed (DEC-AA).
- **Blank by design** (DEC-AF): time on the last page of a session, and milestone
  timers whose date does not exist; `milestone_blank_reason` says which and why.

The loan extract `data/loan_application_data_partial.csv` supplies loan outcomes
and all four milestone dates (application, LE/TIL sent, lock, current status). It
covers 25,318 of the 27,650 pilot loans. Coverage is **not** even across buckets:
13.6% of bucket-1 loans are missing, against 5.9% of buckets 2 and 3.

---

## Running it

```bash
python3 diagnostics/build_path_dictionary.py   # path -> characteristics + provenance
python3 clickstream_processor.py --compare     # event grain, URL characteristics
python3 phase2_user_dataset.py                 # sessions, aggregates, user table
python3 diagnostics/audit_recompute.py         # optional: independent check of the result
python3 diagnostics/build_coverage_sheet.py    # optional: Coding_Dictionary_Coverage.xlsx
```

Requires `pandas`, `openpyxl` and `pyarrow` (`scipy` and `tabulate` for the
diagnostics). Each stage reads the previous stage's output from `output/`.

**New data.** The pipeline names no user, loan or count, so a new extract runs
unchanged if it keeps the five file names in `data/`, their sheet names
(`user_usage`, `users`, `loan_applicants`, `pilot_record`) and column names, and the
loan extract's date formats. Each stage checks this first and stops with a
plain-language list of anything missing. A page the app adds later matches no row
of `docs/page_template_coding.csv`: it stays blank, is counted, and is listed in
the review workbook's `Unmapped_paths` sheet — never silently set to 0.

Changing an assumption is a re-run with a flag, not an edit:

| flag | script | effect |
|---|---|---|
| `--session-timeout 60` | phase2 | re-sessionize at a different inactivity threshold (DEC-N) |
| `--keep-translation-resources` | phase2 | count the `/translations/*` language files as pageviews again (pre-audit behaviour, DEC-Z) |
| `--no-excel` | phase2 | skip the workbook |
| `--pilot-start YYYY-MM-DD` | dictionary, phase1 | override the date before which accounts are treated as testers (DEC-AA); pass to both |
| `--compare` | phase1 | write a before/after against the inherited implementation |
| `--excel` | phase1 | also write the event-grain table as .xlsx (slow) |

To change how a page is coded, edit its row in `docs/page_template_coding.csv`.

---

## Reading the output safely

The Read Me First sheet is the authoritative list. In brief:

- **`pages_X` counts confirmed cases only.** Each one sits beside `unknown_X`, and
  for the two download types beside `downloads_type_unknown`. The three
  `downloads_typed_by_*` columns say what evidence typed each borrower's downloads.
- **The `time_X` columns overlap and must not be summed.** A page can carry several
  characteristics. Use `total_time_observed` as the denominator. `time_X` is
  observed dwell only: the last page of each session contributes 0.
- **`provided_language` is not a pre-treatment covariate.** It reads `es` only in
  bucket 3. Use `language_preference` per person, or `borrower_language` per loan.
- **This file is per person, not per loan.** Aggregate to the loan before comparing
  with the paper's loan-level tables.
- **Milestone dates are calendar dates, not timestamps.** A milestone that falls on
  the same day as activation has an undetermined sign.

---

## Data quality, as measured

| | |
|---|---|
| Events / users / distinct paths in the log | 337,581 / 10,140 / 9,471 |
| Pre-pilot test accounts removed (DEC-AA) | 2 users, 433 events |
| Pageviews after dropping browser assets and language files | 312,983 |
| Page templates in `docs/page_template_coding.csv` | 70 (every path in the log matches one) |
| Pageviews with every characteristic known | 99.7% (the rest: downloads of undeterminable type) |
| Document downloads typed (DEC-AC) | see `output/qa_phase2.md` §4b |
| Sessions at a 30-minute timeout | see `output/session_timeout_sensitivity.csv` |

Every dictionary value carries a provenance stamp in a `__prov` column. Filtering
to `__prov == 'coded'` reproduces `beta_coding` cell for cell, and `coded_by_us`
reproduces the template table cell for cell (`diagnostics/audit_recompute.py`).

---

## Still open

1. **The professor's confirmation of the cells coded by us** and his answers to
   `docs/Professor_Questions.md` (ProcessRelated's `????`, two GeneralFinancial
   exceptions, Audio on the module pages, and the rest). Page by page in
   `output/dictionary_review_for_professor.xlsx`.
2. **The rest of the loan extract.** The 2,332 missing pilot loans are concentrated
   in bucket 1.
3. **Optional: a LoanDocument-id → document-type lookup**, which would replace the
   typing evidence for downloads with a measured type.

---

## Documentation

| File | Role |
|---|---|
| `CLAUDE.md` | Working rules, canonical column names, data hygiene, source-of-truth order |
| `docs/Clickstream_Variable_Specification_v2.md` | Canonical variable definitions, with audit notes where the data contradicts it |
| `docs/Project_Brief.md` | Status, inputs and open decisions |
| `docs/DECISIONS.md` | Append-only log of every provisional choice |
| `docs/page_template_coding.csv` | Every page template's coding: the professor's blanks filled, with a rationale per row |
| `docs/Professor_Questions.md` | What the professor is asked to confirm or decide |
| `docs/Phase2_Plan.md` | Historical: the Phase 2 design as proposed, and how the implementation differs |
| `docs/AUDIT_REPORT_2026-10-01.md` | Independent audit: what was wrong, what changed, what is still open |
| `user_level_dataset.xlsx` → Data Dictionary | Every output column, generated from code |

Refer to variables **by name, never by number**. Three incompatible numbering
schemes exist across the professor's sheet, the v2 spec and this repository's
history.
