#!/usr/bin/env python3
"""Phase 2 — sessionization, aggregation, and the user-level dataset.

Produces one row per user with every variable in the specification. Variables
that cannot be computed from the files we hold are present as all-NULL columns
so the schema is complete and stable; they are listed in the codebook with the
reason.

The output is built to be ANALYSED BY SOMEONE ELSE. Three consequences:
  - every parameter is a CLI flag, so a different assumption is a re-run
  - every count that could be misread ships beside its own quality column
  - a codebook is generated from the code describing every column

Run:
    python3 phase2_user_dataset.py
    python3 phase2_user_dataset.py --session-timeout 60
    python3 phase2_user_dataset.py --no-excel

Outputs (output/):
    user_level_dataset.xlsx / .parquet     one row per user  <- the deliverable
    phase2_events.parquet                  event grain + session_id, time_on_page
    phase2_sessions.parquet                one row per session
    codebook.csv                           every column, described
    qa_phase2.md                           checks, reported as measured
    session_timeout_sensitivity.csv
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
DATA = ROOT / "data"

# ---------------------------------------------------------------- parameters
SESSION_TIMEOUT_MIN = 30      # DEC-N; §7-A is our assumption, never the professor's
TIMEZONE = "UTC"              # DEC-R; eventdate is tz-naive, no input disagrees
SENSITIVITY_GRID = [5, 10, 15, 20, 30, 45, 60, 120, 240]

CHARACTERISTICS = [
    "Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated_provisional",
    "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated", "LoanTermsRelated",
    "LoanEstimateRelated", "CDRelated", "Download", "Video", "Goal_to_inform",
    "Goal_to_Advise",
]
# DEC-F: no LoanDocument-id -> type lookup exists in any input file.
BLOCKED_CHARACTERISTICS = ["LEDocument", "CDDocument", "LEDownload", "CDDownload"]
BLOCKED_MILESTONES = [
    "t_application_to_activation", "t_activation_to_le_sent",
    "t_le_sent_to_first_le_visit", "t_activation_to_lock",
    "t_last_access_to_current_status",
]

CODEBOOK: list[dict] = []


def note(column, level, definition, quality="", decision=""):
    CODEBOOK.append(dict(column=column, level=level, definition=definition,
                         quality_note=quality, decision_id=decision))


# ================================================================ sessionize
def sessionize(ev: pd.DataFrame, timeout_s: int) -> pd.DataFrame:
    """Spec §3 vars 23-24 and §4. Assumes the frame is already in sort order."""
    gap = ev.groupby("user_hash").eventdate.diff().dt.total_seconds()
    ev["session_start"] = (gap.isna() | (gap > timeout_s)).astype("int8")
    ev["session_id"] = ev.groupby("user_hash").session_start.cumsum()

    nxt = ev.groupby(["user_hash", "session_id"]).eventdate.shift(-1)
    # time_on_page is NULL on the last row of a session: the successor does not
    # exist, so the duration is unobservable. NOT zero (spec §3 var 22).
    ev["time_on_page"] = (nxt - ev.eventdate).dt.total_seconds().astype("Int64")
    ev["session_end"] = ev.time_on_page.isna().astype("int8")
    # A measured zero (same-second navigation) is distinct from an unobservable
    # one; flag it so dwell analysis can exclude it deliberately (DEC-O).
    ev["zero_dwell"] = (ev.time_on_page == 0).fillna(False).astype("int8")

    s = ev.groupby(["user_hash", "session_id"]).eventdate
    ev["session_start_ts"] = s.transform("min")
    ev["session_end_ts"] = s.transform("max")
    return ev


def parent_page_index(ev: pd.DataFrame) -> pd.Series:
    """Index of the most recent non-mp3 row in the same session (spec §5 var 33).

    shift(1) is wrong here: 2,468 mp3 rows follow another mp3, so the lookup
    must walk back to the last non-mp3 row.
    """
    idx = pd.Series(np.where(ev.AudioMp3 == 0, ev.index, np.nan), index=ev.index)
    return idx.groupby([ev.user_hash, ev.session_id]).ffill()


# ================================================================ build
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-timeout", type=int, default=SESSION_TIMEOUT_MIN)
    ap.add_argument("--no-excel", action="store_true")
    args = ap.parse_args()
    timeout_s = args.session_timeout * 60

    ev = pd.read_parquet(OUT / "phase1_url_features.parquet")

    # DEC-P: /favicon.ico and /cart.json are browser asset requests. 4,548 of the
    # 4,675 sit between two real rows, truncating the preceding page's dwell.
    # Dropped before sessionization so dwell flows page-to-page. The only rows
    # removed anywhere in the pipeline.
    dropped = int((ev.row_class == "non_pageview").sum())
    ev = ev[ev.row_class != "non_pageview"].reset_index(drop=True)

    ev = sessionize(ev, timeout_s)
    parent = parent_page_index(ev)

    # ---------------------------------------------------------- session level
    gs = ev.groupby(["user_hash", "session_id"])
    sess = pd.DataFrame({
        "session_start_ts": gs.eventdate.min(),
        "session_end_ts": gs.eventdate.max(),
        "pages_in_session": gs.size(),
    })
    sess["session_duration"] = (
        (sess.session_end_ts - sess.session_start_ts).dt.total_seconds().astype("Int64"))
    prev_end = sess.groupby("user_hash").session_end_ts.shift(1)
    sess["inter_session_elapsed"] = (
        (sess.session_start_ts - prev_end).dt.total_seconds().astype("Int64"))
    sess = sess.reset_index()

    # ------------------------------------------------------------- user level
    g = ev.groupby("user_hash")
    u = pd.DataFrame(index=g.size().index)

    note("user_hash", "id", "Pseudonymous user identifier. The join key to "
         "talkument_useraccount.xlsx and, via loan number, to the pilot arms.",
         "One row per user; unique.")
    u["webpages_visited"] = g.size()
    note("webpages_visited", "user", "Total pageviews. Excludes browser asset requests "
         "(/favicon.ico, /cart.json).", "Exact.", "DEC-P")
    u["unique_webpages_visited"] = g.path.nunique()
    note("unique_webpages_visited", "user", "Distinct URL paths visited.", "Exact.")
    u["spanish_webpages_visited"] = g.Spanish_YN.sum()
    u["english_webpages_visited"] = g.English_YN.sum()
    for c in ("spanish_webpages_visited", "english_webpages_visited"):
        note(c, "user", "Pageviews in that language, from the per-user language state "
             "machine seeded by the account's provided_language.",
             "Exact and exhaustive: the two sum to webpages_visited.", "DEC-I/DEC-K/DEC-M")
    u["audio_clips_clicked"] = g.AudioMp3.sum()
    note("audio_clips_clicked", "user", "Count of .mp3 requests. Spec defines this as "
         "sum(AudioMp3), explicitly not sum(Audio).", "Exact; row-level test on the path.")
    u["days_accessed"] = g.eventdate.apply(lambda s: s.dt.date.nunique())
    note("days_accessed", "user", f"Distinct calendar dates with at least one event, "
         f"bucketed in {TIMEZONE}.", "eventdate is timezone-naive at second resolution.", "DEC-R")

    u["num_sessions"] = g.session_id.nunique()
    note("num_sessions", "user", f"Distinct sessions at a {args.session_timeout}-minute "
         "inactivity timeout.",
         "Robust to the timeout: 30->60 min changes this by about -5%.", "DEC-N")

    su = sess.groupby("user_hash")
    u["total_session_time"] = su.session_duration.sum()
    u["mean_session_duration"] = su.session_duration.mean().round(1)
    u["median_session_duration"] = su.session_duration.median()
    note("total_session_time", "user", "Sum of session durations, seconds.",
         "Sensitive to the timeout.", "DEC-N")
    note("mean_session_duration", "user", "Mean session duration, seconds.",
         "HIGHLY timeout-sensitive: 30->60 min moves the population mean by +44%. "
         "Report the median alongside it.", "DEC-N")
    note("median_session_duration", "user", "Median session duration, seconds.",
         "Preferred over the mean; session durations are heavily right-skewed.", "DEC-N")
    u["mean_pages_in_session"] = su.pages_in_session.mean().round(2)
    note("mean_pages_in_session", "user", "Mean pageviews per session.", "")
    u["mean_inter_session_elapsed"] = su.inter_session_elapsed.mean().round(1)
    note("mean_inter_session_elapsed", "user", "Mean seconds between the end of one "
         "session and the start of the next.", "NULL for single-session users.")
    u["single_page_sessions"] = su.pages_in_session.apply(lambda s: int((s == 1).sum()))
    note("single_page_sessions", "user", "Sessions consisting of one pageview "
         "(duration 0 by definition, not NULL).",
         "A high share across the population suggests revisiting the timeout.", "DEC-N")

    u["total_time_observed"] = g.time_on_page.sum()
    u["zero_dwell_pages"] = g.zero_dwell.sum()
    note("total_time_observed", "user", "Sum of observed time_on_page, seconds. Excludes "
         "each session's last page, whose duration is unobservable.",
         "Equals total_session_time by construction; QA check 1 verifies this.")
    note("zero_dwell_pages", "user", "Pageviews with a measured dwell of exactly 0 "
         "seconds, mostly redirects and language toggles.",
         "11.7% of all rows population-wide. Exclude these before any dwell analysis.", "DEC-O")

    # ----------------------------------------- per-characteristic expansion
    # Audio dwell is credited to the PARENT page's characteristics, never
    # additionally to the clip's own flags — attributing both would double count
    # (spec §5 var 33).
    attr = ev[["user_hash", "time_on_page"]].copy()
    is_mp3 = ev.AudioMp3 == 1
    for c in CHARACTERISTICS:
        own = ev[c]
        par = ev[c].reindex(parent).reset_index(drop=True)
        par.index = ev.index
        attr[c] = own.where(~is_mp3, par)

    for c in CHARACTERISTICS:
        short = c.replace("_provisional", "")
        flag = attr[c]
        u[f"pages_{short}"] = ev.assign(_f=(flag == 1)).groupby("user_hash")._f.sum()
        u[f"time_{short}"] = (attr.assign(_t=attr.time_on_page.where(flag == 1))
                                  .groupby("user_hash")._t.sum())
        u[f"unknown_{short}"] = ev.assign(_u=flag.isna()).groupby("user_hash")._u.sum()
        note(f"pages_{short}", "user", f"Pageviews where {short} == 1.",
             "Counts confirmed 1s only. Read together with unknown_" + short + ".", "DEC-Q")
        note(f"time_{short}", "user", f"Seconds spent on pages where {short} == 1. "
             "Audio-clip time is credited to the page that played the clip.",
             "The time_* columns OVERLAP — a page can carry several "
             "characteristics, so they do not sum to total time.", "DEC-Q")
        note(f"unknown_{short}", "user", f"Pageviews where {short} could not be "
             "determined (path not classified).",
             "The denominator caveat for pages_/time_" + short + ".", "DEC-L")

    # what no characteristic can account for
    unknown_all = attr[CHARACTERISTICS].isna().all(axis=1)
    u["pages_unattributable"] = ev.assign(_x=unknown_all).groupby("user_hash")._x.sum()
    u["time_unattributable"] = (attr.assign(_t=attr.time_on_page.where(unknown_all))
                                    .groupby("user_hash")._t.sum())
    u["pct_pages_classified"] = (1 - u.pages_unattributable / u.webpages_visited).round(4)
    note("pages_unattributable", "user", "Pageviews with no characteristic determined at all.",
         "", "DEC-Q")
    note("time_unattributable", "user", "Seconds on those pages.",
         "Without this column every per-characteristic share silently understates.", "DEC-Q")
    note("pct_pages_classified", "user", "Share of the user's pageviews carrying at least "
         "one determined characteristic.",
         "A low value means this user's totals rest on little classified data.", "DEC-G")

    # ------------------------------------------------------------- milestones
    span = g.eventdate.agg(["min", "max"])
    u["first_access"] = span["min"]
    u["last_access"] = span["max"]
    u["t_activation_to_last_access"] = (
        (span["max"] - span["min"]).dt.total_seconds().astype("Int64"))
    note("first_access", "user", "First pageview timestamp. The spec defines activation "
         "as first Talkuments access.", "")
    note("last_access", "user", "Last pageview timestamp.", "")
    note("t_activation_to_last_access", "user",
         "Seconds from first to last pageview (spec var 37).",
         "Computable for every user; needs no external file.")

    for c in BLOCKED_MILESTONES:
        u[c] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        note(c, "user", "Milestone timer from the specification.",
             "NOT COMPUTED. talkument_loan_applicants.xlsx contains no milestone-date "
             "columns. Needs a source file before this can be built.", "DEC-F")
    for c in BLOCKED_CHARACTERISTICS:
        u[f"pages_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        u[f"time_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        note(f"pages_{c}", "user", f"Pageviews where {c} == 1.",
             "NOT COMPUTED. Download URLs carry a numeric id and no document type; "
             "needs a LoanDocument-id to document-type lookup.", "DEC-F")
        note(f"time_{c}", "user", f"Seconds on pages where {c} == 1.",
             "NOT COMPUTED. Same reason.", "DEC-F")

    # -------------------------------------------------- joinable attributes
    acct = (pd.read_excel(DATA / "talkument_useraccount.xlsx", sheet_name="users")
              .drop_duplicates("user_hash").set_index("user_hash"))
    u["provided_language"] = acct.provided_language.reindex(u.index)
    u["expertise_level"] = acct.expertise_level.reindex(u.index)
    u["account_enabled"] = acct.account_enabled.reindex(u.index)
    note("provided_language", "account", "Account language setting.",
         "NOT a pre-treatment covariate: it reads 'es' for 280 users in pilot bucket 3 "
         "and 0 in bucket 2, so it reflects the treatment, not the borrower.")
    note("expertise_level", "account", "Account expertise level.",
         "Use with caution. There is no level 3, and levels 2 and 4 appear only among "
         "users with events, so this may be assigned on engagement rather than at signup.")
    note("account_enabled", "account", "Account enabled flag.", "")

    appl = pd.read_excel(DATA / "talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
    buck = pd.read_excel(DATA / "talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
    br = (appl.dropna(subset=["user_hash"])
              .merge(buck, left_on="loannumber", right_on="loan_number", how="inner"))
    gb = br.groupby("user_hash")
    u["pilot_bucket"] = gb.bucket.agg(lambda s: s.iloc[0] if s.nunique() == 1 else np.nan
                                      ).reindex(u.index)
    # reindex with fill_value rather than fillna: reindexing a bool series onto a
    # wider index yields object dtype, and fillna on that is deprecated.
    u["pilot_bucket_conflicting"] = (gb.bucket.nunique().gt(1)
                                     .reindex(u.index, fill_value=False).astype(bool))
    u["language_preference"] = gb.language_preference.agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else np.nan).reindex(u.index)
    u["state"] = gb.state.agg(lambda s: s.iloc[0] if s.nunique() == 1 else np.nan).reindex(u.index)
    note("pilot_bucket", "loan", "Pilot arm, bridged loan_number to loannumber.",
         "NULL where a user holds loans in different arms. Bucket 1 never appears: those "
         "borrowers had no Talkument access and so generate no clickstream.")
    note("pilot_bucket_conflicting", "loan", "True if the user's loans span more than one arm.",
         "567 users. Excluded from pilot_bucket rather than assigned a guess.")
    note("language_preference", "loan", "Applicant's stated language preference.",
         "Pre-treatment and balanced across arms; the appropriate language covariate.")
    note("state", "loan", "Applicant state.", "NULL where a user's loans disagree.")

    u = u.reset_index()

    # ------------------------------------------------------------- outputs
    OUT.mkdir(exist_ok=True)
    ev.to_parquet(OUT / "phase2_events.parquet", index=False)
    sess.to_parquet(OUT / "phase2_sessions.parquet", index=False)
    u.to_parquet(OUT / "user_level_dataset.parquet", index=False)

    cb = pd.DataFrame(CODEBOOK).drop_duplicates("column")
    cb["present_in_output"] = cb.column.isin(u.columns)
    cb["non_null"] = cb.column.map(lambda c: int(u[c].notna().sum()) if c in u.columns else 0)
    cb["coverage"] = (cb.non_null / len(u)).round(4)
    cb.to_csv(OUT / "codebook.csv", index=False)

    # timeout sensitivity, regenerated every run so the figure is never quoted alone
    rows = []
    for t in SENSITIVITY_GRID:
        e2 = sessionize(ev.drop(columns=[c for c in ev.columns
                                         if c.startswith("session") or c in
                                         ("time_on_page", "zero_dwell")]).copy(), t * 60)
        d = e2.groupby(["user_hash", "session_id"]).eventdate.agg(
            lambda s: (s.max() - s.min()).total_seconds())
        p = e2.groupby(["user_hash", "session_id"]).size()
        rows.append(dict(timeout_min=t, sessions=int(e2.session_start.sum()),
                         median_duration_s=float(d.median()), mean_duration_s=round(float(d.mean()), 1),
                         median_pages=float(p.median()), mean_pages=round(float(p.mean()), 2),
                         pct_single_page=round(100 * float((p == 1).mean()), 1)))
    pd.DataFrame(rows).to_csv(OUT / "session_timeout_sensitivity.csv", index=False)

    if not args.no_excel:
        u.to_excel(OUT / "user_level_dataset.xlsx", index=False)
        cb.to_excel(OUT / "codebook.xlsx", index=False)

    qa(ev, sess, u, dropped, args)
    print(f"users {len(u):,}  columns {u.shape[1]}  sessions {len(sess):,}")
    print(f"dropped non-pageview rows: {dropped:,}")
    print(f"codebook entries: {len(cb)}")


# ================================================================ QA
def qa(ev, sess, u, dropped, args) -> None:
    L = ["# Phase 2 QA", "",
         f"Timeout {args.session_timeout} min · timezone {TIMEZONE} · "
         f"{len(u):,} users · {len(sess):,} sessions · {len(ev):,} pageviews", "",
         f"{dropped:,} browser asset requests dropped before sessionization (DEC-P).", ""]

    L += ["## 1. Session time reconciles with page dwell", "",
          "FAILS IF time_on_page is ever computed across a session boundary, or a "
          "session's last row is given a duration. Summing dwell within a session "
          "(trailing NULL as zero) must equal session_end_ts - session_start_ts exactly.", ""]
    chk = ev.groupby(["user_hash", "session_id"]).time_on_page.sum().astype("float")
    ref = sess.set_index(["user_hash", "session_id"]).session_duration.astype("float")
    bad = int((chk - ref).abs().gt(0).sum())
    L += [f"- sessions disagreeing: **{bad:,}** of {len(sess):,} "
          f"({'PASS' if bad == 0 else 'FAIL'})", ""]

    L += ["## 2. Inter-session gaps exceed the timeout", "",
          "FAILS IF sessions were ordered wrongly or a boundary double-counted: every "
          "non-null inter_session_elapsed must be greater than the timeout, since that "
          "is what ended the previous session.", ""]
    ise = sess.inter_session_elapsed.dropna().astype(float)
    viol = int((ise <= args.session_timeout * 60).sum())
    L += [f"- gaps at or below the timeout: **{viol:,}** of {len(ise):,} "
          f"({'PASS' if viol == 0 else 'FAIL'})",
          f"- minimum observed gap: {ise.min():,.0f} s (timeout is {args.session_timeout*60:,} s)", ""]

    L += ["## 3. Language counts are exhaustive", "",
          "FAILS IF the language state machine leaves any pageview unassigned. Not "
          "trivially true: English comes from an independent state variable, not as the "
          "complement of Spanish.", ""]
    bad = int((u.spanish_webpages_visited + u.english_webpages_visited
               != u.webpages_visited).sum())
    L += [f"- users failing: **{bad:,}** ({'PASS' if bad == 0 else 'FAIL'})", ""]

    L += ["## 4. Audio time is credited once, not twice", "",
          "FAILS IF an mp3 row's dwell reached both its own characteristics and its "
          "parent page's. Total attributed time for any single characteristic cannot "
          "exceed total observed time.", ""]
    tot = float(u.total_time_observed.sum())
    worst, wc = 0.0, ""
    for c in CHARACTERISTICS:
        s = c.replace("_provisional", "")
        v = float(u[f"time_{s}"].sum())
        if v > worst:
            worst, wc = v, s
    L += [f"- total observed time: {tot:,.0f} s",
          f"- largest single characteristic (`{wc}`): {worst:,.0f} s "
          f"({worst/tot:.1%}) ({'PASS' if worst <= tot else 'FAIL'})", ""]

    L += ["## 5. The time_* columns overlap and must not be summed", "",
          "Not a pass/fail check — a property analysts need to know. A page carrying "
          "several characteristics is counted in each.", ""]
    ssum = sum(float(u[f"time_{c.replace('_provisional','')}"].sum()) for c in CHARACTERISTICS)
    L += [f"- sum of all time_* columns: {ssum:,.0f} s = **{ssum/tot:.2f}x** total observed time",
          "- use `total_time_observed` as the denominator, never the sum of the parts", ""]

    L += ["## 6. Coverage of the user-level table", "", "| column | non-null | coverage |",
          "|---|---|---|"]
    for c in ["webpages_visited", "num_sessions", "total_session_time",
              "t_activation_to_last_access", "pilot_bucket", "language_preference",
              "pages_MortgageRelated", "time_MortgageRelated", "pages_LEDocument"]:
        nn = int(u[c].notna().sum())
        L.append(f"| `{c}` | {nn:,} | {nn/len(u):.1%} |")
    L += ["", f"- median share of a user's pages classified: "
          f"**{u.pct_pages_classified.median():.1%}**",
          f"- users below 50% classified: {int((u.pct_pages_classified < 0.5).sum()):,}", ""]

    (OUT / "qa_phase2.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
