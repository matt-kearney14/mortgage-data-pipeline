#!/usr/bin/env python3
"""Phase 1 — URL-level characteristics for every event in the clickstream.

Joins output/path_dictionary_extended.csv (built by diagnostics/
build_path_dictionary.py) onto the event log, adds AudioMp3 and the language
state, and writes the event-grain table. Cells marked `context` in the
dictionary (download type, the Dashboard's CD flags) stay NULL here and are
filled once, in phase 2, which needs sessions to decide them.

Run:
    python3 clickstream_processor.py
    python3 clickstream_processor.py --compare     # before/after vs the inherited pipeline
    python3 clickstream_processor.py --excel       # also write .xlsx (slow)
    python3 clickstream_processor.py --pilot-start 2023-05-11   # DEC-AA override

Outputs (output/):
    phase1_url_features.parquet      event grain, one row per log event
    variable_manifest.csv            generated here, never by hand
    discrepancy_log.csv              every path with a flag still NULL after the dictionary
    qa_phase1.md                     QA, reported as measured
    phase1_before_after.md           only with --compare
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import pipeline_common as pc

OUT = pc.OUT
USER_COL, TIME_COL, URL_COL = pc.USER, pc.TIME, pc.PATH

# DEC-K: users absent from talkument_useraccount.xlsx start in this language.
DEFAULT_LANGUAGE = "en"
# DEC-AB: language files requested within this many seconds of each other, or of
# a page, belong to one page load.
LANG_LOAD_WINDOW_S = 2

DICT_FLAGS = pc.FLAGS + ["Audio"]


def out_name(flag: str) -> str:
    """Output column name for a dictionary flag (DEC-E renames ProcessRelated)."""
    return "ProcessRelated_provisional" if flag == "ProcessRelated" else flag


# ============================================================================
# LOAD
# ============================================================================
def load_dictionary() -> pd.DataFrame:
    if not pc.DICTIONARY_FILE.exists():
        raise SystemExit(f"Missing {pc.DICTIONARY_FILE}. Run diagnostics/build_path_dictionary.py first.")
    return pd.read_csv(pc.DICTIONARY_FILE)


def load_account_language() -> pd.Series:
    acct = pd.read_excel(pc.ACCOUNT_FILE, sheet_name="users")
    return (acct.drop_duplicates(USER_COL).set_index(USER_COL)["provided_language"]
                .str.lower().str.strip())


# ============================================================================
# FEATURES
# ============================================================================
def attach_dictionary(df: pd.DataFrame, dic: pd.DataFrame) -> pd.DataFrame:
    """Join the path dictionary, carrying every __prov column through."""
    keep = [URL_COL, "row_class", "template"] + [c for f in DICT_FLAGS for c in (f, f + "__prov")]
    out = df.merge(dic[keep], on=URL_COL, how="left", validate="many_to_one")
    # THIS FAILS IF the dictionary is stale relative to the log: a silent miss
    # would turn into a NULL that looks like an unclassified page.
    missing = int(out["row_class"].isna().sum())
    if missing:
        raise SystemExit(f"{missing} events matched no dictionary row — rebuild the dictionary.")
    return out


def add_audio_mp3(df: pd.DataFrame) -> pd.DataFrame:
    """AudioMp3 — the row IS an mp3 request. Row-level, unambiguous."""
    df["AudioMp3"] = df[URL_COL].str.endswith(".mp3").astype("int8")
    return df


def add_language_state(df: pd.DataFrame, acct_lang: pd.Series) -> pd.DataFrame:
    """English_YN / Spanish_YN — the language each page was shown in (spec §3,
    vars 18-19). DEC-AB.

    The app requests its language files (/translations/en, /translations/es)
    as part of loading a page. Requests within LANG_LOAD_WINDOW_S seconds of
    each other are one load; a load that includes /translations/es means the
    page was shown in Spanish, one with only /translations/en means English.
    (In Spanish the app requests both files in the same second, so a lone
    /translations/en is a genuine English load and a paired one is not.)

    A load sets the language of the page it belongs to — the page within the
    same window — and every page after it, until the next load says otherwise.
    Each user starts in their account language (DEC-K: English if none).
    """
    p = df[URL_COL]
    is_tr = p.str.startswith(pc.TRANSLATION_PREFIX)
    tr = df.loc[is_tr, [USER_COL, TIME_COL]].copy()
    tr["lang"] = np.where(p[is_tr].str.startswith("/translations/es"), "es", "en")
    gap = tr.groupby(USER_COL)[TIME_COL].diff().dt.total_seconds()
    tr["load"] = (gap.isna() | (gap > LANG_LOAD_WINDOW_S)).cumsum()
    tr["load_lang"] = tr.groupby("load").lang.transform(lambda s: "es" if (s == "es").any() else "en")

    state = pd.Series(pd.NA, index=df.index, dtype="object")
    state[is_tr] = tr.load_lang
    # the page that owns a load: nearest language request within the window
    pages = df.loc[~is_tr, [USER_COL, TIME_COL]].reset_index().sort_values(TIME_COL, kind="mergesort")
    if len(tr):
        own = pd.merge_asof(pages, tr[[USER_COL, TIME_COL, "load_lang"]].sort_values(TIME_COL),
                            on=TIME_COL, by=USER_COL, direction="nearest",
                            tolerance=pd.Timedelta(seconds=LANG_LOAD_WINDOW_S))
        own = own.set_index("index").load_lang.dropna()
        state[own.index] = own

    seed = df[USER_COL].map(acct_lang)
    df["language_seed_source"] = np.where(seed.notna(), "account", "default")
    seed = seed.where(seed.isin(["en", "es"]), DEFAULT_LANGUAGE)
    first_row = ~df[USER_COL].duplicated()
    state[first_row & state.isna()] = seed[first_row & state.isna()]
    state = state.groupby(df[USER_COL]).ffill()

    df["language_load"] = np.where(is_tr, "request", None)
    df["language_state"] = state
    df["English_YN"] = (state == "en").astype("int8")
    df["Spanish_YN"] = (state == "es").astype("int8")
    return df


def finalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply frozen canonical names (CLAUDE.md) and dtypes."""
    df = df.rename(columns={"ProcessRelated": "ProcessRelated_provisional",      # DEC-E
                            "ProcessRelated__prov": "ProcessRelated_provisional__prov"})
    df["Audio"] = df["Audio"].astype("Int16")                                     # a count (DEC-AE)
    for f in pc.FLAGS:
        c = out_name(f)
        df[c] = df[c].astype("Int8")
    return df


