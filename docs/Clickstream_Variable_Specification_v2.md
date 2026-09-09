# Clickstream Variable Specification (v2)

**Status:** Working draft, supersedes the inherited *Clickstream Tracking & Variable Documentation*.
**Source of truth for variable definitions:** professor-supplied variable list.
**Source of truth for URL classification:** `Clickstream_path_frequencies_and_coding_scheme.xlsx`, sheets `coding_dictionary` and `beta_coding`.

---

## 0. How to read this document

Every variable is tagged with its **origin**:

| Tag | Meaning | May we change it? |
|---|---|---|
| **[FIXED]** | Marked *hard coded* in the professor's sheet. Definition and URL membership come from `coding_dictionary`. | No. Read from the dictionary at runtime. |
| **[OURS]** | Marked *calculated* in the professor's sheet. Methodology is our design decision. | Yes — but every choice must be recorded in §7. |
| **[OPEN]** | Underspecified in the source. Blocked pending a decision. | Must be resolved before implementation. |

Numbering below is **canonical and frozen**. It follows the order of the professor's sheet. The inherited document used a different numbering that reordered variables by implementation method, duplicated IDs 27 and 28, and skipped 35 and 36 — do not cross-reference against it.

---

## 1. Inputs, grain, and outputs

### Required inputs

| Input | Contents | Notes |
|---|---|---|
| Clickstream event log | `user_id`, `timestamp`, `url_path` | ~100k+ rows. One row per pageview/action. |
| `coding_dictionary` | Path → characteristic flags | ~103 baseline paths. Drives all [FIXED] variables. |
| `beta_coding` | Supplementary coding | Reconcile against `coding_dictionary`; document any conflicts. |
| Loan milestone table | `Application_Date`, `LE_TIL_Sent_Date`, `Lock_Date`, `Current_Status_Date` | Keyed by user. Drives vars 38–42. |
| Activated-language field | Per-user language at account activation | Needed for vars 18–19. See §7-D if unavailable. |
| Document ID → type map | Document ID → {LE, CD} | Needed for vars 10–11 **if** download URLs are ID-based. See §7-B. |

### Output grain

Produce **three tables**, not one. Mixing grains is the most common source of double-counted totals.

| Table | Key | Variables |
|---|---|---|
| `url_level` | `user_id` × `event_seq` | 1–24 |
| `session_level` | `user_id` × `session_id` | 25–27 |
| `user_level` | `user_id` | 28–42 |

### Global processing conventions

These apply to every sequential variable and must be applied **before** any variable is computed.

1. **Sort key:** `user_id`, then `timestamp` ascending, then a stable tiebreaker (original file row order). Every order-dependent variable (18, 19, 22, 23, 24, 33, and the fallback form of 13/14) is undefined without this. Never rely on incidental file order.
2. **Ties:** identical timestamps for one user are resolved by original row order. Do not drop them.
3. **Duplicate consecutive URLs** (page refreshes) are retained as distinct pageviews by default. See §7-F.
4. **Session inactivity threshold:** `SESSION_TIMEOUT = 30 minutes`. Declare as a named parameter, never inline. This value is **not** in the professor's spec — it is our assumption. See §7-A.
5. **Unmapped URLs:** any URL in the event log with no match in `coding_dictionary` receives `0` for all [FIXED] flags **and** is written to a discrepancy log. Never silently drop.

---

## 2. URL-level variables — hardcoded flags (1–12, 15–17, 20–21)

All are read from `coding_dictionary`. **Do not hardcode path lists or path counts into code**, and do not restate counts in documentation — they drift the moment the dictionary is revised. Join to the dictionary at runtime.

These flags are **not mutually exclusive**. A single URL routinely fires many at once (an FAQ mp3 may be `AudioMp3`, `LoanTermsRelated`, and `MortgageRelated` simultaneously). They are tags, not categories. This is intentional and materially affects the interpretation of vars 32–33.

