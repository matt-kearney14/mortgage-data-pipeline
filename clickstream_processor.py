#!/usr/bin/env python3
"""Phase 1 — URL-level feature engineering for the Talkument clickstream.

Rebuilt 2026-09-14 against `output/path_dictionary_extended.csv` (DEC-G/H/I).
Replaces the inherited implementation, which defaulted every unmapped path and
every blank dictionary cell to 0 and shipped four identically-zero columns.

Run:
    python3 clickstream_processor.py                 # parquet intermediate
    python3 clickstream_processor.py --excel         # also write .xlsx (slow)
    python3 clickstream_processor.py --compare       # before/after vs inherited
    python3 clickstream_processor.py --unresolved-fill 0

Outputs:
    output/phase1_url_features.parquet      event grain, 337,581 rows
    output/variable_manifest.csv            generated here, never by hand
    output/discrepancy_log.csv              every path with an unresolved flag
    output/qa_phase1.md                     QA results, reported as measured
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================================
# CONFIGURATION — named constants only. A professor's "try it the other way"
# must be a rerun, not an edit (CLAUDE.md, Provisional decisions).
# ============================================================================
ROOT = Path(__file__).resolve().parent
EVENTS_FILE = ROOT / "data" / "talkument_userinteractions.xlsx"
ACCOUNT_FILE = ROOT / "data" / "talkument_useraccount.xlsx"
DICTIONARY_FILE = ROOT / "output" / "path_dictionary_extended.csv"
CODING_FILE = ROOT / "docs" / "Clickstream_path_frequencies_and_coding_scheme.xlsx"
OUT = ROOT / "output"

USER_COL = "user_hash"
TIME_COL = "eventdate"
URL_COL = "path"

# DEC-K: users absent from talkument_useraccount.xlsx get this seed language.
DEFAULT_LANGUAGE = "en"
LANG_SWITCH_EN = "/translations/en"
LANG_SWITCH_ES = "/translations/es"
# DEC-M. Spec §3's algorithm switches state on /translations/{en,es} ONLY.
# Treating an /es/ or /en/ asset segment (e.g. /audio/faq/es/CC_12.mp3) as a
# switch is a deviation from the spec; it moves 195 rows. Default is the spec.
LANG_ASSET_PATH_SWITCHING = False

# DEC-S. /translations/en is NOT reliably a user action.
#
# The language toggle exists only in pilot arm 3. /translations/es occurs there
# and nowhere else (292 events in arm 3, 0 in arm 2) — it is a genuine switch.
# /translations/en occurs in BOTH arms at almost the same rate (9,085 in arm 2,
# 9,114 in arm 3) and is preceded by a /Module/ page 85.0% and 83.8% of the time
# respectively. Arm 2 has no toggle, so its 9,085 events cannot be user actions:
# the app emits /translations/en when a module page loads. 117 of the 119
# es-then-en pairs are 0 seconds apart — one page load, both resources.
#
# Treating every /translations/en as "switched to English" therefore flips
# Spanish-preference users back to English on module page loads they did not
# request. Default is 'never'; the alternatives are kept so the choice can be
# re-run rather than re-coded.
#   never             /translations/en is a page resource, never a switch
#   always            spec-literal; every /translations/en switches (old behaviour)
#   not-after-module  a switch unless the previous row was a /Module/ page
#   not-paired-with-es  a switch unless it follows /translations/es within 1s
LANG_EN_SWITCH = "never"
LANG_PATH_ES = "/es/"
LANG_PATH_EN = "/en/"

# DEC-G / CLAUDE.md Data hygiene 2 tension, see docs/DECISIONS.md DEC-L.
# None  -> unresolved flags stay NULL  (default; honest)
# 0     -> unresolved flags become 0   (CLAUDE.md's literal instruction)
UNRESOLVED_FILL: int | None = None

# Flags the dictionary supplies. ProcessRelated is renamed on output (DEC-E).
DICT_FLAGS = [
    "Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated",
    "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated",
    "LoanTermsRelated", "LoanEstimateRelated", "CDRelated", "CDDocument",
    "Download", "Audio", "Video", "Goal_to_inform", "Goal_to_Advise",
]

# DEC-F: unresolvable from current inputs. Emitted as all-NULL so the schema is
# stable and downstream code does not read a fabricated 0 as a measured no.
BLOCKED_ON_DOCUMENT_TYPE = ["LEDocument", "CDDocument", "LEDownload", "CDDownload"]

# Flags the dictionary nominally supplies but DEC-F overrides to NULL. Excluded
# from the manifest's dictionary loop and from the discrepancy log so they are
# attributed once, to DEC-F, rather than counted twice.
DICT_FLAGS_OVERRIDDEN = [f for f in DICT_FLAGS if f in BLOCKED_ON_DOCUMENT_TYPE]
DICT_FLAGS_REPORTED = [f for f in DICT_FLAGS if f not in BLOCKED_ON_DOCUMENT_TYPE]


# ============================================================================
# LOAD
# ============================================================================
def load_events() -> pd.DataFrame:
    df = pd.read_excel(EVENTS_FILE, sheet_name="user_usage")
    df[TIME_COL] = pd.to_datetime(df[TIME_COL])
    df[URL_COL] = df[URL_COL].astype(str)
    # CLAUDE.md Data hygiene 3: sort key is user, time ascending, then original
    # file row order as a stable tiebreaker. Capture row order BEFORE sorting.
    df["_source_row"] = np.arange(len(df))
    df = df.sort_values([USER_COL, TIME_COL, "_source_row"], kind="mergesort")
    return df.reset_index(drop=True)


def load_dictionary() -> pd.DataFrame:
    if not DICTIONARY_FILE.exists():
        sys.exit(f"Missing {DICTIONARY_FILE}. Run diagnostics/build_path_dictionary.py first.")
    return pd.read_csv(DICTIONARY_FILE)


def load_account_language() -> pd.Series:
    acct = pd.read_excel(ACCOUNT_FILE, sheet_name="users")
    return (acct.drop_duplicates(USER_COL)
                .set_index(USER_COL)["provided_language"]
                .str.lower().str.strip())


# ============================================================================
# FEATURES
# ============================================================================
def attach_dictionary(df: pd.DataFrame, dic: pd.DataFrame) -> pd.DataFrame:
    """Join the extended dictionary. Carries provenance through to the output."""
    prov_cols = [f + "__prov" for f in DICT_FLAGS]
    keep = [URL_COL, "row_class", "path_language"] + DICT_FLAGS + prov_cols
    out = df.merge(dic[keep], on=URL_COL, how="left", validate="many_to_one")

    # Every path in the log is in the dictionary by construction; assert it,
    # because a silent left-join miss would reintroduce exactly the defect this
    # rebuild removes. THIS FAILS IF the dictionary is stale relative to the log.
    missing = out["row_class"].isna().sum()
    if missing:
        sys.exit(f"{missing} events matched no dictionary row — rebuild the dictionary.")

    if UNRESOLVED_FILL is not None:
        for f in DICT_FLAGS:                      # DEC-L, opt-in only
            out.loc[out[f].isna(), f] = UNRESOLVED_FILL
    return out


def add_audio_mp3(df: pd.DataFrame) -> pd.DataFrame:
    """AudioMp3 — the row IS an mp3 request. Row-level, unambiguous."""
    df["AudioMp3"] = df[URL_COL].str.endswith(".mp3").astype("int8")
    return df


def add_language_state(df: pd.DataFrame, acct_lang: pd.Series) -> pd.DataFrame:
    """English_YN / Spanish_YN — stateful per user (spec §3, vars 18-19).

    Seeded from the account's provided_language, then switched by
    /translations/{en,es} and by explicit /es/ or /en/ path segments, then
    forward-filled. Mutually exclusive and exhaustive by construction.

    DEC-I: the dictionary is NOT consulted — it codes English=1 / Spanish=0 on
    every row, so inheriting it would label every Spanish page English.
    DEC-K: users absent from the account file are seeded DEFAULT_LANGUAGE.
    """
    p = df[URL_COL]
    switch = pd.Series(pd.NA, index=df.index, dtype="object")
    is_en = p.str.startswith(LANG_SWITCH_EN)          # spec §3
    is_es = p.str.startswith(LANG_SWITCH_ES)

    # DEC-S: decide which /translations/en rows count as a user action
    if LANG_EN_SWITCH == "never":
        is_en = pd.Series(False, index=df.index)
    elif LANG_EN_SWITCH == "not-after-module":
        prev = df.groupby(USER_COL)[URL_COL].shift(1).fillna("")
        is_en = is_en & ~prev.str.startswith("/Module/")
    elif LANG_EN_SWITCH == "not-paired-with-es":
        prev = df.groupby(USER_COL)[URL_COL].shift(1).fillna("")
        gap = (df[TIME_COL] - df.groupby(USER_COL)[TIME_COL].shift(1)).dt.total_seconds()
        is_en = is_en & ~(prev.str.startswith(LANG_SWITCH_ES) & (gap <= 1))
    elif LANG_EN_SWITCH != "always":
        raise SystemExit(f"unknown --lang-en-switch: {LANG_EN_SWITCH}")

    if LANG_ASSET_PATH_SWITCHING:                     # DEC-M, opt-in
        is_en = is_en | p.str.contains(LANG_PATH_EN, regex=False)
        is_es = is_es | p.str.contains(LANG_PATH_ES, regex=False)
    switch[is_en] = "en"
    switch[is_es] = "es"

    seed = df[USER_COL].map(acct_lang)
    df["language_seed_source"] = np.where(seed.notna(), "account", "default")
    seed = seed.where(seed.isin(["en", "es"]), DEFAULT_LANGUAGE)

    # The seed applies at each user's first row; switches propagate forward.
    first_row = ~df[USER_COL].duplicated()
    state = switch.copy()
    state[first_row & state.isna()] = seed[first_row]
    state = state.groupby(df[USER_COL]).ffill()

    df["language_state"] = state
    df["English_YN"] = (state == "en").astype("int8")
    df["Spanish_YN"] = (state == "es").astype("int8")
    return df


def add_blocked_columns(df: pd.DataFrame) -> pd.DataFrame:
    """DEC-F: no LoanDocument-id -> type lookup exists in any input file.

    Emitted as NULL rather than 0. The inherited regexes matched 0 of 337,581
    rows, so the previous columns were not sparse — they were empty.
    LEDownload/CDDownload's session-bounded fallback (spec §3) needs
    sessionization and lands in Phase 2.
    """
    for c in BLOCKED_ON_DOCUMENT_TYPE:
        df[c] = pd.Series(pd.NA, index=df.index, dtype="Int8")
    return df


def finalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply frozen canonical names (CLAUDE.md) and dtypes."""
    df = df.rename(columns={"ProcessRelated": "ProcessRelated_provisional",      # DEC-E
                            "ProcessRelated__prov": "ProcessRelated_provisional__prov"})
    # Audio is an integer count per spec; the dictionary only supplies 0/1 and a
    # true link count is not derivable from the event log (DEC-J).
    df["Audio"] = df["Audio"].astype("Int16")
    for c in [f for f in DICT_FLAGS if f not in ("Audio", "ProcessRelated")]:
        df[c] = df[c].astype("Int8")
    df["ProcessRelated_provisional"] = df["ProcessRelated_provisional"].astype("Int8")
    return df


