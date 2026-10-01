# Independent audit — mortgage clickstream pipeline

**Audited state:** branch `backup/pre-audit-2026-09-30` (commit `a6dec42`), frozen and untouched.
**Audit work:** branch `audit`, one commit per change. Compare with
`git diff backup/pre-audit-2026-09-30..audit`.
**Method:** I read the code and data first and formed a view before reading
`docs/DECISIONS.md`. I recomputed everything from `data/` with code that shares
nothing with the pipeline (`diagnostics/audit_recompute.py`, plus the ad hoc checks
quoted below). Data > code > documents.

## Headline

The pipeline's core mechanics are sound. I independently reproduced the sort
order, tie-breaking, session boundaries, dwell, volume counts, language state, the
loan join and all five external milestone timers exactly, cell for cell. **Two
defects changed published values:**

1. **`/translations/en` was counted as a pageview and credited dwell.** The
   project's own evidence (DEC-S) shows the app emits it on module page load. It
   follows the module page by a median 1 s, so it took the module's reading time:
   **291 hours, 8.9% of all observed time, credited to a row with no
   characteristics.** Fixed (DEC-Z). `time_LoanEstimateRelated` +272%,
   `time_CDRelated` +95%, `pages_unattributable` 19,581 → 359.
2. **`pages_C` counted the parent page's flags on audio rows, against spec var 32.**
   `pages_LoanTermsRelated` was 0 for all 10,140 users as a direct result. Fixed.
   Up to 715 users change per column.

Separately, **more than a dozen figures typed into the deliverable's Read Me sheet,
codebook and decision log were wrong for the file they described**. Two
substantive claims were false: that the missing loans are "spread evenly across
buckets", and that "bucket 1 never appears". All such figures are now computed in
the run.

---

## 1. Removed in Task 1, with proof the outputs did not change

Commit `4711023`. Every removal was verified by re-running the pipeline and
diffing **content**: parquet with `assert_frame_equal` (exact, including dtypes and
row order), xlsx cell by cell on every sheet, csv/md byte for byte. Results were
IDENTICAL for the default run and for `--unresolved-fill 0`,
`--lang-en-switch always` and `--session-timeout 60`. All diagnostics scripts gave
identical output too, except `inventory_report.md`, which differs only in its
generation timestamp and absolute paths.

| file | removed | why it was dead |
|---|---|---|
| `clickstream_processor.py` | `DICT_FLAGS_OVERRIDDEN` | never read |
| | `if nc not in new.columns or oc not in old.columns: continue` in `compare()` | always false: every paired column exists on both sides |
| | `if col in df.columns else 0` in the manifest | always true; the next line would raise anyway |
| | 4 copies of the `ProcessRelated` → `_provisional` mapping | duplicated logic, now `out_name()` |
| | docstring saying `/translations/en` and `/es/` asset paths switch language | contradicted the defaults (both opt-in) |
| `phase2_user_dataset.py` | the whole "loan file absent" fallback (`BLOCKED_MILESTONES`, `WAIT_MILESTONE`, `if loans is not None`, `if "loan_status" in u.columns`) | the file is an input. A missing file now fails loudly instead of silently shipping a degraded deliverable. This changes behaviour **only** when `data/` is incomplete. |
| | `Border`, `Side` imports; `sess`/`args` parameters of `build_notes`/`write_workbook` | unused |
| | three copies of the unique-or-null lambda | now `single_or_null()` |
| | re-sort inside `infer_download_type()` | ran after `sessionize()` on an already-sorted frame. The sort is now enforced once, *before* sessionization, where the order matters. |
| | `BLOCKED_CHARACTERISTICS` name | two of the four are no longer blocked → `DOCUMENT_CHARACTERISTICS` |
| `diagnostics/build_path_dictionary.py` | one-iteration `for df in (coded,)`; duplicate `topic_value`/`format_value`; unused `pages_only`; a second identical event join; an obfuscated `inference_source` expression | dead or duplicated |
| `diagnostics/inventory.py` | `load_alphabetical`, `load_most_frequent` | never called |
| `diagnostics/replicate_activation_table.py` | `unit` parameter | unused |
| `diagnostics/treatment_effect_time.py` | `POWER` | unused (the z value is inlined) |
| `output/user_level_interim.xlsx` | stale file | nothing regenerates it; superseded by `user_level_dataset.xlsx` |