| # | Variable | Type | Definition |
|---|---|---|---|
| 1 | `Personalized` | binary | Webpage includes personalized information. |
| 2 | `GeneralFinancial` | binary | Webpage includes general financial information. |
| 3 | `MortgageRelated` | binary | Webpage includes mortgage product-related information. |
| 4 | `ProcessRelated` | binary | Webpage includes mortgage process-related information. **[OPEN]** — see §7-E. |
| 5 | `BorrowerMortgageProcessRelated` | binary | Process information, consumer side. |
| 6 | `LenderMortgageProcessRelated` | binary | Process information, lender (non-consumer) side. |
| 7 | `LoanTermsRelated` | binary | Webpage includes loan terms information. |
| 8 | `LoanEstimateRelated` | binary | Webpage includes Loan Estimate information. |
| 9 | `CDRelated` | binary | Webpage includes Closing Disclosure information. |
| 10 | `LEDocument` | binary | Webpage **is** an LE document. See §7-B. |
| 11 | `CDDocument` | binary | Webpage **is** a CD document. See §7-B. |
| 12 | `Download` | binary | Webpage is a downloaded document. |
| 15 | `AudioMp3` | binary | Webpage **is** an mp3 file. |
| 16 | `Audio` | **integer count** | **Number of mp3 links present on the webpage.** |
| 17 | `Video` | binary | Webpage includes a video. |
| 20 | `Goal_to_inform` | binary | Webpage goal is to inform. |
| 21 | `Goal_to_Advise` | binary | Webpage goal is to give advice. |

> **Correction to inherited doc — var 16.** `Audio` was previously implemented as a binary flag firing on every mp3 path. The professor's spec defines it as *"Number of audio file (mp3) links on the webpage"* — a **count**, describing how many audio links a page offers. `AudioMp3` (var 15) is the binary "this row is an mp3." Var 34 confirms the distinction: audio clips clicked is the sum of `AudioMp3`, not of `Audio`. Building `Audio` as a flag destroys the difference between a page with one audio link and a page with twelve.

### Validation pass (required)

The dictionary is a static ~103-path list; the production site emits dynamic URLs. Run pattern matching **as a check, not as the definition**:

- URLs ending `.mp3` → compare to `AudioMp3` from the dictionary.
- URLs containing `/Download/LoanDocument/` → compare to `Download`.
- URLs containing `/translations/es` or `/translations/en` → compare to language state (§3).

Where the pattern fires but the dictionary does not, the URL is dictionary-uncovered. Write it to the discrepancy log with a hit count and review before deciding whether to extend the dictionary. **Do not let the pattern silently override the dictionary** — that would put us in conflict with the professor's fixed coding scheme.

---

## 3. URL-level variables — calculated (13, 14, 18, 19, 22, 23, 24)

### 13–14. `LEDownload`, `CDDownload` **[OURS]**

Source formula is truncated in the sheet (`= if Download=1 &`).

**Primary definition — adopt this if the data supports it:**

```
LEDownload = (Download == 1) AND (LEDocument == 1)
CDDownload = (Download == 1) AND (CDDocument == 1)
```

Row-level, order-independent, no lag required. Matches the spec's wording ("Webpage **is** a downloaded LE document" — a property of the row) and matches the shape of the truncated formula.

**Precondition:** inspect every distinct URL where `Download == 1`. If the document type is recoverable — either the path differs between LE and CD, or the document ID resolves via the ID→type map — use the primary definition.

**Fallback — only if download URLs are type-opaque:**

```
LEDownload = (Download == 1) AND (most recent LoanEstimateRelated or CDRelated
             pageview within the same session was LoanEstimateRelated)
```

Symmetrically for `CDDownload`. Note this is **session-bounded** and uses the *most recent* qualifying page, not the immediately preceding row. The inherited implementation used the immediately preceding row, which misclassifies the ordinary sequence *view LE page → play an mp3 explainer → download*: the prior row is an mp3, so the download is assigned to neither type. It also had no session bound, so a two-day-old LE view could leak forward onto an unrelated download.

If the fallback is used, add a `download_type_inferred` flag to the output so downstream analysis can distinguish measured from inferred classification.

### 18–19. `English (Y/N)`, `Spanish (Y/N)` **[OURS]**

The professor's sheet marks both as *calculated* and specifies they are *"a function of activated language and prior URLs."* These are **stateful per user**, not static properties of a URL.

```
Algorithm (per user, in sort order):
  1. Initialize language_state := activated language for the user.
  2. For each row in chronological order:
       if url contains "/translations/en":  language_state := EN
       if url contains "/translations/es":  language_state := ES
       English := (language_state == EN)
       Spanish := (language_state == ES)
  3. Forward-fill: rows between switches inherit the prevailing state.
```

Properties: the two are **mutually exclusive and exhaustive**, so `var29 + var30 == var28` exactly. This is a hard QA check (§8).

> **Correction to inherited doc — vars 18/19.** Previously, all baseline dictionary paths were defaulted to English and Spanish was a substring match on `/translations/es`. That misclassifies every page a Spanish-language user views whose path does not itself carry `/es` — likely the large majority of their session — and breaks the reconciliation above.