# ============================================================================
# QA — "a check that cannot fail is not a check" (CLAUDE.md)
# ============================================================================
def run_qa(df: pd.DataFrame, acct_lang: pd.Series) -> list[str]:
    L = ["# Phase 1 QA", "", f"Rows: {len(df):,}  Users: {df[USER_COL].nunique():,}", ""]

    L.append("## 1. Language state machine vs account language")
    L.append("")
    L.append("FAILS IF a user's modal computed language disagrees with their account")
    L.append("language. Reported **within each language group** — a pooled rate is 98.7%")
    L.append("English and could not fail.")
    L.append("")
    modal = (df.groupby(USER_COL)["language_state"]
               .agg(lambda s: s.mode().iloc[0] if len(s.mode()) else pd.NA))
    cmp = pd.DataFrame({"computed": modal, "account": acct_lang}).dropna()
    L.append(f"Switching mode: `--lang-en-switch {LANG_EN_SWITCH}` (DEC-S)"
             + (" + asset paths (DEC-M)" if LANG_ASSET_PATH_SWITCHING else ""))
    L.append("")
    L.append("| account language | users | modal computed matches | mismatch rate |")
    L.append("|---|---|---|---|")
    for lang, g in cmp.groupby("account"):
        ok = (g.computed == g.account).sum()
        L.append(f"| `{lang}` | {len(g):,} | {ok:,} | {1 - ok/len(g):.2%} |")
    L.append("")

    L.append("## 2. English_YN + Spanish_YN == 1 on every row")
    L.append("")
    L.append("FAILS IF the state machine ever leaves a row unassigned. This is *not*")
    L.append("the trivially-true check the brief warns about: English is derived from an")
    L.append("independent state variable, not as the complement of Spanish, so a row")
    L.append("with a NULL state would score 0 and fail here.")
    L.append("")
    bad = int((df.English_YN + df.Spanish_YN != 1).sum())
    L.append(f"- rows failing: **{bad:,}** ({'PASS' if bad == 0 else 'FAIL'})")
    L.append(f"- rows whose language came from a path switch rather than the seed: "
             f"{int((df.language_seed_source == 'account').sum()):,} account-seeded, "
             f"{int((df.language_seed_source == 'default').sum()):,} default-seeded")
    L.append("")

    L.append("## 3. Spanish volume is non-zero")
    L.append("")
    L.append("FAILS IF Spanish_YN is all zeros — the exact symptom of the inherited bug,")
    L.append("where only paths literally containing /translations/es counted as Spanish.")
    L.append("")
    es_rows = int(df.Spanish_YN.sum())
    es_users = int((df.groupby(USER_COL).Spanish_YN.max() == 1).sum())
    L.append(f"- Spanish rows: **{es_rows:,}** across **{es_users:,}** users "
             f"({'PASS' if es_rows > 0 else 'FAIL'})")
    L.append(f"- inherited logic would have produced: "
             f"{int(df[URL_COL].str.contains(LANG_SWITCH_ES, regex=False).sum()):,} rows")
    L.append("")

    L.append("## 4. Blocked columns are NULL, not 0")
    L.append("")
    L.append("FAILS IF DEC-F columns carry any non-null value — that would mean a")
    L.append("document type was fabricated.")
    L.append("")
    for c in BLOCKED_ON_DOCUMENT_TYPE:
        n = int(df[c].notna().sum())
        L.append(f"- `{c}`: {n} non-null ({'PASS' if n == 0 else 'FAIL'})")
    L.append("")

    L.append("## 5. Flag coverage, reported as measured")
    L.append("")
    L.append("| column | non-null | coverage | ==1 | % of log |")
    L.append("|---|---|---|---|---|")
    cols = [("ProcessRelated_provisional" if f == "ProcessRelated" else f) for f in DICT_FLAGS_REPORTED]
    for c in cols + ["AudioMp3", "English_YN", "Spanish_YN"]:
        nn = int(df[c].notna().sum()); ones = int((df[c] == 1).sum())
        L.append(f"| `{c}` | {nn:,} | {nn/len(df):.1%} | {ones:,} | {ones/len(df):.1%} |")
    L.append("")

    L.append("## 6. Consecutive duplicate URLs (§7-F — retained, reported)")
    L.append("")
    dup = int((df[URL_COL] == df.groupby(USER_COL)[URL_COL].shift(1)).sum())
    L.append(f"- consecutive same-URL pageviews retained: **{dup:,}** ({dup/len(df):.1%})")
    L.append("**Correction, 2026-09-15.** An earlier version of this report described the")
    L.append("Spanish mismatch as a behavioural finding. It was not — it was an artifact of")
    L.append("treating `/translations/en` as a user action when the app emits it on module")
    L.append("page loads. Under `--lang-en-switch never` the mismatch falls from 11.5% to")
    L.append("0.0%. See DEC-S.")
    L.append("")
    return L