**Every CLI flag was tested by running with it and diffing.** All of them change
output. `--excel` and `--no-excel` change only which files are written, and
everything else changes data. None are vestigial. `TIMEZONE` is a constant that
changes only labels. The data is tz-naive and no conversion happens. It is kept,
and commented as a declaration rather than configuration.

**Also found:** the original Mac outputs and a fresh Linux run differed in **row
order** of `path_dictionary_extended.csv` and `discrepancy_log.csv`. The rows and
values were the same; ties in `value_counts()` and an unstable sort have no defined
order. Fixed in `18929d7` with path as the tie-break. No downstream data changed.

## 2. Documentation claims I could not reproduce

Mine are recomputed from `data/`. "Pre" means the pre-audit output, which the
claim described.

| claim (where) | theirs | mine |
|---|---|---|
| Workbook shape (README) | 2 sheets, 10,140 × 81 | 3 sheets, 10,140 × 98 pre-audit (99 now) |
| Variables that cannot be built (README) | 9 of 42 | 2 empty (`LEDocument`, `CDDocument`), 2 inferred |
| All-NULL columns (README) | 13 | 4 |
| Event coverage after inference (README) | 68–89% | 47–89% (`LoanTermsRelated` 47.4%) |
| Borrowers holding >1 loan (Read Me, codebook, DEC-V) | 7.2% / 1,577 | **4.5%** of borrowers in this file with a loan. 7.21% is all applicants, counted in the applicant file rather than the extract `loans_in_pilot` counts. |
| Borrowers with loans in different buckets (Read Me, DEC-V) | 1,180, "excluded by pilot_bucket_conflicting" | **567** in this file. 1,180 is all applicants. |
| `provided_language = es` in bucket 3 (Read Me, codebook, README, CLAUDE.md) | 280 | **171** in this file. 282 is the whole account file. |
| Spanish share by bucket (Read Me, CLAUDE.md) | 2.68 / 2.74 / 2.80% | 2.59 / 2.76 / 2.82% per loan; 3.27 / 2.60 / 2.57% per person. Neither reproduces. |
| Missing loans (Read Me, DEC-U) | "spread evenly", "not concentrated in one arm" | **False.** 1,243 (13.6%) of bucket 1 vs 541 / 548 (5.9%) of buckets 2 / 3 |
| "Bucket 1 never appears" (codebook) | — | 353 users in the file hold a bucket-1 loan. All of them also hold a 2/3 loan, so they are flagged conflicting. |
| Activation blanks (Read Me, codebook, DEC-Y) | "333 borrowers … no activation status recorded by the lender"; "333 of the borrowers on 471 blank loans appear in our clickstream" | 333 = **119** with a blank lender value + **214** with no loan at all. 122 of the 471 blank loans have an applicant in the clickstream. |
| Per-person activation, blanks as no → excluded (Read Me, DEC-Y) | 46.8% → 48.0%, "517 unknown" | 46.28% → 47.99%, with **1,209** persons unknown. 48.0 reproduces; 46.8 and 517 do not. |
| Language field agreement (codebook, DEC-Y) | 99.72% over 24,048 loans, 68 disagreements | 99.88% over 23,862, 28 disagreements (the method I could reconstruct) |
| Coapplicant flag vs hash counts (codebook, DEC-W) | 2,303 / 1,601 | 2,303 reproduces (email hashes) / **1,756** |
| Loans with >1 distinct user_hash (replicate docstring) | 4,666 | 4,439 |
| Row before a download (DEC-X) | `/translations/en` 7,942, `/Dashboard` 4,003 | 7,903 / 3,968 (pageview frame); 7,816 / 3,992 (raw log) |
| Loan type × content table "validates the path dictionary" (crossvalidate) | "~40x separation" | 34x (64.1 vs 1.9). It matches raw URL strings, so it validates the loan join only. |
| `/translations/en` preceded by `/Module/` (DEC-S) | 85.0% / 83.8% | 84.9% / 83.7% |
| "0 of 4,170 mp3 rows are a user's first row, so a parent always exists" (Phase2_Plan) | 0 orphans implied | **40** mp3 rows have no parent in their *session* |
| `Goal_to_*` "coded only on audio clips" (Project_Brief) | — | coded on 6 of the 26 page rows as well |
| Sum of `time_*` / total time (Phase2_Plan) | ≈3× | 1.41× pre-audit, 2.06× now |

