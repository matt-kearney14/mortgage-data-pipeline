# Mortgage Clickstream Pipeline

Transforms raw Talkument clickstream logs into a **user-level behavioral dataset**
— one row per borrower, every variable in the specification as a column — for
analysis of how mortgage borrowers engage with disclosure and education content.

> **Rewritten 2026-09-14.** The previous README described an inherited pipeline
> whose output contained fabricated zeros, four identically-empty columns, and a
> static language rule the specification does not describe. It also referenced
> `03_final_merge.py`, which does not exist. None of that is accurate any more.

---

## The deliverable

`output/user_level_dataset.xlsx` — regenerated on every run, with two sheets:

| sheet | contents |
|---|---|
| **User Data** | 10,140 users × 81 columns, one row per borrower. Filters on, header frozen. |
| **Data Dictionary** | every column explained — definition, how to read it, and for anything not yet computable, exactly what input is needed. Blocked columns sort to the top. |

`output/codebook.csv` is the same dictionary in machine-readable form.

Thirteen columns are present but deliberately all-NULL — the four download
characteristics and five milestone timers — because the source data for them does
not exist yet. Keeping them stabilises the schema; the codebook states why each
is empty.

---

## Running it

```bash
python3 diagnostics/build_path_dictionary.py   # path -> characteristics + provenance
python3 clickstream_processor.py --compare     # event grain, URL characteristics
python3 phase2_user_dataset.py                 # sessions, aggregates, user table
```

Requires `pandas`, `openpyxl`, `pyarrow`. Each stage reads the previous stage's
output from `output/` and writes QA alongside it.

Anything a professor might reasonably want changed is a flag, not an edit:

| flag | effect |
|---|---|
| `--session-timeout 60` | re-sessionize at a different inactivity threshold |
| `--unresolved-fill 0` | fill unresolved characteristics with 0 instead of NULL |
| `--lang-asset-paths` | also switch language state on `/es/` or `/en/` asset paths |
| `--compare` | write a before/after against the inherited implementation |

---

## Reading the output safely

Three properties of the dataset that are easy to misread.

**`pages_X` counts confirmed 1s only.** Each ships beside `unknown_X`, the count
of the user's pageviews where that characteristic could not be determined. A low
`pages_X` can mean "didn't read that" or "we couldn't classify those pages" —
`unknown_X` is how you tell. `pages_unattributable` and `pct_pages_classified`
give the same picture overall; the median user is 95.8% classified.

**The `time_X` columns overlap and must not be summed.** A page carrying several
characteristics is counted in each, so the columns total about 1.41× real time.
`total_time_observed` is the only valid denominator.

**`provided_language` is not a pre-treatment covariate.** It reads `es` for 280
users in pilot bucket 3 and 0 in bucket 2 — it encodes the treatment arm, not the
borrower. Use `language_preference` from the applicant file, which is balanced
across arms.

---

## Data quality, as measured

| | |
|---|---|
| Events / users / distinct paths | 337,581 / 10,140 / 9,471 |
| Paths coded by the professor | 103 |
| Event coverage from those alone | 36.6% |
| After sibling inference (DEC-G) | 68–89% depending on characteristic |
| Inference accuracy, leave-one-out | 90.9% (169 of 186 held-out cells) |
| Sessions at a 30-minute timeout | 34,436 |

Every value carries a provenance stamp. Filtering to `__prov == 'coded'` returns
the professor's original 103 paths exactly, so no analysis is locked into the
inference.

---

## Known blockers

Nine of the 42 variables cannot be built from the files in `data/`:

1. **A LoanDocument-id → document-type lookup.** Every download path is
   `/Download/LoanDocument/{numeric id}` with no type token; the inherited
   regexes matched 0 of 337,581 rows. Blocks 4 variables.
2. **A real milestone-date source.** `talkument_loan_applicants.xlsx` contains
   no `Application_Date`, `LE_TIL_Sent_Date`, `Lock_Date` or
   `Current_Status_Date` despite earlier documentation saying so. Blocks 5.
3. **Coding for 28 content paths across 6 topics** — not blocking, but worth
   8.45% of events. Prepared in `output/dictionary_review_for_professor.xlsx`.

---

## Documentation

| File | Role |
|---|---|
| `CLAUDE.md` | Working rules, canonical column names, data hygiene |
| `docs/Clickstream_Variable_Specification_v2.md` | Canonical variable definitions |
| `docs/Project_Brief.md` | Status, defects, open decisions |
| `docs/DECISIONS.md` | Every provisional choice — ID, rationale, evidence, reversal cost |
| `docs/Phase2_Plan.md` | Sessionization and attribution design |
| `user_level_dataset.xlsx` → Data Dictionary | Every output column explained, generated from code |
| `output/codebook.csv` | The same, machine-readable |

Reference variables **by name, never by number** — three incompatible numbering
schemes exist across the professor's sheet, the v2 spec, and this repository's
history.
