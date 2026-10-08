# Mortgage Clickstream Project — Brief

**Companion to:** `Clickstream_Variable_Specification_v2.md` (full variable definitions)
**Status, 2026-10-08:** Phase 0 (diagnostics), Phase 1 (URL features) and Phase 2
(sessions, aggregates, loan join, user-level dataset) are **built and running**.
The deliverable is `output/user_level_dataset.xlsx`. An independent audit
(`docs/AUDIT_REPORT_2026-10-01.md`) corrected defects in Phase 2; on 2026-10-08
every variable was taken to full coverage without overriding the professor's
coding (DEC-AA to DEC-AF).
**Last verified against data:** 2026-10-08, by `diagnostics/audit_recompute.py`,
`diagnostics/crossvalidate.py` and `diagnostics/replicate_activation_table.py`.

> This brief previously said the inherited pipeline was "under review", Phase 2
> was "not started", and the milestone timers were "blocked" because no file held
> milestone dates. All three stopped being true when Phase 2 was built and
> `data/loan_application_data_partial.csv` arrived. The history is in
> `docs/DECISIONS.md` (DEC-F, DEC-U, DEC-V).

---

## What this project does

Transform raw Talkument clickstream logs into a user-level behavioural dataset of
the specification's **42 variables**, then use those variables to analyse how
mortgage borrowers engage with disclosure and education content.

## Inputs

| File | Role | Key fields | Verified |
|---|---|---|---|
| `talkument_userinteractions.xlsx` | Event log: **337,581 rows**, 10,140 users, 9,471 distinct paths | `user_hash`, `eventdate`, `path` | ✅ All timestamps parse. Within each user, file order is already chronological. |
| `Clickstream_path_frequencies_and_coding_scheme.xlsx` | URL classification: `beta_coding`, 103 coded path keys, 100 of them seen in the log | `CODING SCHEME` | ✅ see DEC-C |
| `talkument_useraccount.xlsx` | Accounts: 21,263 rows | `first_login`, `provided_language`, `expertise_level` | ✅ 132 log users have no account row (DEC-K) |
| `talkument_loan_applicants.xlsx` | The bridge from user to loan: 35,176 rows, 26,971 loans | `loannumber`, `user_hash`, `language_preference` | ✅ It holds **no milestone dates**. Those come from the loan extract. |
| `talkument_pilot_buckets.xlsx` | Bucket per loan: 27,650 loans | `loan_number`, `bucket` ∈ {1,2,3} | ✅ No `user_hash`. It joins via `loannumber`, and the bridge is validated (below). |
| `loan_application_data_partial.csv` | Loan extract: 25,318 loans (+39 blank trailing rows) | `Loan_Status`, `Application_Date`, `LE_TIL_Sent_Date`, `Lock_Date`, `Current_Status_Date`, `bucket`, `Activated_Talkument`, `Borrower_Language_Preference`, `Coapplicant`, rate/APR/credit | ✅ All four dates parse at 100% (DEC-U). Every milestone is a **calendar date with no time of day**. |

**The buckets are confirmed.** The loan extract names them: 1 = `no talkument`,
2 = `talkument` (English), 3 = `talkument_multi` (multilingual). Across all 25,318
loans the extract's labels agree with `pilot_buckets` with 0 disagreements.

**The extract is partial, and unevenly so.** It holds 91.6% of pilot loans. The
2,332 missing loans are 13.6% of bucket 1 against 5.9% of buckets 2 and 3. Any
outcome comparison between bucket 1 and the treatment buckets rests on a less
complete control group.

## Where things stand

| Phase | Variables | Status |
|---|---|---|
| 0 — input diagnostics | — | Complete. `diagnostics/output/inventory_report.md`, `coverage_report.md`, `dictionary_inference_report.md` |
| 0 — path dictionary | `Personalized` … `Goal_to_Advise` | Complete. The professor's coding, his blanks filled from `docs/page_template_coding.csv`, provenance on every cell (DEC-AD). |
| 1 — URL & path features | URL-level flags, language state | Built. `output/phase1_url_features.parquet`, `qa_phase1.md` |
| 2 — sessions & durations | `time_on_page`, `session_*`, `pages_in_session` | Built at 30 min (DEC-N). Test accounts, browser assets and the `/translations/*` language files are dropped first (DEC-AA, DEC-P, DEC-Z, DEC-AB). |
| 2 — volume & per-characteristic | `webpages_visited` … `pages_*`, `time_*`, `unknown_*` | Built. `pages_*` counts a row's own flags (spec var 32). `time_*` credits clip time to the parent page (var 33). |
| 2 — milestone timers | all six `t_*` | Built from the loan extract. Computed from each borrower's earliest loan (DEC-V). |
| 2 — download type | `LEDownload`, `CDDownload` | Built per download from the page it was clicked from, the same document elsewhere, or number order; each records which (DEC-AC). |
| 2 — document type | `LEDocument`, `CDDocument` | Built: the page shows the borrower's own LE / CD (DEC-AC). |

Deliverable: `output/user_level_dataset.xlsx`, with the sheets **Read Me First**,
**User Data** (10,138 × 106) and **Data Dictionary** (one row per column).

