# Mortgage Clickstream Project — Brief

**Companion to:** `Clickstream_Variable_Specification_v2.md` (full variable definitions)
**Status:** Inherited pipeline under review. Phase 1 code exists; correctness not yet verified.

---

## What this project does

Transform raw Talkument clickstream logs into a user-level behavioral dataset of **42 variables**, then use those variables to analyze how mortgage borrowers engage with disclosure and education content.

## Inputs

| File | Role | Key fields |
|---|---|---|
| `talkument_userinteractions.xlsx` | Event log — **337,581 rows** | `user_hash`, `eventdate`, `path` |
| `Clickstream_path_frequencies_and_coding_scheme.xlsx` | URL classification | tab `beta_coding`, key `CODING SCHEME` |
| `talkument_loan_applicants.xlsx` | Loan milestones | `Application_Date`, `LE_TIL_Sent_Date`, `Lock_Date`, `Current_Status_Date` |
| `talkument_useraccount.xlsx` | Account data | activation, activated language |

## Where things stand

| Phase | Variables | Status |
|---|---|---|
| 1 — URL & path features | 1–21 | Built. **Under correctness review.** |
| 2 — Sessions & durations | 22–27, 33, 35 | Not started |
| 2 — Volume aggregations | 28–32, 34, 36 | Not started |
| 2 — Milestone timers | 37–42 | Not started. Needs external file joins. |

Current output: `Processed_URL_Variables_1_to_21.xlsx`, 337,581 rows × 21 feature columns.

---

## Numbering: three incompatible schemes

The professor's sheet is unnumbered. The prior documentation and the repo each invented their own ordering, and they disagree. `Audio` is **16** in the spec and **14** in the repo; `Download` is **12** in the spec and **2** in the repo.

**Rule going forward:** the v2 spec numbering (professor's sheet order) is canonical. Reference variables **by name, not number**, in all code, commits, and discussion until the repo is renumbered.

---

## Known defects in the inherited pipeline

Verified against the professor's spec. All four affect variables already marked "fully functional."

| Variable | Built as | Should be | Impact |
|---|---|---|---|
| `Audio` | Binary flag | **Count** of mp3 links on the page | Loses link-density signal; spec says "number of audio file links" |
| `English_YN` / `Spanish_YN` | Static: `English = NOT /translations/es` | **Stateful** per user from activated language + prior URLs | Misclassifies most pages viewed by Spanish-language users |
| `LEDownload` / `CDDownload` | `shift(1)` on immediate prior row | Row-level `Download AND LEDocument` | Fails on *LE page → mp3 → download*; no session bound |
| `LEDocument` / `CDDocument` | Regex (dynamic) | Marked *hard coded* in professor's sheet | Method conflicts with spec; regex is unanchored |

Two further gaps: **unmapped paths silently default to 0** with no discrepancy log — coverage of a ~103-path dictionary against 337,581 events is currently unknown and could be very low. And the README's config location (`03_final_merge.py`) does not match its run command (`clickstream_processor.py`), so the actual entry point needs confirming.

---

## Open decisions

| # | Question | Blocks | Owner |
|---|---|---|---|
| E | `ProcessRelated` is marked `????` in the professor's own sheet; inherited path counts are internally contradictory | Var 4 | **Professor** |
| C | `beta_coding` vs `coding_dictionary` — repo uses `beta_coding`; is it authoritative or supplementary? | Vars 1–21 | Professor |
| D | Is an activated-language field present in `talkument_useraccount.xlsx`? | Vars 18–19, 29–30 | Data check |
| A | Session timeout of 30 min is our assumption, not in the spec | Vars 22–27, 35 | Us — sensitivity test |
| G | Timezone for calendar-day bucketing | Var 36 | Us — declare |

**Resolved by the repo:** download URLs appear type-identifiable (`.../LoanDocument/...LE`), so the row-level definition of `LEDownload`/`CDDownload` is buildable. Verify the regex fires as intended before closing.

---

## Next actions

1. **Run the coverage check.** Distinct paths in the event log vs `beta_coding`. Match rate and top unmapped paths by volume. This determines whether variables 1–14 are usable at all — do it before writing any Phase 2 code.
2. **Verify the download regex** against real paths; confirm §7-B closed.
3. **Fix the four defects** in Phase 1 and re-run.
4. **Ask the professor** about `ProcessRelated` and the `beta_coding` question — these are the only items we cannot decide ourselves.
5. **Build Phase 2** per the v2 spec, in order: sessionization → volume aggregations → milestone joins.

---

## Note on validating the language fix

The reconciliation check `Spanish + English == total pages` **passes trivially** on the current implementation, because English is computed as the complement of Spanish. It is not evidence of correctness. Once the state machine is in place, validate instead against the activated-language field: compare each user's modal computed language to their account language, and report mismatches.