**If no activated-language field exists,** see §7-D.

### 22. `Time spent on page` **[OURS]**

```
time_on_page[T] = timestamp[T+1] - timestamp[T]     (same user, same session)
time_on_page[last row of session] = NULL
```

The final row of a session has no successor and its duration is unobservable. Use `NULL`, not `0`, and exclude from means. This choice makes session duration internally consistent: summing `time_on_page` across a session (treating the trailing NULL as contributing nothing) equals `session_end - session_start` exactly.

Emit units consistently (recommend: seconds, integer) across all duration variables.

### 23–24. `Session start`, `Session end` **[OURS]**

Per the sheet these are **boundary tests, not timestamps**:

```
Session start[T] = (timestamp[T] - timestamp[T-1]) > SESSION_TIMEOUT
Session end[T]   = (timestamp[T+1] - timestamp[T]) > SESSION_TIMEOUT
```

Boundary conditions, which the source does not state and which must be handled explicitly:

- A user's **first** row is always `Session start = 1` (no predecessor).
- A user's **last** row is always `Session end = 1` (no successor).

Implementation: flag boundaries → cumulative sum of `Session start` within user → `session_id`. Derive session start/end *timestamps* from the min/max within each `session_id`, and keep them as separate fields from the boolean flags so downstream code cannot confuse the two.

---

## 4. Session-level variables (25–27)

| # | Variable | Definition |
|---|---|---|
| 25 | `Session time duration` | `max(timestamp) - min(timestamp)` within `session_id`. Equivalently, the sum of `time_on_page` over the session. |
| 26 | `Inter-session elapsed time` | `session_start[n] - session_end[n-1]`, same user. `NULL` for a user's first session. |
| 27 | `Webpages viewed in session` | Row count within `session_id`. |

A single-pageview session has duration `0`, not `NULL`. Report the count of these separately — a high share indicates the timeout parameter may need revisiting (§7-A).

---

## 5. User-level variables (28–42)

### Volume aggregations

| # | Variable | Definition |
|---|---|---|
| 28 | `Webpages visited` | Total row count for the user. |
| 29 | `Spanish webpages visited` | `sum(Spanish)`. |
| 30 | `English webpages visited` | `sum(English)`. |
| 31 | `Unique webpages visited` | Count of distinct `url_path`. |
| 34 | `Audio clips clicked` | `sum(AudioMp3)` — per the spec, explicitly the sum of var 15, **not** var 16. |
| 35 | `Number of sessions` | Count of distinct `session_id`. |
| 36 | `Days account accessed` | Count of distinct calendar dates with ≥1 event. **Declare the timezone** used for date bucketing. |

### 32. `Webpages visited per characteristic` **[OURS — schema-expanding]**

This is **not one column.** It expands to one column per binary characteristic:

```
pages_{characteristic} = sum(flag) over the user's rows
```

Applied to vars 1–15, 17, 20, 21 → **18 columns**. Naming convention: `pages_Personalized`, `pages_GeneralFinancial`, … `Audio` (16) is excluded — it is a count, not a flag. Language characteristics are already covered by vars 29–30.

### 33. `Total time per characteristic` **[OURS — schema-expanding]**

Also 18 columns, `time_{characteristic}`, matching var 32's naming.

**Audio time is attributed to the parent page.** The professor's note is explicit: *"including all audio mp3 time spent since it is associated with that page."*

```
parent_page[T] := the most recent non-mp3 pageview in the same session, at or before T

For each characteristic C:
  time_C = SUM( time_on_page[T] for rows where AudioMp3[T]==0 and C[T]==1 )
         + SUM( time_on_page[T] for rows where AudioMp3[T]==1 and C[parent_page[T]]==1 )
```

Note the two clauses partition the rows: an mp3 row's duration is credited **only** to its parent's characteristics, never additionally to its own dictionary flags. Attributing both would double-count. If an mp3 row has no parent in its session (session opens on an mp3), credit its own flags and log the case.

> **Correction to inherited doc — var 33.** The inherited version made no mention of audio-time attribution, which understates the total time for every content category that hosts audio.

### External milestone timers

All are **signed durations**. Emit `NULL`, not `0`, when the milestone date is missing for a user, and report missingness rates per variable.