# ============================================================================
# QA — "a check that cannot fail is not a check" (CLAUDE.md)
# ============================================================================
def run_qa(df: pd.DataFrame, acct_lang: pd.Series, info: dict) -> list[str]:
    pv = df[df.row_class != "page_resource"]
    L = ["# Phase 1 QA", "",
         f"Rows: {len(df):,}  Users: {df[USER_COL].nunique():,}  "
         f"(pageviews, excluding page resources: {len(pv):,})", "",
         f"Pre-pilot test users removed (DEC-AA, pilot start {info['pilot_start'].date()}): "
         f"{len(info['test_users'])} users, {info['test_events']:,} events.", ""]

    L += ["## 1. Language state vs account language", "",
          "FAILS IF a user's modal computed language disagrees with their account language. "
          "Reported **within each language group** — pooled, the population is nearly all "
          "English and the check could not fail.", "",
          "| account language | users | modal computed matches | mismatch rate |", "|---|---|---|---|"]
    modal = (pv.groupby(USER_COL)["language_state"]
               .agg(lambda s: s.mode().iloc[0] if len(s.mode()) else pd.NA))
    cmp = pd.DataFrame({"computed": modal, "account": acct_lang}).dropna()
    for lang, g in cmp.groupby("account"):
        ok = int((g.computed == g.account).sum())
        L.append(f"| `{lang}` | {len(g):,} | {ok:,} | {1 - ok/len(g):.2%} |")
    L.append("")

    L += ["## 2. Spanish audio clips are not language evidence (DEC-AB)", "",
          "Reported, not a pass/fail. Spanish clips (/audio/faq/es/) are also played by "
          "borrowers who never load the Spanish interface, so a clip's language is a "
          "per-clip choice and does not move the language state.", ""]
    es_clip = df[URL_COL].str.contains("/audio/faq/es/", regex=False)
    in_en = es_clip & (df.language_state == "en")
    L += [f"- Spanish clip plays: {int(es_clip.sum()):,}; in the Spanish state "
          f"{int((es_clip & (df.language_state == 'es')).sum()):,}; in the English state "
          f"{int(in_en.sum()):,}, by {df.loc[in_en, USER_COL].nunique():,} borrower(s)", ""]

    L += ["## 3. English_YN + Spanish_YN == 1 on every row", "",
          "FAILS IF the state machine leaves a row unassigned. English comes from an independent "
          "state, not as the complement of Spanish, so a NULL state would score 0 and fail.", ""]
    bad = int((df.English_YN + df.Spanish_YN != 1).sum())
    L += [f"- rows failing: **{bad:,}** ({'PASS' if bad == 0 else 'FAIL'})",
          f"- rows by seed: {int((df.language_seed_source == 'account').sum()):,} account, "
          f"{int((df.language_seed_source == 'default').sum()):,} default (DEC-K)", ""]

    L += ["## 4. Flag coverage on pageviews, as measured", "",
          "`context` cells are filled in phase 2 and are counted separately; anything else "
          "NULL is unresolved.", "",
          "| column | known | context (phase 2) | unresolved | ==1 |", "|---|---|---|---|---|"]
    for f in DICT_FLAGS:
        c = out_name(f)
        prov = pv[c + "__prov"]
        known = pv[c].notna().mean(); ctx = (prov == "context").mean()
        L.append(f"| `{c}` | {known:.1%} | {ctx:.1%} | {1 - known - ctx:.1%} | "
                 f"{(pv[c] == 1).mean():.1%} |")
    L.append("")
    dup = int((df[URL_COL] == df.groupby(USER_COL)[URL_COL].shift(1)).sum())
    L += ["## 5. Consecutive duplicate URLs (§7-F — retained, reported)", "",
          f"- consecutive same-URL rows retained: **{dup:,}** ({dup/len(df):.1%})", ""]
    return L


