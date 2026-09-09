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
