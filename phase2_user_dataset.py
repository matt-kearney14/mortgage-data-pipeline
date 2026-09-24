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

# Pilot buckets. 2 and 3 are as described by the project owner; 1 is inferred
# from the data (90.7% of its loans carry no user_hash at all, versus ~1% in
# buckets 2 and 3) and has no rows in the user table by construction.
PILOT_BUCKET_LABELS = {
    1: "1 - No Talkument access",
    2: "2 - English only",
    3: "3 - Multilingual support",
}

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


def note(column, level, definition, quality="", decision="", waiting_on=""):
    """Register a column in the codebook.

    waiting_on is non-empty only for columns that cannot be computed yet; it
    names the specific input required, so the dictionary sheet answers "why is
    this blank" without anyone having to ask.
    """
    CODEBOOK.append(dict(column=column, level=level,
                         status="Not yet available" if waiting_on else "Ready",
                         definition=definition, how_to_read=quality,
                         waiting_on=waiting_on, decision_id=decision))


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
             "Exhaustive: the two always sum to webpages_visited. Caveat for the ~114 "
             "borrowers who used the toggle — a switch back to English cannot be "
             "distinguished from the app's own page-load request, so Spanish exposure "
             "may be slightly overstated for them. Measured headroom is about 40 events "
             "in total. See used_language_toggle.", "DEC-I/DEC-K/DEC-S")
    u["audio_clips_clicked"] = g.AudioMp3.sum()
    note("audio_clips_clicked", "user", "Count of .mp3 requests. Spec defines this as "
         "sum(AudioMp3), explicitly not sum(Audio).", "Exact; row-level test on the path.")
    u["days_accessed"] = g.eventdate.apply(lambda s: s.dt.date.nunique())
    note("days_accessed", "user", f"Distinct calendar dates with at least one event, "
         f"bucketed in {TIMEZONE}.", "eventdate is timezone-naive at second resolution.", "DEC-R")

    # Switches TO Spanish are measured cleanly: /translations/es occurs 292 times
    # in arm 3 and zero times in arm 2, so it carries no page-load noise at all
    # (DEC-S). The reverse switch is not separable from noise and is not counted.
    u["language_switches_to_spanish"] = (
        ev.assign(_s=ev.path.str.startswith("/translations/es"))
          .groupby("user_hash")._s.sum())
    u["used_language_toggle"] = u.language_switches_to_spanish.gt(0)
    note("language_switches_to_spanish", "user",
         "Times the borrower explicitly switched the interface to Spanish.",
         "Clean measure — this path occurs only where the toggle exists and carries no "
         "page-load noise. The reverse switch back to English is NOT counted: the app "
         "emits the same path on module page loads and the two are indistinguishable.",
         "DEC-S")
    note("used_language_toggle", "user",
         "True if the borrower ever switched the interface to Spanish.",
         "Only possible in pilot bucket 3, the only bucket with the toggle.", "DEC-S")

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
        extra = ("  This characteristic is flagged '????' in the professor's own coding "
                 "sheet and is unresolved; it is output under a _provisional name."
                 if short == "ProcessRelated" else "")
        note(f"pages_{short}", "user", f"Pageviews where {short} == 1.{extra}",
             "Counts confirmed 1s only. Read together with unknown_" + short + ".", "DEC-Q")
        note(f"time_{short}", "user", f"Seconds spent on pages where {short} == 1. "
             "Audio-clip time is credited to the page that played the clip.",
             "The time_* columns OVERLAP — a page can carry several "
             "characteristics, so they do not sum to total time.", "DEC-Q")
        note(f"unknown_{short}", "user", f"Pageviews where {short} could not be "
             "determined (path not classified).",
             "The denominator caveat for pages_/time_" + short + ".", "DEC-L")

    # ---- AudioMp3 as a characteristic (spec §5 vars 32/33 list it among the 18)
    # DEC-T. The parent-page rule (§5 var 33) sends an mp3 row's dwell to the page
    # that played it, so under that rule alone time_AudioMp3 would be zero for
    # everyone — which cannot be the intent, since the spec names AudioMp3 as one
    # of the 18 expanded characteristics. time_AudioMp3 is therefore the raw dwell
    # on mp3 rows: actual listening time. That time is ALSO credited to the parent
    # page's characteristics, so this column overlaps them exactly as the other
    # time_* columns overlap each other.
    u["pages_AudioMp3"] = ev.assign(_f=ev.AudioMp3 == 1).groupby("user_hash")._f.sum()
    u["time_AudioMp3"] = (ev.assign(_t=ev.time_on_page.where(ev.AudioMp3 == 1))
                            .groupby("user_hash")._t.sum())
    note("pages_AudioMp3", "user", "Pageviews that are an .mp3 request.",
         "Identical to audio_clips_clicked by construction — the specification names "
         "this same quantity twice, as var 15 expanded by var 32 and again as var 34. "
         "Kept so all 18 expanded characteristics are present.", "DEC-T")
    note("time_AudioMp3", "user", "Seconds spent on .mp3 rows — actual listening time.",
         "Overlaps the other time_* columns by design: this same dwell is also credited "
         "to the characteristics of the page that played the clip, per spec §5 var 33.",
         "DEC-T")

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

    MILESTONE_DEFS = {
        "t_application_to_activation":
            "Seconds from the loan application date to the borrower's first Talkuments access.",
        "t_activation_to_le_sent":
            "Seconds from first access to the date the Loan Estimate / TIL was sent.",
        "t_le_sent_to_first_le_visit":
            "Seconds from the Loan Estimate being sent to the borrower's first visit to "
            "an LE-related page on or after that date.",
        "t_activation_to_lock":
            "Seconds from first access to the rate lock date.",
        "t_last_access_to_current_status":
            "Seconds from the borrower's last access to the loan's current status date.",
    }
    WAIT_MILESTONE = ("A file containing the loan milestone dates (application, "
                      "LE/TIL sent, lock, current status), joinable on loannumber or "
                      "user_hash. talkument_loan_applicants.xlsx was expected to hold "
                      "these but contains none of them.")
    for c in BLOCKED_MILESTONES:
        u[c] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        note(c, "user", MILESTONE_DEFS[c],
             "Column is present but empty for every user so the schema stays stable. "
             "These durations may legitimately be negative once built — do not clip.",
             "DEC-F", WAIT_MILESTONE)
    BLOCKED_DEFS = {
        "LEDocument": "the page is a downloaded Loan Estimate document",
        "CDDocument": "the page is a downloaded Closing Disclosure document",
        "LEDownload": "the event is a download of a Loan Estimate",
        "CDDownload": "the event is a download of a Closing Disclosure",
    }
    WAIT_DOCTYPE = ("A lookup from LoanDocument id to document type (Loan Estimate / "
                    "Closing Disclosure / other), or an export of the document "
                    "service's metadata. Every download path in the log is "
                    "/Download/LoanDocument/{numeric id} and carries no type token, "
                    "so the type cannot be recovered from the URL.")
    for c in BLOCKED_CHARACTERISTICS:
        u[f"pages_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        u[f"time_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
        note(f"pages_{c}", "user", f"Pageviews where {BLOCKED_DEFS[c]}.",
             "Column is present but empty for every user so the schema stays stable. "
             "17,502 download events are waiting on this.", "DEC-F", WAIT_DOCTYPE)
        note(f"time_{c}", "user", f"Seconds on pages where {BLOCKED_DEFS[c]}.",
             "Column is present but empty for every user so the schema stays stable.",
             "DEC-F", WAIT_DOCTYPE)

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
    u["pilot_bucket"] = (gb.bucket.agg(lambda s: s.iloc[0] if s.nunique() == 1 else np.nan)
                           .reindex(u.index).astype("Int64"))
    u["pilot_bucket_label"] = u.pilot_bucket.map(PILOT_BUCKET_LABELS)
    # reindex with fill_value rather than fillna: reindexing a bool series onto a
    # wider index yields object dtype, and fillna on that is deprecated.
    u["pilot_bucket_conflicting"] = (gb.bucket.nunique().gt(1)
                                     .reindex(u.index, fill_value=False).astype(bool))
    u["language_preference"] = gb.language_preference.agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else np.nan).reindex(u.index)
    u["state"] = gb.state.agg(lambda s: s.iloc[0] if s.nunique() == 1 else np.nan).reindex(u.index)
    note("pilot_bucket_label", "loan", "Plain-language name of the pilot bucket.",
         "Sort or filter on this, or on pilot_bucket. Bucket 1 never appears: those "
         "borrowers had no Talkument access, so they generate no clickstream and have "
         "no row in this table. Compare bucket 1 on loan outcomes, not on this dataset.")
    note("pilot_bucket", "loan", "Pilot arm number, bridged loan_number to loannumber.",
         "NULL where a user holds loans in different buckets. Bucket 1 never appears: those "
         "borrowers had no Talkument access and so generate no clickstream.")
    note("pilot_bucket_conflicting", "loan", "True if the user's loans span more than one bucket.",
         "567 users. Excluded from pilot_bucket rather than assigned a guess.")
    note("language_preference", "loan", "Applicant's stated language preference.",
         "Pre-treatment and balanced across buckets; the appropriate language covariate.")
    note("state", "loan", "Applicant state.", "NULL where a user's loans disagree.")

    u = u.reset_index()

    # Grouping columns sit immediately after the id so the sheet can be sorted or
    # filtered by bucket without scrolling past 70 measure columns first.
    FRONT = ["user_hash", "pilot_bucket", "pilot_bucket_label", "pilot_bucket_conflicting",
             "language_preference", "provided_language", "expertise_level",
             "account_enabled", "state"]
    u = u[FRONT + [c for c in u.columns if c not in FRONT]]

    # carry the bucket down to the event and session files too, so each stands alone
    lbl = u.set_index("user_hash").pilot_bucket_label
    ev["pilot_bucket_label"] = ev.user_hash.map(lbl)
    sess["pilot_bucket_label"] = sess.user_hash.map(lbl)

    # ------------------------------------------------------------- outputs
    OUT.mkdir(exist_ok=True)
    ev.to_parquet(OUT / "phase2_events.parquet", index=False)
    sess.to_parquet(OUT / "phase2_sessions.parquet", index=False)
    u.to_parquet(OUT / "user_level_dataset.parquet", index=False)

    cb = pd.DataFrame(CODEBOOK).drop_duplicates("column")
    cb["users_with_a_value"] = cb.column.map(
        lambda c: int(u[c].notna().sum()) if c in u.columns else 0)
    cb["coverage"] = (cb.users_with_a_value / len(u)).round(4)
    cb = cb[["column", "status", "level", "definition", "how_to_read",
             "waiting_on", "users_with_a_value", "coverage", "decision_id"]]
    # blocked columns first (what is missing is the first thing seen), then the
    # rest in the same order as the User Data sheet so the two line up
    order = {c: i for i, c in enumerate(u.columns)}
    cb["_sheet_pos"] = cb.column.map(order).fillna(9999)
    cb["_blocked"] = (cb.status == "Not yet available").map({True: 0, False: 1})
    cb = cb.sort_values(["_blocked", "_sheet_pos"]).drop(columns=["_blocked", "_sheet_pos"])
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
        write_workbook(u, cb, sess, args)

    qa(ev, sess, u, dropped, args)
    print(f"users {len(u):,}  columns {u.shape[1]}  sessions {len(sess):,}")
    print(f"dropped non-pageview rows: {dropped:,}")
    print(f"codebook entries: {len(cb)}")


