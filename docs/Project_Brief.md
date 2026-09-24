# Mortgage Clickstream Project — Brief

**Companion to:** `Clickstream_Variable_Specification_v2.md` (full variable definitions)
**Status:** Inherited pipeline under review. Phase 1 code exists and is **partly
non-functional** — see Known defects. Input diagnostics are complete.
**Last verified against data:** 2026-09-14, by `diagnostics/inventory.py`,
`diagnostics/coverage.py`, `diagnostics/build_path_dictionary.py`.

---

## What this project does

Transform raw Talkument clickstream logs into a user-level behavioral dataset of
**42 variables**, then use those variables to analyze how mortgage borrowers
engage with disclosure and education content.

## Inputs

| File | Role | Key fields | Verified |
|---|---|---|---|
| `talkument_userinteractions.xlsx` | Event log — **337,581 rows**, 10,140 users, 9,471 distinct paths | `user_hash`, `eventdate`, `path` | ✅ as described |
| `Clickstream_path_frequencies_and_coding_scheme.xlsx` | URL classification | tab `beta_coding`, key `CODING SCHEME`, 103 coded paths | ✅ but see DEC-C |
| `talkument_useraccount.xlsx` | Account data — 21,263 rows | `first_login`, `last_login`, `expertise_level`, `provided_language` | ✅ — but no field named "activation"; `first_login` is the candidate |
| `talkument_loan_applicants.xlsx` | Loan milestones | `loannumber`, `applicant_email_hash`, `user_hash`, `language_preference`, `city`, `state`, `postalcode` | ❌ **contains no milestone dates at all** |
| `talkument_pilot_buckets.xlsx` | Pilot arm assignment — 27,650 rows, buckets {1,2,3} | `loan_number`, `bucket` | ⚠️ no `user_hash`; joins only via `loannumber` |

**The `loan_applicants` row above is the correction that matters most.** This brief
previously claimed the file carries `Application_Date`, `LE_TIL_Sent_Date`,
`Lock_Date` and `Current_Status_Date`. **None of those columns exist.** Variables
`t_application_to_activation`, `t_activation_to_le_sent`,
`t_le_sent_to_first_le_visit`, `t_activation_to_lock` and
`t_last_access_to_current_status` are blocked until a real source is located.

## Where things stand

| Phase | Variables | Status |
|---|---|---|
| 0 — input diagnostics | — | **Complete.** `diagnostics/output/inventory_report.md`, `coverage_report.md`, `dictionary_inference_report.md` |
| 0 — path dictionary | `Personalized` … `Goal_to_Advise` | **Complete.** Extended from 103 coded paths to all 9,471, with per-cell provenance. See DEC-G. |
| 1 — URL & path features | URL-level flags | **Rebuilt 2026-09-14.** 20/20 verification checks pass. Four DEC-F columns emitted NULL. |
| 2 — Sessions & durations | `time_on_page`, `session_*`, `pages_in_session` | Not started. **Unblocked** — plan mode required per CLAUDE.md. |
| 2 — Volume aggregations | `webpages_visited`, `unique_webpages_visited`, `pages_{Characteristic}`, … | Not started. Unblocked. |
| 2 — Milestone timers | `t_*` | **Blocked.** Source file has no milestone dates. |

Current output: `output/phase1_url_features.parquet` (337,581 rows, event grain,
every flag paired with a `__prov` provenance column). The inherited
`Processed_URL_Variables_1_to_21.xlsx` is superseded and should be deleted.

### Phase 2 preconditions, measured
- `eventdate` is **second-resolution**; 0 rows carry sub-second precision.
- **77,695 rows (23.0%)** share a timestamp with another row for the same user,
  and **39,483 consecutive gaps (11.7%) are exactly 0 seconds** — so `time_on_page`
  will be 0 for a large minority of rows. Decide whether a 0-second pageview is
  real before building duration aggregates.
- Under a 30-minute timeout there are **24,307** inter-event gaps over threshold,
  implying roughly **34,400 sessions** across 10,140 users.
- Median inter-event gap is **8 seconds**; p90 is 285 s; p99 is 16.8 days.

---

## Numbering: three incompatible schemes