**Reproduced exactly, and therefore not repeated above:** 337,581 rows, 10,140
users, 9,471 paths. 36.6% coded coverage. LOO 90.9% (169/186), via my own
re-implementation; note that a no-information baseline scores 74.4%. Pre-audit
sessions 34,436, median 95.8% classified, 1.41× overlap. 25,318 / 27,650 loans,
214 with no loan, 312 Spanish-preference borrowers (142 / 170). Bucket labels
perfectly diagonal over 25,318 loans. Lender activation vs `first_login`: 0
disagreements over 16,953 loans. 471 blank loans. **The paper's Table 2 to 0.00
pp in all 12 cells**, using the lender's own fields with blank activation excluded.
DEC-S counts 292 / 0 and 9,085 / 9,114, 117 of 119, and 114 toggle users. DEC-X
13,291 (75.9%), 14,439 (82.5%), 87.0% all-agree, 29.8 vs 17.7 days, 4,508
borrowers, 7,276 of 9,158 documents. DEC-K 132 users and 5,621 rows. DEC-T
258,622 s over 878 users. DEC-U 7 scores and 1 APR nulled, rate 53% null. Origination
68.9% vs 46.9%. Milestone medians and sign shares. 77,695 tied rows. 39,483 zero
gaps.

## 3. Validity problems, ranked by effect on a published result

| # | problem | effect | status |
|---|---|---|---|
| 1 | `/translations/en` counted as a pageview and credited dwell | Nine of the thirteen dictionary `time_*` columns were wrong (all except `GeneralFinancial`, `LenderMortgageProcessRelated`, `LoanTermsRelated` and `Goal_to_Advise`). `time_LoanEstimateRelated` was understated by 73%. `webpages_visited` +6.1%, `pct_pages_classified`, `zero_dwell_pages` | **Fixed** (`6007999`, DEC-Z). `--keep-translation-resources` reverts it. Needs confirmation from someone who knows the app. |
| 2 | `pages_C` / `unknown_C` on the parent page's flags for audio rows (spec var 32) | `pages_*` for up to 715 users per column; `pages_LoanTermsRelated` all zero; `pages_Video` +1,813 | **Fixed** (`51710f3`) |
| 3 | Loan extract missing 13.6% of bucket 1 vs 5.9% of buckets 2/3 | Any bucket-1 vs treatment outcome comparison | Not fixable here. Now stated in the Read Me, README and brief. |
| 4 | "LE download" is almost entirely a tie-break: pages coded both LE and CD count as LE (7,157 of 7,161 contexts) | Meaning of `pages_LEDownload` / `time_LEDownload` | Named constant, documented in the codebook, Read Me and DEC-Z. **Keeping these columns is the research team's decision.** |
| 5 | Milestone dates have no time of day | `t_activation_to_le_sent`: "99.8% negative" holds to the day only. 23.2% activated on the LE day, so their sign is unknowable. All timers carry up to ±1 day of error. | Documented in the Read Me, codebook and spec |
| 6 | Untyped downloads scored as 0 LE / 0 CD | 964 users had `pages_LEDownload = 0` with an unknown download | **Fixed**: `downloads_type_unknown` (`2ccbf97`) |
| 7 | Navigation rule sets every content flag to 0 on `/MyMortgage`, `/Dashboard`, etc. (DEC-H; 46.5% of events) | `Personalized = 0` on the dashboard is an assumption, not a coding. The dashboard is plausibly the most personalised page. | Not changed. Flagged for the professor (§5). |
| 8 | Read Me / codebook figures wrong for this file (§2) | Misreading by the person who only opens the workbook | **Fixed**: all computed in the run (`9615d66`) |
| 9 | Orphan audio rows (40) sent to "unknown" instead of their own flags (spec §5) | `time_*` for 1–6 users | **Fixed** (`51710f3`) |
| 10 | `pilot_bucket_conflicting` False for 5 users with no bucket | Filter logic | **Fixed** (`2ccbf97`) |
| 11 | Earliest-loan ties (9 users with two loans on the same first date) | Which loan's outcome is used | Documented (applicant-file order) |
| 12 | `time_X` is observed dwell. Each session's last page adds 0. | Spec-consistent, easily misread as total time on X | Documented |

