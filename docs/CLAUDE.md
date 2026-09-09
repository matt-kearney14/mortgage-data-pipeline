# Working rules — Mortgage Clickstream Pipeline

## Source of truth

| Rank | File | Role |
|---|---|---|
| 1 | `docs/Clickstream_Variable_Specification_v2.md` | Canonical variable definitions. Cite by section (§3, §7-B). |
| 2 | `docs/Project_Brief.md` | Status, known defects, open decisions. |
| 3 | `docs/DECISIONS.md` | Every provisional choice we have made. |
| — | `README.md` | **INHERITED AND PARTLY WRONG.** Describes what the code currently does, not what it should do. Its variable numbering conflicts with the spec. Not a requirements document. Do not use it to resolve a question. |

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
| Audio | `Audio` | **integer count** | FIXED |
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
  talkument_userinteractions.xlsx     event log, 337,581 rows
  talkument_useraccount.xlsx
  talkument_loan_applicants.xlsx
  talkument_pilot_buckets.xlsx        unreferenced in spec — purpose TBD
docs/
  Clickstream_Variable_Specification_v2.md
  Project_Brief.md
  DECISIONS.md
  Clickstream_path_frequencies_and_coding_scheme.xlsx    tracked in git
diagnostics/           read-only analysis scripts
  output/              gitignored
output/                pipeline outputs, gitignored
clickstream_processor.py    the entry point
```

`03_final_merge.py` does not exist. The README references it; the README is wrong.
Configuration constants live at the top of `clickstream_processor.py`.

---

## Classification dictionary

**`coding_dictionary` is authoritative.** `beta_coding` was the professor
experimenting and must not be read by the pipeline.

The inherited code reads `beta_coding` and joins on the key `CODING SCHEME`.
Both the sheet name and the key must be re-verified against `coding_dictionary` —
its headers and key column may differ. Do not assume the column list in the
existing `static_vars` array transfers.

Where the two sheets code the same path differently, that is a finding worth
reporting, not a problem to reconcile silently.

---

## Data hygiene

1. **Never modify the source `.xlsx` files.** They are inputs.
2. **Never silently default an unmapped path to 0.** Unmapped paths receive 0 for
   FIXED flags *and* are written to a discrepancy log with hit counts.
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

**"Activation" is defined twice.** Spec §5 defines it as first Talkuments access;
the brief says `talkument_useraccount.xlsx` contains activation data. Vars 37–41
all depend on which is meant. If the account file has an activation date that
differs from first pageview, report the discrepancy distribution and open a
decision — do not pick one silently.

**Timezone.** Before declaring one, check whether `eventdate` is tz-aware and what
zone the milestone dates in `talkument_loan_applicants.xlsx` use. If they differ,
the milestone arithmetic is wrong until reconciled. Declare the chosen zone in
code as a named constant and in `DECISIONS.md`.

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
  `coding_dictionary`, output as `ProcessRelated_provisional`. Do not treat it as
  the union of Borrower and Lender. Do not inherit the old 48-path list.

Everything else may be decided under the protocol above.

---

## QA

**A check that cannot fail is not a check.** `Spanish_YN + English_YN ==
webpages_visited` passes trivially while English is computed as the complement of
Spanish — it is not evidence of correctness. When writing a validation, state in a
comment what would make it fail.

Validate the language state machine against the activated-language field instead:
compare each user's modal computed language to their account language and report
the mismatch count.

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