| # | Variable | Definition |
|---|---|---|
| 37 | `Activation → last access` | `last pageview timestamp - first pageview timestamp`. ("Activation" = first Talkuments access throughout.) |
| 38 | `Application_Date → activation` | `first pageview timestamp - Application_Date`. |
| 39 | `Activation → LE_TIL_Sent_Date` | `LE_TIL_Sent_Date - first pageview timestamp`. |
| 40 | `LE_TIL_Sent_Date → first LE visit` | `min(timestamp where LoanEstimateRelated==1 AND timestamp >= LE_TIL_Sent_Date) - LE_TIL_Sent_Date`. |
| 41 | `Activation → Lock_Date` | `Lock_Date - first pageview timestamp`. |
| 42 | `Last access → Current_Status_Date` | `Current_Status_Date - last pageview timestamp`. |

> **Correction to inherited doc — var 40.** The inherited definition used the first LE-related visit outright. Users who browsed LE content *before* their LE was issued would produce negative elapsed times. The visit must be constrained to occur on or after `LE_TIL_Sent_Date`. Users with LE-related activity only before the send date get `NULL`, and their count should be reported — it is itself an interesting behavioral finding.

Vars 38–42 can legitimately be negative or null depending on real-world sequencing (e.g. a lock before first access). Do not clip to zero. Report the sign distribution for each.

---

## 6. Full variable index

| # | Variable | Level | Origin |
|---|---|---|---|
| 1 | Personalized | URL | FIXED |
| 2 | GeneralFinancial | URL | FIXED |
| 3 | MortgageRelated | URL | FIXED |
| 4 | ProcessRelated | URL | FIXED / **OPEN** |
| 5 | BorrowerMortgageProcessRelated | URL | FIXED |
| 6 | LenderMortgageProcessRelated | URL | FIXED |
| 7 | LoanTermsRelated | URL | FIXED |
| 8 | LoanEstimateRelated | URL | FIXED |
| 9 | CDRelated | URL | FIXED |
| 10 | LEDocument | URL | FIXED |
| 11 | CDDocument | URL | FIXED |
| 12 | Download | URL | FIXED |
| 13 | LEDownload | URL | OURS / **OPEN** |
| 14 | CDDownload | URL | OURS / **OPEN** |
| 15 | AudioMp3 | URL | FIXED |
| 16 | Audio *(count)* | URL | FIXED |
| 17 | Video | URL | FIXED |
| 18 | English (Y/N) | URL | OURS |
| 19 | Spanish (Y/N) | URL | OURS |
| 20 | Goal_to_inform | URL | FIXED |
| 21 | Goal_to_Advise | URL | FIXED |
| 22 | Time spent on page | URL | OURS |
| 23 | Session start | URL | OURS |
| 24 | Session end | URL | OURS |
| 25 | Session time duration | session | OURS |
| 26 | Inter-session elapsed time | session | OURS |
| 27 | Webpages viewed in session | session | OURS |
| 28 | Webpages visited | user | OURS |
| 29 | Spanish webpages visited | user | OURS |
| 30 | English webpages visited | user | OURS |
| 31 | Unique webpages visited | user | OURS |
| 32 | Webpages per characteristic *(18 cols)* | user | OURS |
| 33 | Total time per characteristic *(18 cols)* | user | OURS |
| 34 | Audio clips clicked | user | OURS |
| 35 | Number of sessions | user | OURS |
| 36 | Days account accessed | user | OURS |
| 37 | Activation → last access | user | OURS |
| 38 | Application_Date → activation | user | OURS |
| 39 | Activation → LE_TIL_Sent_Date | user | OURS |
| 40 | LE_TIL_Sent_Date → first LE visit | user | OURS |
| 41 | Activation → Lock_Date | user | OURS |
| 42 | Last access → Current_Status_Date | user | OURS |

Total distinct variables: 42. Output columns: 42 − 2 + 36 = **76**, before IDs.

---

## 7. Open questions and decision log

### A. Session timeout = 30 minutes **[our assumption]**
Not present in the professor's spec; carried over from the inherited doc. It is the industry convention, but it drives vars 23–27, 35, and 26. **Action:** run the pipeline at 15 / 30 / 60 minutes and report how session count and mean duration move. If results are stable, the choice is defensible; if not, it needs justification.

### B. Are download URLs type-identifiable? **[blocking vars 10, 11, 13, 14]**
The sheet marks `LEDocument` and `CDDocument` as *hard coded*, but real download URLs of the form `/Download/LoanDocument/{id}` are dynamic and cannot be enumerated in a static ~103-path dictionary. Either the dictionary covers module-level download pages (in which case the hardcoded reading works), or an external document ID → type map is required.
**Action:** list every distinct URL where `Download == 1` and determine which case holds. This decides between the primary and fallback definitions in §3.