# ============================================================================
# BEFORE / AFTER (CLAUDE.md Working style)
# ============================================================================
def inherited_logic(df: pd.DataFrame) -> pd.DataFrame:
    """Reimplementation of the inherited pipeline, for honest comparison only."""
    beta = pd.read_excel(pc.CODING_FILE, sheet_name="beta_coding")
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
    """Before/after. A 0 -> NULL is a fabricated value withdrawn; a flip is a
    value the inherited pipeline got wrong (or that we now code differently)."""
    L = ["# Phase 1 — before / after", "",
         "'Before' is the inherited implementation re-run on the same input.", "",
         "| column | flips 0→1 | flips 1→0 | 0→NULL | before ==1 | after ==1 |",
         "|---|---|---|---|---|---|"]
    pairs = [(out_name(f), f) for f in DICT_FLAGS if f in old.columns]
    pairs += [("English_YN", "English_YN"), ("Spanish_YN", "Spanish_YN"), ("AudioMp3", "AudioMp3")]
    flips = {}
    for nc, oc in pairs:
        a, b = new[nc], old[oc]
        if nc == "Audio":                       # now a count: compare "has audio"
            a = (a > 0).astype("Int8").where(a.notna())
        up =(b == 0) & (a == 1); down = (b == 1) & (a == 0); gone = (b == 0) & a.isna()
        flips[nc] = up | down
        L.append(f"| `{nc}` | {int(up.sum()):,} | {int(down.sum()):,} | {int(gone.sum()):,} | "
                 f"{int((b == 1).sum()):,} | {int((a == 1).sum()):,} |")
    L.append("")
    for nc, _ in pairs:
        if not flips[nc].any():
            continue
        L += [f"## `{nc}` — five pages whose value changed", "", "| path | after | source |", "|---|---|---|"]
        shown = new[flips[nc]].drop_duplicates(URL_COL).head(5)
        pcol = nc + "__prov"
        for _, r in shown.iterrows():
            L.append(f"| `{r[URL_COL][:70]}` | {r[nc]} | {r[pcol] if pcol in new else '—'} |")
        L.append("")
    return L


