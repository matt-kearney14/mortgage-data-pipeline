# Working rules — Mortgage Clickstream Pipeline

## Source of truth

**For facts, the data outranks everything.** The order of authority is: raw files
in `data/` > what the code computes > what any document claims. If a document and
the data disagree, the document is wrong and gets corrected. Never adjust a number,
method or threshold to match a document. If the code and the data disagree, the
code is wrong. Fix it and say so.

Among the documents, for **definitions and intent**:

| Rank | File | Role |
|---|---|---|
| 1 | `docs/Clickstream_Variable_Specification_v2.md` | Canonical variable definitions. Cite by section (§3, §7-B). Where the data contradicts it, an **AUDIT NOTE** in the spec says so; the data wins and the deviation is recorded in DECISIONS.md. |
| 2 | `docs/Project_Brief.md` | Status, inputs, open decisions. |
| 3 | `docs/DECISIONS.md` | Append-only log of every provisional choice, including reversed ones. Never edit or prune earlier entries; append. |
| 4 | `README.md` | How to run the pipeline and read the output. Not a requirements document. |
| — | `user_level_dataset.xlsx` → **Read Me First** and **Data Dictionary** sheets | Generated from code, and every figure in them is computed in the run. Never edit by hand; edit `build_notes()` / `note()` in `phase2_user_dataset.py`. |
| — | `docs/AUDIT_REPORT_2026-10-01.md` | Independent audit of the state as of 2026-09-30, and what it changed. |

