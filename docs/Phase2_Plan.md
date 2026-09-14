# Phase 2 Plan — sessionization, aggregations, time attribution

**Status:** Proposed, not implemented. CLAUDE.md requires plan mode for
sessionization and time-per-characteristic attribution; this is that plan.
**Prerequisite:** `output/phase1_url_features.parquet` (built, 20/20 checks pass).
**Every number below is measured**, not estimated — see the Diagnostics section.

---

## 1. Five decisions to settle before writing code

These are the places where the spec is silent or the data pushes back. Each has
a recommendation and the measurement behind it.

### D1. Session timeout — the assumption is safer than we thought

`SESSION_TIMEOUT = 30 min` is our assumption (§7-A), never the professor's. It
matters far less than expected for counts, and a great deal for durations:

| timeout | sessions | per user | median dur | **mean dur** | median pages | single-page sessions |
|---|---|---|---|---|---|---|
| 5 min | 42,458 | 4.19 | 53 s | 143 s | 6 | 10.0% |
| 15 min | 36,546 | 3.60 | 70 s | 250 s | 6 | 7.7% |
| **30 min** | **34,447** | **3.40** | **82 s** | **342 s** | **7** | **6.7%** |
| 60 min | 32,752 | 3.23 | 93 s | 494 s | 7 | 5.9% |
| 240 min | 29,429 | 2.90 | 127 s | 1,432 s | 8 | 4.6% |

Doubling the timeout from 30 to 60 minutes changes `num_sessions` by **−4.9%**
but changes **mean session duration by +44%**.

**Recommendation:** keep 30 minutes as the default; emit `num_sessions` and
`pages_in_session` as robust, and flag `session_duration`'s **mean** as
timeout-sensitive in the manifest. Ship a `--session-timeout` flag and write the
sensitivity table to `output/` on every run so the number is never quoted
without its error bar. Report median alongside mean everywhere.

### D2. Zero-second gaps are mostly redirects, not reading

**39,483 consecutive events (11.7%) are exactly 0 seconds apart.** Only 1,512 of
those repeat the same path; **37,971 are different paths**. The top pairs are not
plausible human reading:

| pair | count |
|---|---|
| `/` → `/AcceptTerms` | 7,420 |
| `/AcceptTerms` → `/` | 7,123 |
| `/` → `/MyMortgage` | 3,883 |
| `/Module/your-loan-estimate-made-clear` → `/translations/en` | 2,180 |

These are redirect chains and language-toggle round-trips logged in the same
second. They will produce `time_on_page = 0` rows.

**Recommendation:** do **not** drop them — they are real navigation and they
matter for `webpages_visited`. Emit `time_on_page = 0` (a measured zero, not a
NULL: the successor timestamp exists), and add a `zero_dwell` boolean so any
dwell-based analysis can exclude them explicitly. Report what share of each
`time_{Characteristic}` total comes from zero-dwell rows.

### D3. `/favicon.ico` splits dwell and must be dropped before timing

4,675 `non_pageview` rows (`/favicon.ico`, `/cart.json`), and **4,548 of them sit
between two other rows of the same user**, median 4 seconds after the preceding
page. Left in, each one truncates a real page's dwell and adds a phantom
pageview.

**Recommendation:** drop `row_class == 'non_pageview'` rows **before**
sessionization and before `time_on_page`, so dwell flows from the real page to
the next real page. Record the dropped count. This is the one place we remove
rows; everything else is retained per §7-F.

### D4. Rows whose flags are NULL get no time, and the gap is reported

**24,256 rows have all 13 characteristic flags NULL** (unresolved downloads and
uncoded content). Their dwell cannot be credited to any characteristic.

**Recommendation:** exclude NULL from every `time_{C}` and `pages_{C}` sum — NULL
is not zero — and emit two companion columns per user,
`time_unattributable` and `pages_unattributable`, so the denominator is visible.
Without these, every per-characteristic share silently understates.

### D5. `time_{Characteristic}` columns overlap by design — say so

**27.5% of rows carry more than one flag**, mean **3.21 flags** per flagged row.
So `sum(time_{C}) over all C` ≈ 3× total time. This is correct per spec §5 —
the characteristics are not a partition — but it is a foot-gun.

**Recommendation:** state it in the manifest note for all 18 `time_*` and
`pages_*` columns, and emit `total_time` and `webpages_visited` separately as the
only legitimate denominators.

---

## 2. Build order

### Step 1 — sessionization
Drop `non_pageview` (D3). Sort key is already `user_hash`, `eventdate`,
`_source_row` (verified across 77,695 tied rows). Then per spec §3:

- `session_start[T] = (t[T] − t[T−1]) > SESSION_TIMEOUT`, and **1** for a user's first row
- `session_end[T]   = (t[T+1] − t[T]) > SESSION_TIMEOUT`, and **1** for a user's last row
- `session_id = cumsum(session_start)` within user
- `session_start_ts` / `session_end_ts` = min/max within `session_id` — **separate
  fields from the boolean flags**, per CLAUDE.md

`time_on_page[T] = t[T+1] − t[T]` within the same session; **NULL** on the last
row of each session (unobservable, not zero).

**Check that can fail:** for every session, `sum(time_on_page)` treating the
trailing NULL as zero must equal `session_end_ts − session_start_ts` exactly.
This fails if the session boundary and the dwell calculation disagree by even one
row — for instance if `time_on_page` were computed across a session break.

### Step 2 — session-level (§4)
`session_duration`, `pages_in_session`, `inter_session_elapsed`
(NULL for a user's first session). Single-pageview sessions get duration **0**,
not NULL, and are counted separately (§4 requires reporting the share).

**Check that can fail:** `inter_session_elapsed` must be strictly positive
wherever it is non-NULL — a zero or negative value means sessions were ordered
wrongly or a boundary was double-counted.

### Step 3 — user-level volume (§5)
`webpages_visited`, `spanish_webpages_visited`, `english_webpages_visited`,
`unique_webpages_visited`, `audio_clips_clicked` (= `sum(AudioMp3)`, explicitly
**not** `Audio`), `num_sessions`, `days_accessed`.

`days_accessed` needs the timezone declared (§7-G). `eventdate` is
timezone-naive at second resolution; recommend declaring `TIMEZONE = "UTC"` as a
named constant and recording it as a decision, since no surviving input file
disagrees with it.

**Check that can fail:** `spanish_webpages_visited + english_webpages_visited ==
webpages_visited`. This is **not** trivially true here — the two come from an
independent `language_state`, not from complementation — so a user with an
unassigned row would fail it.

### Step 4 — schema-expanding (§5 vars 32, 33)
18 `pages_{C}` and 18 `time_{C}` columns, using the frozen canonical names.
`Audio` excluded from both — it is a count, not a flag.

Audio time goes to the **parent page** per §5: an mp3 row's dwell is credited
only to the characteristics of the most recent non-mp3 pageview in the same
session, never additionally to the mp3's own flags. Feasibility confirmed:
**0 of 4,170 mp3 rows are a user's first row**, so a parent always exists in
principle; **2,468 are preceded by another mp3**, so the lookup must walk back to
the last non-mp3 row rather than using `shift(1)`.

**Check that can fail:** no mp3 row's dwell may appear in both its own flags and
its parent's. Assert that the two clauses partition the rows — total attributed
time must equal total dwell on flagged rows exactly, and any mp3 with no
in-session parent must land in the logged exception list, not silently in either
bucket.

### Step 5 — milestone timers
Five of six stay blocked (DEC-F, no source file). **One is not:**
`t_activation_to_last_access` = last pageview − first pageview needs no external
file. Computable for all 10,140 users; median **7.3 days**; 95 users have a
zero-length span. Build it; emit the other five as NULL with documented
missingness.

---

## 3. Outputs

| file | grain |
|---|---|
| `output/phase2_events.parquet` | event grain + session_id, time_on_page, flags |
| `output/phase2_sessions.parquet` | one row per session |
| `output/user_level_dataset.parquet` / `.xlsx` | **one row per user — the deliverable** |
| `output/session_timeout_sensitivity.csv` | D1 table, regenerated each run |
| `output/qa_phase2.md` | every check above, reported as measured |
| `output/variable_manifest.csv` | extended, generated from code |

The user-level file is the thing the project has been aiming at: **one row per
user, every variable as a column**, sortable and filterable by user.

---

## 4. New decisions this plan would create

| ID | Subject |
|---|---|
| DEC-N | Session timeout 30 min default + mandatory sensitivity output (D1) |
| DEC-O | Zero-dwell rows retained, flagged, measured as 0 not NULL (D2) |
| DEC-P | `non_pageview` rows dropped before sessionization (D3) |
| DEC-Q | NULL flags excluded from attribution; unattributable time reported (D4) |
| DEC-R | Timezone declared for calendar-day bucketing (§7-G) |

---

## 5. Diagnostics behind every number here

Reproduce with `diagnostics/coverage.py`, `diagnostics/build_path_dictionary.py`,
and the Phase 1 QA in `clickstream_processor.py`. Source figures:
`output/qa_phase1.md`, `output/phase1_before_after.md`,
`diagnostics/output/dictionary_inference_report.md`.