### Spec variables → output

| spec vars | where |
|---|---|
| 1–12, 15–21 (URL flags, language) | event grain: `phase1_url_features.parquet`, `phase2_events.parquet` |
| 13–14 `LEDownload`/`CDDownload` | event grain. User grain: `pages_`/`time_LEDownload`, `…CDDownload`, `downloads_typed_by_*`, `downloads_type_unknown` |
| 16 `Audio` | event grain only: the count of clips that play on the page (DEC-AE). |
| 22–24 dwell, session start/end | `phase2_events.parquet` |
| 25–27 | `phase2_sessions.parquet` |
| 28–31, 34–36 | `webpages_visited`, `spanish_`/`english_webpages_visited`, `unique_webpages_visited`, `audio_clips_clicked`, `num_sessions`, `days_accessed` |
| 32–33 | 18 `pages_*` and 18 `time_*` columns, with `unknown_*` beside them. |
| 37–42 | `t_activation_to_last_access`, `t_application_to_activation`, `t_activation_to_le_sent`, `t_le_sent_to_first_le_visit`, `t_activation_to_lock`, `t_last_access_to_current_status`; `milestone_blank_reason` (DEC-AF) |

---

## Numbering: three incompatible schemes

The professor's sheet is unnumbered. The prior documentation and the repo each
invented their own ordering, and they disagree: `Audio` is **16** in the spec and
**14** in the repo. **Refer to variables by name, never by number.**

---

## Defects in the inherited pipeline (historical, all fixed in Phase 1)

1. **Download document type.** Every download path is
   `/Download/LoanDocument/{numeric id}`. The inherited regexes matched 0 of 337,581
   rows, so four columns were identically zero. Now NULL, or inferred (DEC-F, DEC-X).
2. **Coverage.** `beta_coding` matches 100 of 9,471 paths, which is 36.6% of
   events. The inherited code filled every gap with 0. Now NULL plus a
   discrepancy log (DEC-L), and extended by inference (DEC-G).
3. **No language information in the dictionary.** `English(Y/N)` is 1 and
   `Spanish (Y/N)` is 0 on every row. Language now comes from a per-user state
   machine (DEC-I).
4. **`Audio` built as binary.** The spec defines a count, but no input supplies
   one (DEC-J).
5. **Static `English_YN`/`Spanish_YN`.** Replaced by the stateful rule (DEC-K, DEC-S).
6. **Entry point.** `03_final_merge.py` never existed.

---

## Measured findings worth knowing

- **The `alphabetical` frequency sheet double-counts.** It sums to 675,162 against a
  337,581-row log (2.00×). Do not reconcile event counts against it.
- **`first_login` precedes the first pageview** for all 10,008 comparable users, by
  a median of 2 s. The tail runs to −9,750,114 s.
- **Navigation is 46.5% of the log.** `/MyMortgage` alone is 63,037 events (18.7%).
- **`/translations/en` is a page resource, not a page.** There are 19,222 events,
  emitted at the same rate in bucket 2, which has no toggle, as in bucket 3.
  Counted as a pageview it absorbed 8.9% of all dwell (DEC-S, DEC-Z).
- **The Loan Estimate usually precedes activation, but only to the day.** The LE
  came on an earlier calendar day for 76.6% of borrowers and on the same day for
  23.2%, where the order is unknowable.
- **Selection.** 68.9% of borrowers in the user file originated, against 46.9% of
  all loans in the extract. This is selection, not a treatment effect.
- **The lender's activation flag** agrees with `first_login` on all 16,953 loans
  where both exist. 471 Talkuments-bucket loans have no lender value.

---

## Open decisions

| # | Question | Status | Owner |
|---|---|---|---|
| B | Are download URLs type-identifiable? | **Closed — no** (DEC-F); typed per download from evidence instead (DEC-AC) | — |
| C | `beta_coding` vs `coding_dictionary` | **Closed** (DEC-C) | — |
| D | Is an activated-language field available? | **Closed — yes**, `provided_language`, but it encodes the treatment (DEC-S) | — |
| E | `ProcessRelated` is marked `????` in the professor's own sheet | **Open**; his values kept | Professor |
| — | Confirm the cells coded by us (DEC-AD) and Audio as a count (DEC-AE) | **Open** — `docs/Professor_Questions.md` | Professor |
| — | Optional LoanDocument-id → type lookup | Open, no longer blocking | Data owner |
| — | The missing 2,332 loans, concentrated in bucket 1 | **Open** | Data owner |
| A | Session timeout of 30 min is our assumption | Decided (DEC-N). Sensitivity is regenerated every run. | Us |
| G | Timezone for calendar-day bucketing | Decided (DEC-R). The data is naive. | Us |
| — | Test accounts, language, download type, blanks | Decided (DEC-AA, DEC-AB, DEC-AC, DEC-AF) | Research team |

## What the professor needs to decide

See `docs/Professor_Questions.md`. In short: confirm or correct the cells coded by
us (each page in `output/dictionary_review_for_professor.xlsx`), rule on
`ProcessRelated`, and on the few places where his own coding is inconsistent or
where Audio as a count differs from his 0/1.