**Robustness note:** the exploratory treatment comparison
(`treatment_effect_time.py`) uses session and total-time measures. Its results are
unchanged to within 1 s by fixes 1 and 2. The only interval excluding zero is still
the median-session-length difference-in-differences: +64 s, CI [13, 126].

## 4. Marked verified, and I think it is not

- **QA "audio time is credited once"** (`qa_phase2.md` §4) compared the largest
  `time_` column with total time, so it could not fail. Replaced with a probe that
  can (`time_LoanTermsRelated` must equal orphan-clip dwell: 1,503 s; it would be
  11,590 s if flags leaked).
- **`crossvalidate.py`** printed `PASS` as literal text for checks 1 and 4, and
  hard-coded check 5's results ("100.00% over 16,953 loans, zero disagreements",
  "333 borrowers"). All three are now computed.
- **"Validates the path dictionary"** (crossvalidate §1, commit `a6dec42`). It never
  reads the dictionary.
- **"Exact replication of Table 2 … the strongest available evidence that our
  measures mean what we think"** (DEC-Y). The replication reproduces, but it
  tabulates the lender's own activation and language fields, not anything this
  pipeline computes. It is circular as evidence for our measures. No code in the
  repo produced it until this audit (`replicate_activation_table.py` still computed
  the earlier 6-pp version).
- **DEC-X "Validated at 87.0% self-consistency … independent corroboration".** The
  87.0% is agreement between repeat downloads of one document, not accuracy. The
  LE side is the tie-break above.
