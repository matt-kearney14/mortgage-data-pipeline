# Mortgage Clickstream Pipeline

Transforms the raw Talkument clickstream log into a **user-level behavioural
dataset**: one row per borrower, with every variable in the specification as a
column. The dataset supports research on how mortgage borrowers engage with
disclosure and education content.

> **Reconciled 2026-10-01 by an independent audit** (`docs/AUDIT_REPORT_2026-10-01.md`).
> Every figure below was recomputed from `data/` by `diagnostics/audit_recompute.py`.
> Where a figure is generated in each run, this README points to that output
> rather than copying the number.

---

## The deliverable

`output/user_level_dataset.xlsx` is regenerated on every run. It has three sheets:

| sheet | contents |
|---|---|
| **Read Me First** | The caveats that could change a conclusion. Every figure in it is computed in the run that wrote it. |
| **User Data** | 10,140 borrowers × 99 columns, one row per borrower. Filters are on and the header is frozen. |
| **Data Dictionary** | One row per column: the definition, how to read it, and, for the four empty columns, which input each one is waiting on. |

`output/codebook.csv` holds the same dictionary in machine-readable form.
`output/user_level_dataset.parquet` holds the same data.

Of the specification's 42 variables:

- **Built from measured data:** all except the four below. Some are built under a
  documented deviation from the spec. `Audio` is 0/1 rather than a count (DEC-J).
  `webpages_visited` excludes non-page requests (DEC-P, DEC-Z). `ProcessRelated` is
  provisional (DEC-E).
- **Inferred, not measured:** `LEDownload` and `CDDownload` (DEC-X, DEC-Z). Read the
  Data Dictionary note before using them. The "LE" label rests almost entirely on a
  tie-break rule.
- **Empty:** `LEDocument` and `CDDocument`. They wait on a LoanDocument-id →
  document-type lookup.

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
```

Requires `pandas`, `openpyxl` and `pyarrow` (`scipy` and `tabulate` for the
diagnostics). Each stage reads the previous stage's output from `output/`. All
four data files in `data/` are required.

Changing an assumption is a re-run with a flag, not an edit:

| flag | script | effect |
|---|---|---|
| `--session-timeout 60` | phase2 | re-sessionize at a different inactivity threshold (DEC-N) |
| `--keep-translation-resources` | phase2 | count `/translations/en` as a pageview again (pre-audit behaviour, DEC-Z) |
| `--no-excel` | phase2 | skip the workbook |
| `--unresolved-fill 0` | phase1 | fill unresolved characteristics with 0 instead of NULL (DEC-L) |
| `--lang-en-switch {never,always,not-after-module,not-paired-with-es}` | phase1 | when `/translations/en` counts as a language switch (DEC-S) |
| `--lang-asset-paths` | phase1 | also switch language on `/es/` or `/en/` asset paths (DEC-M) |
| `--compare` | phase1 | write a before/after against the inherited implementation |
| `--excel` | phase1 | also write the event-grain table as .xlsx (slow) |

---

## Reading the output safely

The Read Me First sheet is the authoritative list. In brief:

- **`pages_X` counts confirmed cases only.** Each one sits beside `unknown_X`, and
  for the two download types beside `downloads_type_unknown`.
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
| Pageviews after dropping browser assets and `/translations/en` | 313,684 |
| Path keys coded in `beta_coding` / seen in the log | 103 / 100 |
| Event coverage from coded paths alone | 36.6% |
| Event coverage after sibling inference and rules (DEC-G/H) | 47–89%, by characteristic |
| Inference accuracy, leave-one-out | 90.9% (169 of 186 held-out cells; a no-information baseline scores 74.4%) |
| Sessions at a 30-minute timeout | 34,403 (see `output/session_timeout_sensitivity.csv`) |

Every dictionary value carries a provenance stamp in a `__prov` column. Filtering
to `__prov == 'coded'` reproduces `beta_coding` cell for cell, for the 100 coded
paths that occur in the log.

---

## Still missing

1. **A LoanDocument-id → document-type lookup.** Every download path is
   `/Download/LoanDocument/{numeric id}` with no type token. The lookup would
   replace the inferred `LEDownload`/`CDDownload` and fill `LEDocument`/`CDDocument`.
2. **Coding for 28 content paths across 6 topics** (8.45% of events). These are
   prepared for the professor in `output/dictionary_review_for_professor.xlsx`.
3. **The professor's ruling on `ProcessRelated`** (DEC-E).
4. **The rest of the loan extract.** The 2,332 missing pilot loans are concentrated
   in bucket 1.

---

## Documentation

| File | Role |
|---|---|
| `CLAUDE.md` | Working rules, canonical column names, data hygiene, source-of-truth order |
| `docs/Clickstream_Variable_Specification_v2.md` | Canonical variable definitions, with audit notes where the data contradicts it |
| `docs/Project_Brief.md` | Status, inputs and open decisions |
| `docs/DECISIONS.md` | Append-only log of every provisional choice |
| `docs/Phase2_Plan.md` | Historical: the Phase 2 design as proposed, and how the implementation differs |
| `docs/AUDIT_REPORT_2026-10-01.md` | Independent audit: what was wrong, what changed, what is still open |
| `user_level_dataset.xlsx` → Data Dictionary | Every output column, generated from code |

Refer to variables **by name, never by number**. Three incompatible numbering
schemes exist across the professor's sheet, the v2 spec and this repository's
history.