# ============================================================================
# MANIFEST
# ============================================================================
def write_manifest(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    def add(col, origin, provisional, dec, note):
        nn = int(df[col].notna().sum())
        rows.append(dict(column=col, origin=origin, dtype=str(df[col].dtype), non_null=nn,
                         coverage=round(nn / len(df), 4), provisional=provisional,
                         decision_id=dec, note=note))
    for f in pc.FLAGS:
        dec, note = "DEC-AD", "professor's coding, his blanks filled from docs/page_template_coding.csv"
        if f == "ProcessRelated":
            dec, note = "DEC-E/DEC-AD", "professor flagged ???? in his own sheet; his values kept"
        if f in ("LEDocument", "CDDocument"):
            dec, note = "DEC-AC", "the page shows the document; downloads and the Dashboard per event in phase 2"
        add(out_name(f), "FIXED", True, dec, note)
    add("Audio", "FIXED", True, "DEC-AE", "count of distinct clips that play on the page, measured from the log")
    add("AudioMp3", "FIXED", False, "", "row-level: path ends .mp3")
    add("English_YN", "OURS", True, "DEC-AB/DEC-K", "language of the page load, carried forward")
    add("Spanish_YN", "OURS", True, "DEC-AB/DEC-K", "language of the page load, carried forward")
    m = pd.DataFrame(rows)
    m.to_csv(OUT / "variable_manifest.csv", index=False)
    return m


# ============================================================================
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", action="store_true", help="also write .xlsx (slow)")
    ap.add_argument("--compare", action="store_true", help="before/after vs inherited")
    ap.add_argument("--pilot-start", help="override the pilot start date (DEC-AA), YYYY-MM-DD")
    args = ap.parse_args()
    if args.pilot_start:
        pc.PILOT_START = pd.Timestamp(args.pilot_start)
    pc.check_inputs([pc.EVENTS_FILE, pc.ACCOUNT_FILE, pc.LOAN_FILE, pc.CODING_FILE, pc.TEMPLATE_FILE])

    OUT.mkdir(exist_ok=True)
    events, info = pc.load_events()
    acct_lang = load_account_language()

    df = attach_dictionary(events, load_dictionary())
    df = add_audio_mp3(df)
    df = add_language_state(df, acct_lang)
    df = finalize_columns(df)

    # Discrepancy log — CLAUDE.md Data hygiene 2: every path with a flag that is
    # still NULL for a reason other than phase-2 context or being a page resource.
    flag_cols = [out_name(f) for f in DICT_FLAGS]
    unresolved = pd.concat([df[c + "__prov"].eq("unresolved") for c in flag_cols], axis=1).any(axis=1)
    disc = (df[unresolved].groupby([URL_COL, "row_class"]).size().rename("events").reset_index()
              .sort_values(["events", URL_COL], ascending=[False, True], kind="mergesort"))
    disc["pct_of_log"] = (disc.events / len(df)).round(5)
    disc.to_csv(OUT / "discrepancy_log.csv", index=False)

    df.to_parquet(OUT / "phase1_url_features.parquet", index=False)   # hygiene 9
    import json                                     # read by phase 2 for the Read Me
    (OUT / "run_info.json").write_text(json.dumps(dict(
        pilot_start=str(info["pilot_start"].date()), test_users=len(info["test_users"]),
        test_events=info["test_events"], raw_events=info["raw_events"]), indent=1))
    manifest = write_manifest(df)
    (OUT / "qa_phase1.md").write_text("\n".join(run_qa(df, acct_lang, info)))
    if args.compare:
        (OUT / "phase1_before_after.md").write_text("\n".join(compare(df, inherited_logic(events))))
    if args.excel:
        df.to_excel(OUT / "phase1_url_features.xlsx", index=False)

    print(f"rows {len(df):,}  users {df[USER_COL].nunique():,}  "
          f"(removed {len(info['test_users'])} test users, {info['test_events']:,} events)")
    print(f"paths with an unresolved flag: {len(disc):,} ({int(disc.events.sum()):,} events)")
    print(manifest[["column", "coverage", "decision_id"]].to_string(index=False))


if __name__ == "__main__":
    main()