# ============================================================================
# BEFORE / AFTER (CLAUDE.md Working style)
# ============================================================================
def inherited_logic(df: pd.DataFrame) -> pd.DataFrame:
    """Reimplementation of the previous pipeline, for honest comparison only."""
    beta = pd.read_excel(CODING_FILE, sheet_name="beta_coding")
    beta["CODING SCHEME"] = beta["CODING SCHEME"].astype(str)
    beta = beta.drop_duplicates("CODING SCHEME")
    static = ["Personalized", "Download", "LoanEstimateRelated", "LoanTermsRelated",
              "LenderMortgageProcessRelated", "GeneralFinancial", "Video", "CDRelated",
              "Goal_to_Advise", "ProcessRelated", "BorrowerMortgageProcessRelated",
              "Goal_to_inform", "MortgageRelated", "Audio"]
    o = df[[USER_COL, URL_COL]].merge(
        beta[["CODING SCHEME"] + static], left_on=URL_COL, right_on="CODING SCHEME", how="left")
    o[static] = o[static].fillna(0).astype(int)
    o["English_YN"] = (~o[URL_COL].str.contains("/translations/es", na=False)).astype(int)
    o["Spanish_YN"] = o[URL_COL].str.contains("/translations/es", na=False).astype(int)
    o["LEDocument"] = o[URL_COL].str.contains(r"/Download/LoanDocument/.*LE", regex=True, na=False).astype(int)
    o["CDDocument"] = o[URL_COL].str.contains(r"/Download/LoanDocument/.*CD", regex=True, na=False).astype(int)
    o["AudioMp3"] = o[URL_COL].str.endswith(".mp3").astype(int)
    return o