- **"Filtering to `__prov == 'coded'` returns `beta_coding` exactly"** (README,
  CLAUDE.md, DEC-G). True cell for cell, but for the 100 of 103 coded paths seen in
  the log. One of those, `/Download/LoanDocument/346999` (professor's note "WE CANT
  TELL"), is reclassified by rule.
- **"20/20 verification checks pass"** (Project_Brief, Phase2_Plan). No artefact
  in the repo lists 20 checks, so I cannot say which checks these were.

## 5. Where I disagree with DECISIONS.md

- **DEC-S** is right about the evidence and stops one step short. A row the
  application emits on page load is not a pageview, for the same reason DEC-P
  gives for the favicon. Finished in DEC-Z.
- **DEC-U / DEC-V.** "Not concentrated in one arm" is wrong (13.6% vs 5.9%). DEC-V's
  1,577 / 7.2% / 1,180 come from all applicants, not from the borrowers it governs.
  Its claim that "those 1,180 are already excluded by `pilot_bucket_conflicting`"
  describes a column that holds 567.
- **DEC-X** describes the rule as "the most recent LE- or CD-related pageview" and
  never mentions that pages coded *both* resolve to LE. For this data that
  tie-break is the rule.
- **DEC-Y.** See §4 on circularity. The "333 … appear in our clickstream" count
  conflates no-loan users with blank-field users. The person-level 48.0% excludes
  persons by the *lender's* blank field and then scores them by *our*
  `first_login`, so it mixes two sources.
- **DEC-G.** The 90.9% LOO is real but rests on 186 cells from 22 page rows, with 6
  cells for `BorrowerMortgageProcessRelated`. Against a 74.4% no-information
  baseline the lift is about 16 points. `Goal_to_*` inference is untested.
- **DEC-H.** Setting `Personalized = 0` by rule on `/MyMortgage` and `/Dashboard`
  is a substantive coding of 26% of events, not a structural fact. It should go to
  the professor with the inferred codings.
- **Process.** DEC-N, -O, -P, -Q and -R are cited in code and in the codebook, but
  were never written. Their rationale survives only in `Phase2_Plan.md`.

## 6. Documentation contradictions and how each was resolved

| contradiction | resolution |
|---|---|
| README: 9 of 42 variables unbuildable, 13 all-NULL columns, 81 columns, 2 sheets | README rewritten to the current state; generated figures referenced, not copied |
| Project_Brief: Phase 2 "not started", milestone timers "blocked", loan applicants "contains no milestone dates" (true of that file, but read as "no source exists") | Brief rewritten: status, inputs (loan extract added), spec-var → column map, open decisions. Inherited defects kept as history. |
| Phase2_Plan "Proposed, not implemented" | Marked IMPLEMENTED / historical, with an as-built differences table. Body kept. |
| CLAUDE.md layout omits the loan extract and 4 diagnostics scripts; says 2 sheets | Layout updated |
| CLAUDE.md "README … now accurate" | Removed. The data > code > documents order is added above the document ranking. |
| CLAUDE.md QA: 11.48% Spanish mismatch "is a behavioural finding, not a defect" | Retracted, per DEC-S's own retraction |
| CLAUDE.md "five of six milestone timers" blocked | Corrected: all six built |
| CLAUDE.md / codebook / Read Me 280, 2.68/2.74/2.80% | Corrected to measured values, or computed in the run |
| Spec §3 language algorithm vs data (DEC-S) | Spec text untouched. An **AUDIT NOTICE** at the top plus inline **⚠ AUDIT NOTE**s at §1 inputs, var 16, vars 18–19, var 28 and vars 37–42. |
| Spec §1: `coding_dictionary` "drives all FIXED variables", milestone table "keyed by user" | Inline audit note (DEC-C; keyed by loan) |
| Spec var 39 order vs data (LE precedes activation for 76.6% by day) | Inline audit note |
| Read Me / codebook figures (§2) | Computed in the run |
| `treatment_effect_time.py` says no file holds loan outcomes | Corrected |
| DECISIONS.md | **Not edited.** DEC-Z appended. |

**Proposed for removal (not removed):**
- `diagnostics/inventory.py`'s *report*. It is a Phase-0 snapshot that predates
  the loan extract. Keep the script; consider labelling its output historical.
- `docs/Phase2_Plan.md` could be retired to a `docs/history/` folder once DEC-N–R
  are written into DECISIONS.md, since it is their only rationale today.

## 7. Code changes the data required

| commit | change | what changed in the output |
|---|---|---|
| `18929d7` | deterministic tie order | row order within ties only |
| `51710f3` | `pages_C`/`unknown_C` on own flags; orphan clips credited own flags | `pages_*`/`unknown_*`: up to 715 users per column. `pages_LoanTermsRelated` 0 → 296. `time_*` for 1–6 users. |
| `2ccbf97` | `downloads_type_unknown`; `pilot_bucket_conflicting` NULL when no bucket; tie-break named | +1 column (99), 5 cells NULL |
| `6007999` | `/translations/en` dropped as a page resource | see §3 #1. Sessions 34,436 → 34,403. Total time −0.2%. |
| `9615d66` | Read Me, codebook and QA figures computed; spec §8 checks added | text only |
| `05563bf` | crossvalidate / replicate / treatment diagnostics fixed | diagnostics only |
| `f8491d9` | `diagnostics/audit_recompute.py` | 0 mismatches on 61 columns now; 21,610 against the pre-audit output |
| `a852e65` | manifest text for LEDownload/CDDownload | text only |

**Population totals, pre-audit → now:** `webpages_visited` 332,906 → 313,684.
`time_LoanEstimateRelated` 244,474 → 910,227 s. `time_CDRelated` 1,030,435 →
2,008,035 s. `pages_unattributable` 19,581 → 359. Median `pct_pages_classified`
95.8% → 100%. Sum of `time_*` / total 1.41 → 2.06.

## What I did not verify

- The dictionary inference for `Goal_to_*`, which has no held-out cells.
- DEC-M's 195-row figure and the DEC-S noise-floor arithmetic (~40 events), both
  computed under non-default switching rules.
- That `/translations/en` is a page resource is an inference from the data. It is
  strong, but someone who knows the application should confirm it.