### C. `beta_coding` vs `coding_dictionary`
The inherited doc cites both without explaining their relationship. **Action:** determine which is authoritative and whether `beta_coding` extends or overrides. Document any path coded differently between them.

### D. Is an activated-language field available? **[blocking vars 18, 19, 29, 30]**
The state machine in §3 initializes from it. If unavailable, the fallback is: initialize from the first `/translations/{lang}` URL the user hits; if the user never hits one, default to English. **Action:** confirm availability. If using the fallback, report the share of users defaulted rather than observed.

### E. `ProcessRelated` is flagged `????` in the professor's own sheet **[blocking var 4]**
The original author marked this variable as unresolved. The inherited doc silently resolved it and assigned it 48 paths — while assigning `BorrowerMortgageProcessRelated` 54. If `ProcessRelated` is the parent of Borrower + Lender, it cannot be smaller than one of its children, so at least one of those figures is wrong.
**Action:** ask the professor whether `ProcessRelated` is (a) the union of Borrower and Lender, (b) an independent tag, or (c) to be dropped. Do not inherit the previous path list. Until resolved, compute var 4 as declared in the dictionary and flag it in output as provisional.

### F. Are consecutive duplicate URLs real pageviews?
Refreshes and back-button navigation inflate vars 28 and 27 and create zero-duration rows. Current decision: **retain**. **Action:** report how many rows are consecutive duplicates; if material, test a de-duplication variant.

### G. Timezone for date bucketing **[affects var 36]**
`Days account accessed` depends on which timezone defines a calendar day. **Action:** declare one (recommend UTC or the lender's local zone, whichever matches the milestone dates) and apply it consistently.

---

## 8. QA checks

Run all of these on every build. Each should either pass or produce an explained exception.

**Hard identities — a failure is a bug, not a finding:**
1. `var29 + var30 == var28` for every user (language flags are exhaustive and exclusive).
2. `var31 <= var28` for every user.
3. `SUM(var27) over a user's sessions == var28`.
4. `var25 == SUM(time_on_page)` within each session.
5. `var35 == count of distinct session_id == count of rows where Session start == 1`, per user.
6. `var34 == SUM(AudioMp3)`, and `var34 <= var28`.
7. `pages_{C} <= var28` for all 18 characteristics.
8. Every session has exactly one `Session start == 1` and one `Session end == 1`.
9. No negative values in vars 22, 25, 26, 27, 37 (elapsed times that are structurally non-negative).
10. `var40 >= 0` wherever non-null (this is what the §5 correction guarantees).

**Diagnostics — report, don't fail:**
11. Count and rate of unmapped URLs against the dictionary.
12. Pattern-vs-dictionary disagreements from §2's validation pass.
13. Missingness rate per milestone date, per variable 38–42.
14. Sign distribution for vars 38–42.
15. Count of single-pageview sessions.
16. Count of mp3 rows with no in-session parent page.
17. If the §3 fallback is in use: share of downloads with `download_type_inferred == 1`.
18. Hierarchy check: is `ProcessRelated` a superset of `BorrowerMortgageProcessRelated ∪ LenderMortgageProcessRelated`? Currently expected to **fail** — see §7-E.

---

## 9. Summary of changes from the inherited document

| Change | Variable(s) | Severity |
|---|---|---|
| `Audio` is a count of mp3 links per page, not a binary flag | 16 | Corrupts data |
| Language is a stateful per-user variable, not a static path property | 18, 19, 29, 30 | Corrupts data |
| Audio dwell time attributes to the parent page | 33 | Understates all category times |
| `LEDownload`/`CDDownload` primary definition is a row-level AND; fallback is session-bounded context, not immediate-prior-row | 13, 14 | Definition changed |
| First LE visit must be constrained to on/after the send date | 40 | Produces negatives |
| `ProcessRelated` returned to unresolved status | 4 | Was silently resolved |
| Vars 32 and 33 documented as 18 columns each | 32, 33 | Schema |
| Renumbered to source order; removed duplicate IDs 27/28 and restored 35/36 | all | Traceability |
| Path counts removed in favor of runtime dictionary lookup | 1–21 | Maintainability |
| Sort key, tie-breaking, boundary rows, units, and timezone made explicit | all sequential | Reproducibility |