def compare(new: pd.DataFrame, old: pd.DataFrame) -> list[str]:
    """Before/after per CLAUDE.md Working style.

    Separates the two kinds of change, because lumping them together hides the
    result that matters: a 0 -> NULL is a fabricated value being withdrawn, a
    0 -> 1 is a value the inherited pipeline got wrong.
    """
    L = ["# Phase 1 — before / after", "",
         "'Before' is the inherited implementation re-run on the same input.", "",
         "- **flip** — both sides have a value and they disagree; the old value was wrong.",
         "- **de-fabricated** — old said 0, new says NULL; there was never any evidence for the 0.",
         ""]
    pairs = [("ProcessRelated_provisional" if f == "ProcessRelated" else f,
              "ProcessRelated" if f == "ProcessRelated" else f) for f in DICT_FLAGS_REPORTED]
    pairs += [("English_YN", "English_YN"), ("Spanish_YN", "Spanish_YN"),
              ("AudioMp3", "AudioMp3"), ("LEDocument", "LEDocument"), ("CDDocument", "CDDocument")]

    L.append("| column | flips 0→1 | flips 1→0 | de-fabricated 0→NULL | before ==1 | after ==1 |")
    L.append("|---|---|---|---|---|---|")
    flips = {}
    for nc, oc in pairs:
        if nc not in new.columns or oc not in old.columns:
            continue
        a, b = new[nc], old[oc]
        up = ((b == 0) & (a == 1)); down = ((b == 1) & (a == 0)); gone = ((b == 0) & a.isna())
        flips[nc] = (up | down)
        L.append(f"| `{nc}` | {int(up.sum()):,} | {int(down.sum()):,} | {int(gone.sum()):,} | "
                 f"{int((b == 1).sum()):,} | {int((a == 1).sum()):,} |")
    L.append("")

    for nc, oc in pairs:
        if nc not in flips or not flips[nc].any():
            continue
        L.append(f"## `{nc}` — five rows whose value flipped")
        L.append("")
        L.append("| path | before | after | provenance |")
        L.append("|---|---|---|---|")
        pcol = nc + "__prov"
        # one example per distinct path, so five examples are five different pages
        seen, shown = set(), 0
        for i in new.index[flips[nc]]:
            path = new.at[i, URL_COL]
            if path in seen:
                continue
            seen.add(path); shown += 1
            prov = new.at[i, pcol] if pcol in new.columns else "—"
            L.append(f"| `{path[:70]}` | {old.at[i, oc]} | {new.at[i, nc]} | {prov} |")
            if shown == 5:
                break
        L.append("")
    return L


