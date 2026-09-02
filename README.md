### Proposed File Name

`README_Clickstream_Feature_Engineering.md`

---

# Mortgage Clickstream Feature Engineering Pipeline

## Project Overview

This repository contains the automated feature-engineering pipeline for processing raw clickstream interaction logs (`talkument_userinteractions.xlsx`) into behavioral variable sets. The pipeline is implemented via the `ClickstreamPath` Python class, utilizing a **config-driven architecture** that decouples business classification rules from python code execution.

The current implementation focuses on **Variables 1 through 21 (URL and Path-Based Behavioral Features)** to establish a foundational behavioral dataset prior to generating complex, timestamp-derived mathematical metrics.

---

## Configuration & Master Variables

The script relies on master variable constants defined at the top of `03_final_merge.py` to ensure dataset agility across changing input header conventions:

* **`USER_COL = 'user_hash'`**: Tracks unique user identification.
* **`TIME_COL = 'eventdate'`**: Tracks the interaction timestamp.
* **`URL_COL = 'path'`**: Tracks the target webpage or file path.
* **Mapping Source**: `Clickstream_path_frequencies_and_coding_scheme.xlsx` (specifically reading tab `beta_coding` on key `CODING SCHEME`).

---

## Active & Fully Functional Variables (Variables 1–21)

The pipeline actively processes and outputs **21 behavioral features**, divided across three computational tiers:

### 1. Static Dictionary Lookups (Variables 1–14)

*Engineered by executing an automated merge against the `beta_coding` classification sheet. Unmapped paths default to `0`.*

* **Var 1: Personalized**: Binary indicator (1/0) for user-customized modules.
* **Var 2: Download**: Binary indicator (1/0) for document download pages.
* **Var 3: LoanEstimateRelated**: Core modules and audio files explaining Loan Estimates.
* **Var 4: LoanTermsRelated**: Explanatory content related to loan terms and FAQs.
* **Var 5: LenderMortgageProcessRelated**: Infographics, process guides, and lender audio files.
* **Var 6: GeneralFinancial**: General budgeting, credit management, and financial health pages.
* **Var 7: Video**: Pages with embedded primary video content.
* **Var 8: CDRelated**: Closing Disclosure informational content and audio clips.
* **Var 9: Goal_to_Advise**: Interactive advisory modules (e.g., credit improvement).
* **Var 10: ProcessRelated**: Core application and workflow pages.
* **Var 11: BorrowerMortgageProcessRelated**: Consumer-facing process guides and audio clips.
* **Var 12: Goal_to_inform**: Educational content modules and term acceptance pages.
* **Var 13: MortgageRelated**: General mortgage informational pages, FAQs, and infographics.
* **Var 14: Audio**: Pages embedding media players or direct `.mp3` content.

### 2. Dynamic Pattern Matching (Variables 15–19)

*Engineered via runtime string detection and regular expressions.*

* **Var 15: English_YN (`English_YN`)**: Default `1`; evaluates to `0` if path contains `/translations/es`.
* **Var 16: LEDocument (`LEDocument`)**: Evaluates to `1` if path matches `r'/Download/LoanDocument/.*LE'`.
* **Var 17: CDDocument (`CDDocument`)**: Evaluates to `1` if path matches `r'/Download/LoanDocument/.*CD'`.
* **Var 18: AudioMp3 (`AudioMp3`)**: Evaluates to `1` if path ends directly with `.mp3`.
* **Var 19: Spanish_YN (`Spanish_YN`)**: Evaluates to `1` if path contains `/translations/es`.

### 3. Sequential Look-Back Logic (Variables 20–21)

*Engineered by grouping clicks by `user_hash` and evaluating immediate preceding user action using stateful row shifting (`shift(1)`).*

* **Var 20: LEDownload (`LEDownload`)**: Triggers `1` only if the current event is a `Download` action AND the immediately preceding page viewed by the user was `LoanEstimateRelated`.
* **Var 21: CDDownload (`CDDownload`)**: Triggers `1` only if the current event is a `Download` action AND the immediately preceding page viewed by the user was `CDRelated`.

---

## Pending & Deferred Variables (Variables 22–42)

The following calculated, mathematical, and multi-file metrics are **not yet implemented** in the active pipeline and are intentionally deferred to Phase 2:

### Session & Duration Calculations

* **Var 22**: Time spent on page ($Timestamp_{T+1} - Timestamp_T$).
* **Var 23**: Session start timestamp.
* **Var 24**: Session end timestamp (30-minute inactivity threshold).
* **Var 25**: Total session time duration.
* **Var 26**: Inter-session elapsed time ($SessionStart_{N} - SessionEnd_{N-1}$).
* **Var 33**: Total accumulated time spent grouped by webpage characteristic.
* **Var 35**: Total number of unique sessions per user.

### Aggregated Volume Metrics

* **Var 27**: Total webpages viewed per session.
* **Var 28**: Total overall webpages visited per user.
* **Var 29**: Total Spanish webpages visited per user.
* **Var 30**: Total English webpages visited per user.
* **Var 31**: Count of distinct/unique URL paths visited per user.
* **Var 32**: Aggregated visit count grouped by webpage characteristic.
* **Var 34**: Aggregate count of `.mp3` audio clips triggered per user.
* **Var 36**: Total distinct days account was accessed.

### External Milestone & Loan File Metrics

*(Requires joining external loan files: `talkument_loan_applicants.xlsx`, `talkument_useraccount.xlsx`)*

* **Var 37**: Total time elapsed from account activation to final account access.
* **Var 38**: Time elapsed from `Application_Date` to account activation.
* **Var 39**: Time elapsed from account activation to `LE_TIL_Sent_Date`.
* **Var 40**: Time elapsed from `LE_TIL_Sent_Date` to first LE page visit.
* **Var 41**: Time elapsed from account activation to `Lock_Date`.
* **Var 42**: Time elapsed from final account access to `Current_Status_Date`.

---

## Execution Instructions

```bash
python3 clickstream_processor.py

```

**Output**: Produces `Processed_URL_Variables_1_to_21.xlsx` containing all 337,581 raw user events enriched with the 21 engineered behavioral feature columns.