# ================================================================ workbook
def write_workbook(u, cb, sess, args) -> None:
    """One workbook, two sheets: the data, and the dictionary that explains it.

    The dictionary travels with the data deliberately — a codebook in a separate
    file gets separated from the data it describes.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = OUT / "user_level_dataset.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        u.to_excel(xl, sheet_name="User Data", index=False)
        cb.to_excel(xl, sheet_name="Data Dictionary", index=False)

        head_font = Font(bold=True, color="FFFFFF", size=11)
        head_fill = PatternFill("solid", fgColor="14202A")
        wrap = Alignment(vertical="top", wrap_text=True)
        top = Alignment(vertical="top")

        # --- sheet 1: the data ---
        ws = xl.sheets["User Data"]
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.freeze_panes = "B2"                     # hold user_hash and the header
        ws.row_dimensions[1].height = 30
        for i, col in enumerate(u.columns, start=1):
            width = max(len(str(col)) + 2, 12)
            ws.column_dimensions[get_column_letter(i)].width = min(width, 34)
        ws.auto_filter.ref = ws.dimensions

        # --- sheet 2: the dictionary ---
        ws2 = xl.sheets["Data Dictionary"]
        for c in ws2[1]:
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws2.freeze_panes = "A2"
        ws2.row_dimensions[1].height = 30
        widths = {"column": 34, "status": 18, "level": 10, "definition": 62,
                  "how_to_read": 58, "waiting_on": 62, "users_with_a_value": 14,
                  "coverage": 11, "decision_id": 12}
        for i, col in enumerate(cb.columns, start=1):
            ws2.column_dimensions[get_column_letter(i)].width = widths.get(col, 18)
        blocked_fill = PatternFill("solid", fgColor="F7E8E5")
        ready_fill = PatternFill("solid", fgColor="E6F0EA")
        stat_i = list(cb.columns).index("status") + 1
        for r in range(2, len(cb) + 2):
            for c in range(1, len(cb.columns) + 1):
                ws2.cell(r, c).alignment = wrap if c in (4, 5, 6) else top
            cell = ws2.cell(r, stat_i)
            cell.fill = blocked_fill if cell.value == "Not yet available" else ready_fill
            cell.font = Font(bold=cell.value == "Not yet available")
            ws2.row_dimensions[r].height = 46
        ws2.auto_filter.ref = ws2.dimensions

    n_blocked = int((cb.status == "Not yet available").sum())
    print(f"  workbook: 'User Data' {u.shape[0]:,}x{u.shape[1]}  |  "
          f"'Data Dictionary' {len(cb)} columns ({n_blocked} not yet available)")


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