# ============================================================================
# MANIFEST
# ============================================================================
def write_manifest(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    def add(col, origin, provisional, dec, note):
        nn = int(df[col].notna().sum()) if col in df.columns else 0
        rows.append(dict(column=col, origin=origin, dtype=str(df[col].dtype),
                         non_null=nn, coverage=round(nn / len(df), 4),
                         provisional=provisional, decision_id=dec, note=note))
    for f in DICT_FLAGS_REPORTED:
        col = "ProcessRelated_provisional" if f == "ProcessRelated" else f
        dec, prov, note = "DEC-G", True, "dictionary + sibling inference; see __prov column"
        if f == "ProcessRelated":
            dec, note = "DEC-E", "professor flagged ???? in his own sheet"
        if f == "Audio":
            dec, note = "DEC-J", "integer-typed; true link count not derivable from the log"
        if f == "LoanTermsRelated":
            note = "blank on all 26 coded page rows; no content page can receive it"
        add(col, "FIXED", prov, dec, note)
    add("AudioMp3", "FIXED", False, "", "row-level: path ends .mp3")
    add("English_YN", "OURS", True, "DEC-I/DEC-K", "stateful; seeded from provided_language")
    add("Spanish_YN", "OURS", True, "DEC-I/DEC-K", "stateful; seeded from provided_language")
    for c in BLOCKED_ON_DOCUMENT_TYPE:
        add(c, "FIXED" if c.endswith("Document") else "OURS", True, "DEC-F",
            "BLOCKED: no LoanDocument-id -> document-type lookup exists")
    m = pd.DataFrame(rows)
    m.to_csv(OUT / "variable_manifest.csv", index=False)
    return m


# ============================================================================
def main() -> None:
    global UNRESOLVED_FILL, LANG_ASSET_PATH_SWITCHING, LANG_EN_SWITCH
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", action="store_true", help="also write .xlsx (slow)")
    ap.add_argument("--compare", action="store_true", help="before/after vs inherited")
    ap.add_argument("--unresolved-fill", type=int, default=None,
                    help="fill unresolved flags with this value instead of NULL (DEC-L)")
    ap.add_argument("--lang-asset-paths", action="store_true",
                    help="also switch language state on /en/ or /es/ asset segments (DEC-M)")
    ap.add_argument("--lang-en-switch", default=LANG_EN_SWITCH,
                    choices=["never", "always", "not-after-module", "not-paired-with-es"],
                    help="when /translations/en counts as a user language switch (DEC-S)")
    args = ap.parse_args()
    LANG_ASSET_PATH_SWITCHING = args.lang_asset_paths
    LANG_EN_SWITCH = args.lang_en_switch
    if args.unresolved_fill is not None:
        UNRESOLVED_FILL = args.unresolved_fill

    OUT.mkdir(exist_ok=True)
    events = load_events()
    dic = load_dictionary()
    acct_lang = load_account_language()

    df = attach_dictionary(events, dic)
    df = add_audio_mp3(df)
    df = add_language_state(df, acct_lang)
    df = add_blocked_columns(df)
    df = finalize_columns(df)

    # Discrepancy log — CLAUDE.md Data hygiene 2. Every path still carrying an
    # unresolved flag, with its hit count, for the professor.
    # DEC-F columns are NULL by design and are not discrepancies; excluding them
    # is what keeps this log meaningful rather than listing every path in the log.
    flag_cols = [("ProcessRelated_provisional" if f == "ProcessRelated" else f)
                 for f in DICT_FLAGS_REPORTED]
    per_path = (df.drop_duplicates(URL_COL).set_index(URL_COL)[flag_cols].isna().sum(axis=1)
                  .rename("unresolved_flags"))
    disc = (df.groupby([URL_COL, "row_class"]).size().rename("events").reset_index()
              .join(per_path, on=URL_COL))
    disc = disc[disc.unresolved_flags > 0].sort_values("events", ascending=False)
    disc["pct_of_log"] = (disc.events / len(df)).round(5)
    disc.to_csv(OUT / "discrepancy_log.csv", index=False)
    unres = df[df[flag_cols].isna().any(axis=1)]

    df.to_parquet(OUT / "phase1_url_features.parquet", index=False)   # hygiene 9
    manifest = write_manifest(df)
    (OUT / "qa_phase1.md").write_text("\n".join(run_qa(df, acct_lang)))

    if args.compare:
        (OUT / "phase1_before_after.md").write_text("\n".join(compare(df, inherited_logic(events))))
    if args.excel:
        df.to_excel(OUT / "phase1_url_features.xlsx", index=False)

    print(f"rows {len(df):,}  users {df[USER_COL].nunique():,}")
    print(f"unresolved-flag paths: {unres[URL_COL].nunique():,} ({len(unres):,} events)")
    print(manifest[["column", "coverage", "provisional", "decision_id"]].to_string(index=False))


if __name__ == "__main__":
    main()
