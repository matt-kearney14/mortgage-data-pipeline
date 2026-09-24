# Decisions Log

Every provisional choice made under CLAUDE.md's provisional-decision protocol
is recorded here: **ID, choice, rationale, evidence, alternatives if overruled,
reversal cost.** Each entry must also have a corresponding row in
`output/variable_manifest.csv` (generated from code, never by hand) once the
affected code is implemented, and a comment at the point of implementation
citing the decision ID.

An entry here is not necessarily final — several of these are explicitly
flagged as pending the professor's answer (per `docs/Project_Brief.md`'s Open
Decisions table). Recording the interim choice is required regardless, so the
pipeline can proceed without silently resolving the underlying ambiguity.

---

## DEC-E — `ProcessRelated` computation, pending professor clarification

**Status:** Provisional. Blocks on professor input (owner: Professor, per
`docs/Project_Brief.md` Open Decisions table, item E).

**Choice:** Compute `ProcessRelated_provisional` directly from whatever
`beta_coding` declares per path (0/1; unmapped paths get 0 **and** a
discrepancy-log entry, per CLAUDE.md's never-silently-default rule). Do
**not** compute it as the union of `BorrowerMortgageProcessRelated` ∪
`LenderMortgageProcessRelated`. Do **not** inherit the previous author's
hardcoded 48-path list.

**Rationale:** The professor's own sheet marks this variable with a literal
`????` — confirmed verbatim in `coding_dictionary`'s row for `ProcessRelated`
(see evidence below). The inherited documentation silently resolved this by
assigning `ProcessRelated` 48 paths while assigning its supposed child,
`BorrowerMortgageProcessRelated`, 54 — internally contradictory, since a
parent tag cannot be smaller than one of its children if it is meant to be
their union. Rather than guess which of the three readings the professor
intended (union, independent tag, or drop — see spec §7-E), we compute the
column exactly as declared in the dictionary and output it under a name that
makes its provisional status impossible to miss downstream.

**Evidence:**
- `docs/Clickstream_Variable_Specification_v2.md` §7-E and §6 (variable 4
  tagged FIXED/**OPEN**; QA check #18 is documented as *expected to fail*
  until this resolves).
- `diagnostics/output/inventory_report.md`, section Q1 — `coding_dictionary`'s
  row for `ProcessRelated` reads: *"Webpage includes mortgage
  process-related information (1=yes, 0=no)"* with the sheet's fourth
  (unnamed) column holding literal text `????` for that row — this is the
  professor's own unresolved-flag, read directly from the source file, not
  inferred.
- `docs/Project_Brief.md`, Open Decisions table, item E.

**Alternatives considered, rejected:**
- Union of `BorrowerMortgageProcessRelated` ∪ `LenderMortgageProcessRelated`
  — rejected; spec §7-E explicitly forbids assuming this, and the inherited
  path counts already show it can't be reconciled with the current data
  (parent smaller than child).
- Inherit the old 48-path hardcoded list — rejected; spec §7-E explicitly
  forbids inheriting it, since it's the artifact of the same silent
  resolution being corrected here.
- Drop the variable entirely until the professor answers — rejected; CLAUDE.md's
  protocol requires proceeding with a reasonable choice rather than blocking.

**Reversal cost:** Low. The output column is explicitly named
`_provisional`; its computation is a single dictionary lookup with no
downstream variable depending on it being resolved first (the QA hierarchy
check against Borrower/Lender is already documented as an expected failure).
Once the professor answers, only this one column's lookup/aggregation logic
changes.

---

## DEC-C — `coding_dictionary` cannot serve as the pipeline's per-path source; continue reading `beta_coding`

**Status:** Provisional. Blocks on professor input for full reconciliation
(owner: Professor, per `docs/Project_Brief.md` Open Decisions table, item C).
**This decision also identifies a documentation defect in `CLAUDE.md` itself
— see Rationale.**

**Choice:** `clickstream_processor.py` continues reading `beta_coding` (sheet
of `docs/Clickstream_path_frequencies_and_coding_scheme.xlsx`) as the
per-path classification source for URL-level flags. `coding_dictionary` is
used only as a source of variable *definitions* (its `Description` and
`Hard coded or calculated?` text) — never merged against event-log paths,
since it structurally cannot be.

**Rationale:** `coding_dictionary` (45 data rows) is a **variable glossary**
— one row per variable name, columns `Data variable`, `Hard coded or
calculated?`, `Description` — not a per-URL lookup table. It has **no
`CODING SCHEME` column and no per-path rows at all**. `beta_coding` (104
rows) is the actual per-path binary-flag matrix, keyed on `CODING SCHEME`.
These are not parallel/interchangeable tables. `CLAUDE.md`'s Classification
Dictionary section currently states *"`coding_dictionary` is authoritative.
`beta_coding` was the professor experimenting and must not be read by the
pipeline"* — that instruction is incompatible with the sheets' confirmed
structure: `coding_dictionary` cannot be joined to the event log on any key,
so a pipeline that only reads it would have no per-path classification data
at all. The user has flagged this for a direct fix to `CLAUDE.md`; this
decision documents the interim technical reality so the pipeline isn't
blocked while that rule is corrected.

**Evidence:**
- `diagnostics/output/inventory_report.md`, section Q1 — confirms
  `coding_dictionary` has no `CODING SCHEME` column (`False`) while
  `beta_coding` does (`True`); full verbatim dump of all 45
  `coding_dictionary` rows included in that report.
- All 14 names in `clickstream_processor.py`'s inherited `static_vars` list
  appear both as `coding_dictionary` `Data variable` rows and as
  `beta_coding` columns — so the variable *names* are consistent across both
  sheets even though only one sheet carries per-path values.
- `docs/Project_Brief.md`, Open Decisions table, item C.

**Alternatives considered, rejected:**
- Read only `coding_dictionary` and drop `beta_coding` per CLAUDE.md's
  literal current instruction — rejected; structurally impossible, this
  sheet has no per-path rows to merge against.
- Ignore `coding_dictionary` entirely and treat `beta_coding` as the sole
  source of everything, including variable definitions — rejected;
  `coding_dictionary`'s description text is the only surviving
  documentation of intended meaning for several variables (e.g. it confirms
  `Audio` is meant to be a count, and carries the professor's `????` flag on
  `ProcessRelated` used in DEC-E above) — discarding it loses real
  information even though it can't drive the per-path merge.
- Silently keep reading `beta_coding` without recording anything — rejected;
  this is exactly the kind of ambiguity CLAUDE.md requires be surfaced, not
  quietly carried forward as settled fact (as happened previously with
  `ProcessRelated`).

**Reversal cost:** Low. No code path changes as a result of this decision —
`clickstream_processor.py` already reads `beta_coding`. If the professor
later supplies a corrected `coding_dictionary` with genuine per-path rows and
a join key, the merge's `sheet_name`/`right_on` arguments change; nothing
else in the pipeline depends on which sheet is used for the lookup.

---

## DEC-F — Download document type is not recoverable from the URL; `LEDocument`, `CDDocument`, `LEDownload`, `CDDownload` are unresolvable from current inputs

**Status:** Blocked on a missing input. Not provisional — this is a measured
finding that closes spec §7-B in the negative.

**Choice:** `Download` is set to 1 by rule for any path under
`/Download/LoanDocument/` or `/download/samples/`. `LEDocument`, `CDDocument`,
`LEDownload` and `CDDownload` are emitted as **NULL** with provenance
`unresolved_download_type` — never 0 — until a LoanDocument-id → document-type
lookup is supplied.

**Rationale:** Every `/Download` event path in the log has exactly one shape,
`/Download/LoanDocument/{numeric id}`. There is no `LE` or `CD` token anywhere in
any download URL. The inherited regexes `/Download/LoanDocument/.*LE` and
`/Download/LoanDocument/.*CD` match **0 of 337,581 rows**, so all four variables
are identically zero in the current pipeline output — not sparse, zero. Emitting
0 would encode "this was not an LE download" when the truth is "we cannot tell",
which is exactly the silent default CLAUDE.md's Data hygiene rule 2 forbids.

**Evidence:**
- `diagnostics/output/coverage_report.md`, Appendix B — every `/Download` path
  with digit runs masked collapses to the single shape
  `/Download/LoanDocument/{N}` (17,502 events); both inherited regexes return 0
  matching rows over the full log.
- `docs/Project_Brief.md`, Open Decisions — currently states the opposite
  ("download URLs appear type-identifiable… **Resolved by the repo**"). That
  claim is contradicted by the data and needs correcting.
- No file in `data/` contains a LoanDocument id column of any kind.

**Alternatives considered, rejected:**
- Keep the regexes and ship four all-zero columns — rejected; they would read as
  measured zeros in any downstream analysis.
- Treat every `/Download/LoanDocument/` hit as an LE download — rejected;
  unfounded, and it would inflate `LEDownload` by the entire 17,502-event volume.
- Drop the four variables — rejected; the grain is buildable the moment a
  document-type lookup arrives, so the columns stay with NULLs.

**Reversal cost:** Low. One join on LoanDocument id and four lookups; nothing
downstream depends on these being resolved first.

**What is needed to close this:** a table mapping LoanDocument id → document type
(LE / CD / other), or an export of the document-service metadata.

---

## DEC-G — Uncoded content paths are classified by sibling inference, with per-cell provenance

**Status:** Provisional. Every inferred cell is routed to the professor for
confirmation in `output/dictionary_review_for_professor.xlsx`.

**Choice:** Extend the per-path dictionary beyond `beta_coding`'s 103 coded paths
to cover all 9,471 paths in the log, using a two-scope inheritance rule:

- **format-scoped flags** — `Audio`, `Video`, `Personalized` — inherit from coded
  paths of the same presentation format (`/Module`, `/Faq`, `/Slideshow`, …);
- **topic-scoped flags** — all others — inherit from coded paths sharing the same
  content topic slug (`budgeting-basics`, `your-va-fixed-rate-loan`, …);
- **audio clips** inherit from a coded clip with the same `CC_{n}` id, which is
  the clip's identity — the path prefix is only the page it was played from;
- **anything with no basis** stays NULL with provenance `unresolved`.

Every flag cell in `output/path_dictionary_extended.csv` carries a sibling
`<flag>__prov` column valued `coded`, `inferred_topic`, `inferred_format`,
`inferred_audio_cc`, `navigation`, `rule_download_path`, `non_pageview`,
`unresolved` or `unresolved_download_type`. Downstream analysis can therefore
reproduce any result on coded cells alone.

**Rationale:** The professor coded a *sample* of the content library, not all of
it — 26 page rows plus 77 audio clips. The uncoded remainder is not arbitrary:
it is near-identical siblings of coded pages (`/Module/your-va-fixed-rate-loan`
uncoded while `/JustTheFacts/your-va-fixed-rate-loan-just-the-facts` is coded).
The two scopes were chosen by measurement, not assumption: a naive topic-only
rule scores 85.3% leave-one-out, and moving `Audio`/`Video` to format scope lifts
those two flags from 61.1% to 95.5% and the overall rule to **90.9%** (169 of 186
held-out cells). The per-flag accuracies are reported as measured, including the
weak ones — `ProcessRelated` 72.2% and `BorrowerMortgageProcessRelated` 66.7%,
which is consistent with `ProcessRelated` being the variable the professor
himself flagged `????` (DEC-E).

**Evidence:**
- `diagnostics/output/dictionary_inference_report.md` §1 — per-flag leave-one-out
  table; §3 — event coverage per flag before vs after (e.g. `MortgageRelated`
  30.9% → 83.5%); §4a — the complete 28-path list still needing the professor.
- `diagnostics/output/coverage_report.md` — the coverage gap this addresses.
- Script: `diagnostics/build_path_dictionary.py` (sources unmodified).

**Alternatives considered, rejected:**
- Leave uncoded paths at 0 with a discrepancy log — rejected by the user; it
  discards signal on 8.45% of events across near-identical siblings of coded
  pages, and 0 is itself a silent claim.
- Infer everything from topic alone — rejected; measured at 85.3%, and it gets
  `Audio`/`Video` wrong 39% of the time because those are properties of the
  presentation format, not the subject.
- Hand-code the 28 remaining paths ourselves — rejected; that is the professor's
  coding scheme to extend, and the list is short enough to route to him.

**Reversal cost:** Very low. Drop every row whose provenance is not `coded` and
the dictionary reverts exactly to `beta_coding`. Verified: coded values are
reproduced bit-for-bit (0 altered cells) and every originally-coded cell is
labelled `coded`.

---

## DEC-H — Navigation and non-pageview paths are classified by explicit rule

**Status:** Provisional.

**Choice:** 23 structural paths — `/MyMortgage`, `/Login*`, `/`, `/Dashboard*`,
`/SelectLoan`, `/Logout`, `/Contact`, `/About`, `/Privacy`, `/Terms`,
`/Glossary`, `/AwarenessQuestions*`, `/survey` — receive **0 for every content
flag**, with provenance `navigation`. `/favicon.ico` and `/cart.json` receive
NULL with provenance `non_pageview` and should be excluded from pageview counts
entirely.

**Rationale:** These carry 157,055 events — 46.5% of the entire log — and are the
single largest reason the raw dictionary match rate looks catastrophic (36.6%).
They are application chrome and authentication screens with no mortgage education
content to classify, so 0 is the correct value rather than an absence of one.
Recording it as an explicit rule with its own provenance value keeps it
distinguishable from a coded 0 and from a defaulted one. `/favicon.ico` (4,670
events) and `/cart.json` (5) are browser asset requests, not pages a borrower
viewed; counting them as pageviews would inflate `webpages_visited` for every
user.

**Evidence:**
- `diagnostics/output/coverage_report.md`, Appendix A — the unmapped tail broken
  down by cause; navigation is 161,731 of the 213,917 unmapped events.
- `diagnostics/output/dictionary_inference_report.md` §2 — path rows by class.

**Alternatives considered, rejected:**
- Leave them unmapped and NULL — rejected; it would make `MortgageRelated` and
  every sibling flag unknown for nearly half the log when the answer is plainly 0.
- Count `/favicon.ico` as a pageview — rejected; it is an automatic browser
  request, not a user action.
- Drop navigation rows from the event log — rejected; they are needed for
  sessionization, `time_on_page` of the preceding page, and `pages_in_session`.

**Reversal cost:** Very low. Filter on `row_class` / provenance `navigation`;
the membership list is a single named constant in
`diagnostics/build_path_dictionary.py`.

---

## DEC-I — Language is read from the path, never from the dictionary

**Status:** Provisional; supersedes nothing, but it removes `beta_coding` as a
candidate source for variables 18–19.

**Choice:** The extended dictionary emits **no** `English_YN` / `Spanish_YN`
columns. It emits `path_language` ∈ {`en`, `es`, `unknown`} derived from the path
(`/translations/es`, `/audio/faq/es/`, and the `en` equivalents). The stateful
per-user language variables remain a pipeline concern per spec §3.

**Rationale:** `beta_coding` codes `English(Y/N)` = 1 and `Spanish (Y/N)` = 0 on
**every single row without exception** — the professor coded only the English
library. The two columns therefore carry zero information and inheriting them
would manufacture a finding: every Spanish page in the log would be labelled
English. The log does contain Spanish paths (`/translations/es`, 320 events;
`/audio/faq/es/CC_*.mp3`, 73 distinct clips, 144 events), and
`talkument_useraccount.provided_language` has full coverage with 282 `es` users,
so the state machine described in the spec has real inputs to work from.

**Evidence:**
- Value-set scan of `beta_coding`: `English(Y/N)` ∈ {1.0}, `Spanish (Y/N)` ∈
  {0.0} across all 103 coded path rows.
- `diagnostics/output/inventory_report.md` Q2 — `provided_language` coverage
  1.0000, distribution {en: 20,981, es: 282}.
- Path-language distribution in `output/path_dictionary_extended.csv`: 23,248
  `en` events, 464 `es`, 313,869 `unknown` (paths with no language segment).

**Alternatives considered, rejected:**
- Inherit `English(Y/N)`/`Spanish (Y/N)` from the dictionary — rejected; it
  hard-codes every page as English and makes the spec's own recommended QA check
  (modal computed language vs account language) impossible to fail.
- Set `English_YN = NOT Spanish_YN` from the path — rejected; this is the exact
  inherited defect the Project Brief already flags, and it makes the
  reconciliation check pass trivially.

**Reversal cost:** Very low. `path_language` is an additional column; nothing was
removed that carried information.

---

## DEC-J — `Audio` is emitted as an integer but a true link count is not derivable

**Status:** Provisional. The definition and the data disagree; the professor
should confirm which he wants.

**Choice:** `Audio` is typed `Int16` and carries the dictionary's value, which is
0 or 1. It is **not** a count of audio links, despite the canonical column spec
calling it an integer count.

**Rationale:** `coding_dictionary` defines `Audio` as the *"number of audio file
links"* on the page, and CLAUDE.md's canonical column table types it **integer
count**. But `beta_coding` — the only sheet with per-path values — codes it
strictly 0/1, and the event log cannot supply the missing information: a page's
audio-link density is a property of the page's HTML, which we do not have. The
one trace of page-to-clip association in the log is the prefixed clip path
(`/Module/audio/faq/en/CC_100.mp3`), and it identifies only the *section*, not
which module — and covers 153 of 4,170 mp3 events. Emitting a fabricated count
would be worse than emitting the binary; emitting the binary under a column the
spec says is a count is a mismatch that must be visible rather than silent.

**Evidence:**
- `beta_coding` value-set scan: `Audio` ∈ {0.0, 1.0} across all 103 coded paths.
- mp3 path shapes in the log: `/audio/faq/en/CC_{N}.mp3` 3,873 events,
  `/audio/faq/es/CC_{N}.mp3` 144, `/Module/audio/faq/en/CC_{N}.mp3` 130,
  `/Faq/audio/faq/en/CC_{N}.mp3` 23 — only the last two carry any page context,
  and neither names a specific page.
- `docs/Clickstream_Variable_Specification_v2.md` §2; CLAUDE.md canonical columns.

**Alternatives considered, rejected:**
- Derive the count from prefixed clip paths — rejected; covers 3.7% of mp3 events
  and resolves to a section, not a page.
- Silently retype as binary and drop the count language from the docs — rejected;
  that repeats the `ProcessRelated` failure mode of promoting a workaround into
  documented fact.
- Leave `Audio` NULL everywhere — rejected; the binary is real information.

**Reversal cost:** Very low. If the professor supplies per-page link counts it is
a dictionary column swap; `AudioMp3` (row-level, exact) is unaffected either way.

---

## DEC-K — Users absent from `talkument_useraccount.xlsx` are seeded English

**Status:** Provisional.

**Choice:** The language state machine seeds each user from
`provided_language`. The 132 users present in the event log but absent from the
account file are seeded `DEFAULT_LANGUAGE = "en"`. Every row carries
`language_seed_source` ∈ {`account`, `default`} so the seeded-by-default
population is separable in any downstream analysis.

**Rationale:** 5,621 events (1.67% of the log) belong to users with no account
row. The state machine needs an initial value; leaving it NULL would propagate
NULL through every row until that user's first `/translations/` switch, if any,
and break the exhaustiveness property the spec requires (`English + Spanish ==
pages`). English is the base rate at 20,981 of 21,263 accounts (98.7%). Marking
the provenance rather than hiding the assumption is what keeps this honest.

**Evidence:**
- `diagnostics/output/inventory_report.md` Q6 — 132 event-log users absent from
  `useraccount`, 5,621 affected event rows (1.67%).
- Q2 — `provided_language` coverage 1.0000, {en: 20,981, es: 282}.
- `output/qa_phase1.md` §2 — 331,960 account-seeded rows vs 5,621 default-seeded.

**Alternatives considered, rejected:**
- Seed from the first `/translations/` hit instead — rejected; most users never
  visit one, so it would leave the majority NULL.
- Drop the 132 users — rejected; they are real users with real event histories.

**Reversal cost:** Very low. `DEFAULT_LANGUAGE` is a named constant, and
`language_seed_source` lets anyone re-run the analysis excluding these users
without a pipeline change.

---

## DEC-L — Unresolved flags are emitted NULL, not 0; CLAUDE.md rule 2 is amended

**Status:** Provisional. **Conflicts with CLAUDE.md as currently written — that
document needs the edit.**

**Choice:** Flags with no basis are emitted **NULL**, with the path and its hit
count written to `output/discrepancy_log.csv`. The alternative behaviour is
available without a code edit: `--unresolved-fill 0`.

**Rationale:** CLAUDE.md Data hygiene rule 2 says unmapped paths *"receive 0 for
FIXED flags **and** are written to a discrepancy log."* The rule's stated intent —
never silently default — is right, but the prescribed 0 is precisely a silent
default: it encodes "measured, and the answer is no" for a path nobody classified.
The cost is now measurable. Under the inherited 0-fill, `Goal_to_inform` reads
42,391 ones against 108,162 fabricated zeros, and `LEDocument`/`CDDocument` read
as 337,581 measured zeros when not one row was ever evaluated. Any rate computed
over those denominators is wrong by construction. The discrepancy-log half of the
rule is kept and strengthened; only the 0-fill half is changed.

**Evidence:**
- `output/phase1_before_after.md` — de-fabricated 0→NULL counts per column;
  55,631 for the topic-scoped flags, 108,162 for `Goal_to_*`, 337,581 for the
  DEC-F columns.
- `output/qa_phase1.md` §5 — coverage reported as measured per column.
- `output/discrepancy_log.csv` — 9,253 paths, 177,650 events, with hit counts.

**Alternatives considered, rejected:**
- Follow CLAUDE.md literally and fill 0 — rejected as above, but retained behind
  `--unresolved-fill 0` so the professor's preference is a rerun, not an edit.
- Drop rows with any unresolved flag — rejected; it would discard 52.6% of the
  log and bias every user-level aggregate toward users who read coded content.

**Reversal cost:** Zero — it is a CLI flag.

---

## DEC-M — Language switches on `/translations/` only, per spec §3

**Status:** Provisional. Default follows the spec; the alternative is a flag.

**Choice:** The language state machine switches state **only** on
`/translations/en` and `/translations/es`, exactly as spec §3's algorithm is
written. Treating an `/en/` or `/es/` asset segment (e.g.
`/audio/faq/es/CC_12.mp3`) as a switch is available via `--lang-asset-paths`.

**Rationale:** The asset-path extension is defensible — a clip under
`/audio/faq/es/` is Spanish content — but it is a deviation from the rank-1
source of truth, and the spec's algorithm is explicit. Measured, the difference
is small and slightly favours the extension: 195 rows move, Spanish rows go
4,595 → 4,720, and the Spanish-group mismatch rate falls 11.48% → 10.93%. That
is not a large enough gain to justify silently departing from the spec, and the
distinction is real: requesting `/translations/es` means the *user switched the
site language*, whereas playing one Spanish clip does not.

**Evidence:**
- Both modes run on the full log: 195 differing rows; per-group mismatch rates
  en 0.02% / es 11.48% (spec) vs en 0.01% / es 10.93% (extension).
- `output/qa_phase1.md` §1 states the active mode in the report itself.

**Alternatives considered, rejected:**
- Default to the asset-path extension — rejected; deviates from spec §3 for a
  0.55 pp gain on 183 users.
- Hard-code the spec behaviour with no flag — rejected; CLAUDE.md requires that a
  professor's "try it the other way" be a rerun.

**Note on the residual mismatch:** the 11.48% Spanish-group mismatch is **not a
defect**. All of it traces to users who explicitly requested `/translations/en`
and stayed in English; verified that none of those rows were switched by an
asset-path segment. It is a behavioural finding about Spanish-preference
borrowers and is worth reporting to the professor in its own right.

---

## DEC-S — `/translations/en` is a page resource, not a language switch

**Status:** Provisional. Needs confirmation from someone who knows the
application's behaviour. **This decision corrects a defect in our own earlier
work, and retracts a finding we reported.**

**Choice:** `/translations/es` switches the language state to Spanish.
`/translations/en` does **not** switch state by default — it is treated as a
resource the page requests. Controlled by `--lang-en-switch`, default `never`;
`always` restores the previous spec-literal behaviour, with `not-after-module`
and `not-paired-with-es` available as middle positions.

**Rationale:** The language toggle exists only in pilot bucket 3. The log agrees:
`/translations/es` occurs 292 times in bucket 3 and **zero times in bucket 2**. But
`/translations/en` occurs at almost identical rates in both buckets — **9,085 in
bucket 2 and 9,114 in bucket 3** — and bucket 2 users have no toggle to press. Whatever
generates those 9,085 events is not a user action. It is preceded by a
`/Module/` page 85.0% of the time in bucket 2 and 83.8% in bucket 3, and 117 of the
119 cases where `/translations/en` directly follows `/translations/es` are **0
seconds apart** — a single page load requesting both.

Treating every `/translations/en` as "the user switched to English" therefore
flipped Spanish-preference users back to English on module pages they had not
asked to be in English.

**Evidence:**

| switching rule | Spanish pageviews | es-account users whose modal language is not `es` |
|---|---|---|
| `always` (previous behaviour) | 4,548 | 11.5% |
| `not-paired-with-es` | 5,572 | 3.3% |
| `not-after-module` | 5,424 | 6.6% |
| **`never` (chosen)** | **6,836** | **0.0%** |

Under the chosen rule every Spanish-account user's modal computed language is
Spanish, which is what should be true of users given Spanish content who use it.

**Retraction.** We previously reported the 11.5% figure to the project owner as
a *behavioural finding* — "Spanish-preference users who explicitly chose English
and stayed there." That was wrong. It was our own bug, and no such behaviour is
evidenced. `output/qa_phase1.md` §1 now carries the correction inline.

**A check that does not work, recorded so nobody re-runs it as evidence:**
"Spanish pageviews appearing in bucket 2" reads 0 under all four rules and looks
like a passing discriminator. It is not. No bucket-2 user has `provided_language =
es` and `/translations/es` never occurs in bucket 2, so no rule can produce a
Spanish row there. The check cannot fail and is not evidence.

**Alternatives considered, rejected:**
- Keep `always` per spec §3 — rejected; the spec's algorithm was written without
  knowledge that the application emits this path on page load, and the bucket-2
  evidence is decisive that it does.
- `not-after-module` — rejected as the default; it is a heuristic on adjacency
  that still leaves a 6.6% mismatch, and 15% of the events are not
  module-preceded anyway.

**Settled 2026-09-24 by the project owner:** the tag appears **whenever the user
chooses a new language**, in either direction. So a real switch back to English
does emit `/translations/en` — the same path the app emits on page load. The two
are therefore genuinely indistinguishable, and the question becomes how much
real signal is buried in the noise. That is measurable.

Bucket 2 has no toggle, so its `/translations/en` rate is a pure noise baseline:
**0.3061 per module pageview**. Applying that rate to bucket 3's module pageviews
predicts the bucket-3 count if every event were noise:

| | bucket 2 (noise baseline) | bucket 3 (toggle exists) |
|---|---|---|
| module pageviews | 29,676 | 30,774 |
| `/translations/en` observed | 9,085 | 9,114 |
| `/translations/en` predicted as pure noise | — | 9,420 |
| **excess over noise** | — | **−306** |
| `/translations/es` (clean signal) | 0 | 292 |

**Bucket 3 shows no excess whatever** — slightly fewer than noise alone predicts.
At population level, switches back to English are not detectable. Narrowing to
the 114 borrowers who ever touched Spanish, the only people who *can* switch
back: 410 `/translations/en` observed against 370 predicted as noise, an excess
of **about 40 events** — roughly 10% of that subgroup's events, and set against
320 clean switches *to* Spanish.

So `never` remains the default. It forgoes ~40 real English switches;
`always` would manufacture ~370 false ones among exactly the users whose
language matters most. The error is an order of magnitude smaller.

**Consequences recorded in the output rather than left implicit:**
- `language_switches_to_spanish` and `used_language_toggle` are new user-level
  columns. Switching *to* Spanish is a clean measure — zero noise, by the bucket-2
  evidence — so it is exposed directly rather than left buried in the language
  state. 114 borrowers, 320 switches, mean 2.81 and max 20 among those who
  toggled; all but 10 are in bucket 3, the rest having no resolvable bucket.
- The codebook entries for `spanish_webpages_visited` and
  `english_webpages_visited` carry the caveat that Spanish exposure may be
  slightly overstated for those 114 borrowers, with the ~40-event headroom
  stated, and point at `used_language_toggle` for identifying them.

**Reversal cost:** Zero. It is a CLI flag; nothing downstream depends on which
rule was used beyond the two language columns and their aggregates.

---

## DEC-T — `time_AudioMp3` is raw listening time, and overlaps the parent page's characteristics

**Status:** Provisional. Resolves a tension inside the specification itself.

**Choice:** `time_AudioMp3` is the dwell on `.mp3` rows — actual listening time.
The same seconds are **also** credited to the characteristics of the page that
played the clip, so this column overlaps the other `time_*` columns exactly as
they already overlap one another. `pages_AudioMp3` is added alongside it and is
identical to `audio_clips_clicked`.

**Rationale:** Spec §5 expands vars 32 and 33 across 18 characteristics, and
names `AudioMp3` (var 15) as one of them. But §5's audio rule says an mp3 row's
duration is credited *only* to its parent page's characteristics, "never
additionally to its own dictionary flags." Read strictly together, those two
statements make `time_AudioMp3` zero for every user — which cannot be the
intent, or the specification would not list the characteristic at all.

The reading adopted: the parent-page rule exists so that listening time counts
toward the *content category* that hosted the clip (a Loan Estimate explainer's
audio should count as Loan Estimate time). It is not a statement that listening
time should be unmeasurable. `time_AudioMp3` answers a different question — how
long did this borrower spend listening — and both are worth having, provided the
overlap is stated rather than discovered.

`pages_AudioMp3` duplicating `audio_clips_clicked` is likewise the
specification's own doing: the same quantity appears as var 15 expanded by var
32, and again as var 34. Both names are kept so that all 18 expanded
characteristics are present and a reader counting columns finds them.

**Evidence:**
- Measured: 258,622 seconds of listening across 878 borrowers (2.20% of total
  observed time), median 99 seconds among those with any.
- `pages_AudioMp3 == audio_clips_clicked` for all 10,140 users, verified.
- With this added, all 18 of the specification's expanded characteristics have
  both a `pages_` and a `time_` column; previously `AudioMp3` had neither.

**Alternatives considered, rejected:**
- Follow the parent-page rule strictly and emit `time_AudioMp3` as all zeros —
  rejected; a column of zeros presented as a measure is the fabricated-value
  failure this project has already corrected once (DEC-L).
- Omit `AudioMp3` from the expansion entirely — rejected; the spec names it, and
  silently shipping 17 of 18 is the kind of quiet incompleteness someone
  discovers later by counting.
- Stop crediting audio time to the parent page so the columns partition —
  rejected; §5 is explicit, and the professor's own note is quoted there:
  audio time is "associated with that page."

**Reversal cost:** Very low. Two columns; no other variable depends on them.