The professor's sheet is unnumbered. The prior documentation and the repo each
invented their own ordering, and they disagree. `Audio` is **16** in the spec and
**14** in the repo; `Download` is **12** in the spec and **2** in the repo.

**Rule going forward:** reference variables **by name, never by number**, in all
code, commits, and discussion.

---

## Known defects in the inherited pipeline

### 1. `LEDocument`, `CDDocument`, `LEDownload`, `CDDownload` are identically zero

Every download event path in the log has exactly one shape:
`/Download/LoanDocument/{numeric id}`. **No download URL contains an `LE` or `CD`
token.** The inherited regexes match **0 of 337,581 rows**:

| regex | matching rows |
|---|---|
| `/Download/LoanDocument/.*LE` | 0 |
| `/Download/LoanDocument/.*CD` | 0 |

This brief previously recorded the opposite under Open Decisions — *"download URLs
appear type-identifiable … **Resolved by the repo**."* **That claim was wrong and
is withdrawn.** Spec §7-B is closed in the negative. Resolving these four variables
requires a LoanDocument-id → document-type lookup, which exists in no file in
`data/`. See **DEC-F**.

Separately, `CDDocument` is `0` on every non-null row of `beta_coding` and blank on
all 26 coded page rows — even with a document-type lookup, the dictionary
contributes nothing to it.

### 2. Dictionary coverage was never the number anyone assumed

Measured, before any remediation:

| | |
|---|---|
| distinct paths matched | 100 / 9,471 (1.06%) |
| **event rows matched** | **123,664 / 337,581 (36.63%)** |

But the raw match rate overstates per-variable coverage, because most flags are
**blank even on coded paths**:

| flag | events carrying any coded value |
|---|---|
| `MortgageRelated`, `ProcessRelated`, `CDRelated`, `Download`, `Audio`, `Video`, `Personalized`, `GeneralFinancial`, `LoanEstimateRelated` | 30.9% |
| `BorrowerMortgageProcessRelated`, `LenderMortgageProcessRelated` | 16.3% |
| `Goal_to_inform`, `Goal_to_Advise` | 12.6% |
| `LoanTermsRelated`, `CDDocument` | **0.04%** |

`LoanTermsRelated` and `CDDocument` are blank on **all 26** coded page rows. No
content page can receive either one from the dictionary.

The inherited pipeline defaults every unmapped path and every blank cell to `0`,
which encodes "we measured a no" where the truth is "we have no value" — the
silent default CLAUDE.md's Data hygiene rule 2 forbids. Addressed by DEC-G, DEC-H.

### 3. The dictionary carries no language information

`beta_coding` codes `English(Y/N)` = 1 and `Spanish (Y/N)` = 0 on **every single
row without exception**. Inheriting those columns labels every Spanish page in the
log as English. Language must come from the path and the per-user state machine.
See **DEC-I**.

### 4. `Audio` is built as a binary flag

Spec and `coding_dictionary` both describe a **count** of audio file links on the
page. Rebuild as an integer count; `AudioMp3` remains the binary.

### 5. `English_YN` / `Spanish_YN` are static, not stateful

Built as `English = NOT contains('/translations/es')`. The spec requires a
stateful per-user determination from activated language plus prior URLs.
`provided_language` is present in `talkument_useraccount.xlsx` with 100% coverage
({en: 20,981, es: 282}), so the state machine has a real validation target.

### 6. Entry point

The README names `03_final_merge.py` for configuration and
`clickstream_processor.py` for execution. `03_final_merge.py` does not exist.
`clickstream_processor.py` is the entry point.

---

## Other measured findings

- **The `alphabetical` frequency sheet double-counts.** Its `Frequency` column sums
  to 675,162 against a 337,581-row log — exactly 2.00× — while enumerating the same
  9,471 paths. Do not reconcile event counts against that sheet.
- **`first_login` precedes first pageview for all 10,008 comparable users**, median
  2 seconds, never after. It is a usable activation proxy, though the tail runs to
  −9,750,114 seconds. See CLAUDE.md's "Activation is defined twice".
- **`eventdate` is timezone-naive.** No milestone file survives to conflict with it,
  so the timezone question (§7-G) currently only affects calendar-day bucketing for
  `days_accessed`.