Three incompatible numbering schemes exist (professor's sheet, v2 spec, repo README).
**Always reference variables by name, never by number.** Say `Audio`, never "var 16"
or "var 14".

---

## Canonical column names

Naming convention: no spaces, no parentheses, no slashes. `English_YN` style.
These names are frozen. Use them in code, output, docs, and commit messages.

### URL-level

| Spec name | Column | Type | Origin |
|---|---|---|---|
| Personalized | `Personalized` | binary | FIXED |
| GeneralFinancial | `GeneralFinancial` | binary | FIXED |
| MortgageRelated | `MortgageRelated` | binary | FIXED |
| ProcessRelated | `ProcessRelated_provisional` | binary | FIXED / **OPEN** |
| BorrowerMortgageProcessRelated | `BorrowerMortgageProcessRelated` | binary | FIXED |
| LenderMortgageProcessRelated | `LenderMortgageProcessRelated` | binary | FIXED |
| LoanTermsRelated | `LoanTermsRelated` | binary | FIXED |
| LoanEstimateRelated | `LoanEstimateRelated` | binary | FIXED |
| CDRelated | `CDRelated` | binary | FIXED |
| LEDocument | `LEDocument` | binary | FIXED |
| CDDocument | `CDDocument` | binary | FIXED |
| Download | `Download` | binary | FIXED |
| LEDownload | `LEDownload` | binary | OURS |
| CDDownload | `CDDownload` | binary | OURS |
| AudioMp3 | `AudioMp3` | binary | FIXED |
| Audio | `Audio` | **integer count** per spec; 0/1 in practice (DEC-J) | FIXED |
| Video | `Video` | binary | FIXED |
| English (Y/N) | `English_YN` | binary | OURS |
| Spanish (Y/N) | `Spanish_YN` | binary | OURS |
| Goal_to_inform | `Goal_to_inform` | binary | FIXED |
| Goal_to_Advise | `Goal_to_Advise` | binary | FIXED |
| Time spent on page | `time_on_page` | int seconds, nullable | OURS |
| Session start | `session_start` | binary flag | OURS |
| Session end | `session_end` | binary flag | OURS |

`Goal_to_inform` and `Goal_to_Advise` have inconsistent capitalization. That is
inherited from the source. Preserve the dictionary's exact spelling for all FIXED
columns — if the dictionary header differs from the table above, report the
mismatch rather than silently renaming.

`session_start` / `session_end` are **booleans**, not timestamps. The derived
timestamps are separate fields: `session_start_ts`, `session_end_ts`.

### Session-level

`session_duration`, `inter_session_elapsed`, `pages_in_session`

### User-level

`webpages_visited`, `spanish_webpages_visited`, `english_webpages_visited`,
`unique_webpages_visited`, `audio_clips_clicked`, `num_sessions`, `days_accessed`

Schema-expanding: `pages_{Characteristic}` and `time_{Characteristic}`, 18 columns
each, using the exact characteristic column names above. `Audio` is excluded from
both — it is a count, not a flag.

Milestone timers: `t_activation_to_last_access`, `t_application_to_activation`,
`t_activation_to_le_sent`, `t_le_sent_to_first_le_visit`, `t_activation_to_lock`,
`t_last_access_to_current_status`. All signed integer seconds, nullable.

---

## Repository layout

```
data/                  gitignored research data — never commit, never modify sources
  talkument_userinteractions.xlsx     event log, 337,581 rows (sheet user_usage)
  talkument_useraccount.xlsx          accounts: first_login, provided_language, ...
  talkument_loan_applicants.xlsx      loannumber <-> user_hash bridge, language_preference
  talkument_pilot_buckets.xlsx        loan_number -> bucket {1,2,3}; no user_hash
  loan_application_data_partial.csv   loan extract: outcomes, the four milestone
                                      dates, rate, APR, credit score, lender's
                                      activation and language fields (25,318 loans)
docs/
  Clickstream_Variable_Specification_v2.md
  Project_Brief.md
  DECISIONS.md                       append-only
  Phase2_Plan.md                     historical: Phase 2 as proposed
  AUDIT_REPORT_2026-10-01.md
  Clickstream_path_frequencies_and_coding_scheme.xlsx    tracked in git
diagnostics/           read-only analysis scripts; output/ is gitignored
  build_path_dictionary.py   PIPELINE STEP 1: extends beta_coding to all 9,471 paths
  inventory.py               Phase 0 input inventory (predates the loan extract)
  coverage.py                Phase 0 dictionary coverage vs the event log
  audit_recompute.py         independent recomputation of the user-level table
  crossvalidate.py           quantities measured two independent ways
  replicate_activation_table.py   the paper's Table 2, three tabulations
  treatment_effect_time.py   exploratory bucket 3 vs 2 comparison (not a finding)
output/                pipeline outputs, gitignored
  path_dictionary_extended.csv   per-path flags + provenance  (pipeline INPUT)
  dictionary_review_for_professor.xlsx   inferred + uncoded paths to confirm
  phase1_url_features.parquet    event grain + URL characteristics
  phase1_before_after.md         --compare: vs the inherited implementation
  qa_phase1.md, qa_phase2.md     QA, reported as measured
  phase2_events.parquet          + session_id, time_on_page, inferred download type
  phase2_sessions.parquet        one row per session
  session_timeout_sensitivity.csv
  user_level_dataset.xlsx        THE DELIVERABLE — 3 sheets:
                                   'Read Me First' caveats, computed in the run
                                   'User Data' one row per user
                                   'Data Dictionary' every column explained
  user_level_dataset.parquet     the same data
  codebook.csv                   the Data Dictionary sheet, machine-readable
  variable_manifest.csv          Phase 1 provisional columns + decision ids
  discrepancy_log.csv            paths with unresolved flags
clickstream_processor.py    Phase 1 — URL-level characteristics
phase2_user_dataset.py      Phase 2 — sessions, aggregates, loan join, user table
```

Run order: `diagnostics/build_path_dictionary.py` → `clickstream_processor.py`
→ `phase2_user_dataset.py`. Configuration constants live at the top of each;
anything a professor might want changed is also a CLI flag.

---

## Classification dictionary

**Corrected 2026-09-14.** This section previously said `coding_dictionary` is
authoritative and `beta_coding` must not be read. That instruction was
structurally impossible and is withdrawn — see DEC-C.

The two sheets are not parallel tables:

| sheet | rows | shape | role |
|---|---|---|---|
| `coding_dictionary` | 45 | one row per **variable** — a glossary. No `CODING SCHEME` column, no per-URL rows, no join key. | variable *definitions* only |
| `beta_coding` | 104 | one row per **path**, binary flags across columns, keyed on `CODING SCHEME` | the per-path source |

`coding_dictionary` cannot be joined to the event log on any key, so a pipeline
reading only it would have no classification data at all. It is still valuable —
it carries the professor's own wording, confirms `Audio` is meant to be a count,
and holds his `????` flag on `ProcessRelated` — but it cannot drive the merge.

**The pipeline reads `output/path_dictionary_extended.csv`**, built by
`diagnostics/build_path_dictionary.py` from `beta_coding` plus sibling
inference (DEC-G/H/I). It covers all 9,471 log paths and stamps every cell with
a `__prov` provenance value. Filtering to `__prov == 'coded'` returns
`beta_coding` exactly, so nothing is locked in.

Known limits of `beta_coding`, all measured:
- it matches **36.6%** of events on its own (100 of 9,471 distinct paths)
- `LoanTermsRelated` and `CDDocument` are blank on **all 26** coded page rows
- `English(Y/N)` is 1 and `Spanish (Y/N)` is 0 on **every** row — it carries no
  language information whatsoever (DEC-I)

---

## Data hygiene

1. **Never modify the source `.xlsx` files.** They are inputs.
2. **Never silently default an unmapped path to 0.** **Amended 2026-09-14 —
   this rule previously prescribed a 0-fill, which is itself the silent default
   the rule exists to prevent (DEC-L).** Unresolved flags are emitted **NULL**
   and written to `output/discrepancy_log.csv` with hit counts. A 0 asserts "we
   measured this and the answer is no"; an unclassified path supports no such
   claim. The old behaviour is still reachable as `--unresolved-fill 0`.
   Measured cost of the 0-fill: `Goal_to_inform` carried 108,162 fabricated
   zeros against 42,391 real ones, and the four download columns read as
   337,581 measured zeros when not one row had been evaluated.
3. **Sort key** for every order-dependent operation: `user_hash`, `eventdate`
   ascending, then original file row order as a stable tiebreaker. Apply before
   computing any sequential variable.
4. **Ties:** identical timestamps for one user resolve by original row order. Do
   not drop them.
5. **Missing durations are NULL, not 0.** Never clip negative elapsed times —
   vars 38–42 can legitimately be negative.
6. **Duration units:** integer seconds, everywhere, consistently.
7. **Consecutive duplicate URLs** are retained as distinct pageviews (§7-F).
   Report the count.
8. **Random seed** fixed and declared in any sampling script.
9. **Intermediates** are written as parquet. Excel only for final deliverables —
   `to_excel` on 337k rows is the slowest step in the pipeline.

---

## Known ambiguities to surface, not guess

**"Activation" is defined twice.** Spec §5 defines it as first Talkuments access
(first pageview), and the pipeline uses that. `first_login` in
`talkument_useraccount.xlsx` precedes the first pageview for all 10,008 users who
have both, by a median of 2 seconds. A long tail runs to −9,750,114 s. The loan
extract's `Activated_Talkument` is a third, loan-level measure. It agrees with
`first_login` on every loan where both exist.

**Timezone and date resolution.** `eventdate` is timezone-naive with second
resolution. The four milestone dates in `loan_application_data_partial.csv` are
calendar dates with no time of day. Every milestone timer therefore compares a
timestamp with a midnight. When the two fall on the same day, the sign is
undetermined (2,302 borrowers activated on their LE-sent day). `TIMEZONE = "UTC"`
in `phase2_user_dataset.py` is a declaration, not a conversion (DEC-R).

---

## Provisional decisions

Where the spec is ambiguous or a question is unresolved, **make a reasonable
choice and proceed — do not block.** But every such choice requires all three of:

1. An entry in `docs/DECISIONS.md`: ID, choice, rationale, evidence, alternatives
   if overruled, reversal cost.
2. A row in `output/variable_manifest.csv` marking the column provisional with its
   decision ID. Generate this manifest from the code, never by hand.
3. A comment at the point of implementation citing the decision ID.

Prefer choices that are cheap to reverse. Session timeout, timezone, and
dedup-on/off are named constants or CLI flags, never inline literals, so a
professor's "try 60 minutes instead" is a rerun rather than an edit.

**Never resolve an ambiguity silently.** The previous author did this with
`ProcessRelated` and it entered the documentation as settled fact.

### Still genuinely open

- **`ProcessRelated`** (§7-E) — professor's call. Compute as declared in
  `beta_coding`, output as `ProcessRelated_provisional`. Do not treat it as the
  union of Borrower and Lender. Do not inherit the old 48-path list.

**Blocked on inputs we do not hold** (no workarounds; emit NULL):

- **`LEDocument`, `CDDocument`.** Every download path is
  `/Download/LoanDocument/{numeric id}` with no type token, so these need an
  id → document-type lookup (DEC-F). `LEDownload`/`CDDownload` are *inferred* from
  session context under spec §3's fallback (DEC-X). That inference is labelled
  INFERRED everywhere and its limits are in DEC-Z.
- **28 content paths across 6 uncoded topics.** These are routed to the professor
  in `output/dictionary_review_for_professor.xlsx`.

The milestone timers are **not** blocked. All five come from
`loan_application_data_partial.csv` (DEC-U, DEC-V).

Everything else may be decided under the protocol above.

---

## QA

**A check that cannot fail is not a check.** `Spanish_YN + English_YN ==
webpages_visited` passes trivially while English is computed as the complement of
Spanish — it is not evidence of correctness. When writing a validation, state in a
comment what would make it fail.

Validate the language state machine against `provided_language` instead.
Compare each user's modal computed language with their account language, and
report the mismatch count **within each language group**. Pooled, the population is
98.7% English and the check cannot fail. The current figures are in
`output/qa_phase1.md` §1. The 11.48% Spanish mismatch measured earlier under the
spec-literal switching rule turned out to be a defect, not a behavioural finding.
`/translations/en` is emitted on page load (DEC-S).

**`provided_language` is not a pre-treatment covariate.** In the user-level file it
reads `es` for 171 users in bucket 3 and 0 in bucket 2 (282 `es` accounts in the
whole account file). It encodes the treatment bucket, not the borrower. Use
`language_preference` from `talkument_loan_applicants.xlsx` instead. The share of
loans with a Spanish-preference applicant is 2.59% / 2.76% / 2.82% across buckets
1 / 2 / 3.

**Do not type a measured number into a document or a generated sheet.** Compute it
in the run, or cite the output file that computes it. The 2026-10-01 audit found
more than a dozen hand-typed figures that were wrong for the file they described.

Diagnostics stay honest regardless of how reasonable the surrounding decisions
were. Coverage rates, missingness rates, and QA results are reported as measured.

---

## Working style

- Diagnostics before fixes. A read-only script that answers "is this data usable"
  outranks any amount of new pipeline code.
- One defect per commit, each paired with a check that would have caught it.
- After changing any variable's logic, report the changed-row count and five
  concrete before/after examples.
- Use plan mode for anything touching sessionization or time-per-characteristic
  attribution.