- **`pilot_buckets` cannot join on `user_hash`** — it has no such column. Bridging
  via `loan_number`/`loannumber` reaches 26,971 of 27,650 rows, but that bridge's
  semantic validity is unverified.
- **Navigation is 46.5% of the log.** `/MyMortgage` alone is 63,037 events (18.7%);
  `/favicon.ico` contributes 4,670 events that are not pageviews at all.

---

## Open decisions

| # | Question | Status | Owner |
|---|---|---|---|
| B | Are download URLs type-identifiable? | **CLOSED — no.** See defect 1, DEC-F | — |
| C | `beta_coding` vs `coding_dictionary` | **CLOSED** — different table shapes; `coding_dictionary` is a glossary and cannot be joined. DEC-C | — |
| D | Is an activated-language field available? | **CLOSED — yes**, `provided_language`, 100% coverage. DEC-I | — |
| E | `ProcessRelated` is marked `????` in the professor's own sheet | **OPEN** | **Professor** |
| — | Code the 28 remaining content paths (6 uncoded topics) | **OPEN** | **Professor** |
| — | Supply a LoanDocument-id → document-type lookup | **OPEN** | **Professor / data owner** |
| — | Locate the real milestone-date source | **OPEN** | **Professor / data owner** |
| A | Session timeout of 30 min is our assumption | Open — ours, sensitivity test | Us |
| G | Timezone for calendar-day bucketing | Open — ours, declare it | Us |

**CLAUDE.md conflict, unresolved:** its Classification-dictionary section states
`coding_dictionary` is authoritative and `beta_coding` must not be read. That is
structurally impossible — `coding_dictionary` has no per-path rows and no join
key. DEC-C records the interim reality; CLAUDE.md itself still needs the edit.

---

## What the professor needs to decide

Ranked by how much of the log each unblocks.

1. **A LoanDocument-id → document-type lookup** — unblocks four variables and
   17,502 download events.
2. **The real milestone-date source** — unblocks six variables outright.
3. **Code 6 uncoded topics** (28 paths, 8.45% of events): `application-documents-explained`,
   `closing-documents-overview-fixed`, `closing-documents-overview-va`,
   `credit-management`, `your-va-fixed-rate-loan`, the `your-conventional-arm-loan`
   family. Ready for him in `output/dictionary_review_for_professor.xlsx`.
4. **`ProcessRelated`** — the `????` (DEC-E).
5. **Confirm or overrule the inferred codings** — 183 paths, 15.0% of events,
   90.9% leave-one-out accuracy. Same workbook, `Inferred_pages` sheet.
6. **Are `LoanTermsRelated` and `Goal_to_*` meant to apply to pages at all?** They
   are coded only on audio clips.

---

## Next actions

1. ~~Run the coverage check.~~ **Done** — `diagnostics/output/coverage_report.md`.
2. ~~Verify the download regex.~~ **Done** — it matches nothing; §7-B closed no.
3. ~~Extend the dictionary.~~ **Done** — `output/path_dictionary_extended.csv`, DEC-G/H/I.
4. ~~Rebuild Phase 1.~~ **Done** — `clickstream_processor.py` rewritten;
   `output/phase1_url_features.parquet`, `variable_manifest.csv`,
   `discrepancy_log.csv`, `qa_phase1.md`, `phase1_before_after.md`. New decisions
   DEC-J (Audio), DEC-K (language seed), DEC-L (NULL vs 0), DEC-M (switch rule).
5. **Build Phase 2**: sessionization → volume aggregations → per-characteristic
   expansion. Plan mode required for sessionization and time-per-characteristic.
6. **Send the professor** `output/dictionary_review_for_professor.xlsx` with the
   four asks above.
7. Milestone timers stay blocked; emit the columns as NULL so the schema is stable.

---

## Note on validating the language fix

The reconciliation check `Spanish + English == total pages` **passes trivially** on
the current implementation, because English is computed as the complement of
Spanish. It is not evidence of correctness. Once the state machine is in place,
validate instead against `provided_language`: compare each user's modal computed
language to their account language, and report the mismatch count. With 282 `es`
accounts against 20,981 `en`, report the mismatch rate **within each language
group** — a pooled rate would be dominated by English and could not fail.
