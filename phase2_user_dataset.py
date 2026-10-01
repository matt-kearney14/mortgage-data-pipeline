#!/usr/bin/env python3
"""Phase 2 — sessionization, aggregation, and the user-level dataset.

Produces one row per user with every variable in the specification. Variables
that cannot be computed from the files we hold are present as all-NULL columns
so the schema is complete and stable; they are listed in the codebook with the
reason.

The output is built to be ANALYSED BY SOMEONE ELSE. Three consequences:
  - the session timeout is a CLI flag, so a different assumption is a re-run
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
# DEC-R. A declaration, not a conversion: eventdate is tz-naive and is bucketed
# as-is. Changing this string changes only the labels in the codebook and QA.
TIMEZONE = "UTC"
SENSITIVITY_GRID = [5, 10, 15, 20, 30, 45, 60, 120, 240]
# DEC-Z: requested by the app on module page load; not a pageview (DEC-S evidence).
TRANSLATION_RESOURCE = "/translations/en"
# DEC-X / DEC-Z: download context from a page coded both LE- and CD-related.
BOTH_LE_CD_CONTEXT = "LE"

# Pilot buckets. CONFIRMED 2026-09-29 against data/loan_application_data_partial.csv,
# which names them directly: bucket 1 = "no talkument", 2 = "talkument",
# 3 = "talkument_multi". The crosstab against pilot_buckets is perfectly
# diagonal across all 25,318 loans, so these labels are measured, not inferred.
PILOT_BUCKET_LABELS = {
    1: "1 - No Talkument",
    2: "2 - Talkument (English)",
    3: "3 - Talkument multilingual",
}

LOAN_FILE = DATA / "loan_application_data_partial.csv"
# Two date formats coexist in that file (DEC-U).
DATE_COLS = {"Application_Date": "%m/%d/%Y",
             "Current_Status_Date": "%d%b%Y %H:%M:%S",
             "LE_TIL_Sent_Date": "%d%b%Y %H:%M:%S",
             "Lock_Date": "%d%b%Y %H:%M:%S"}

CHARACTERISTICS = [
    "Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated_provisional",
    "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated", "LoanTermsRelated",
    "LoanEstimateRelated", "CDRelated", "Download", "Video", "Goal_to_inform",
    "Goal_to_Advise",
]
# DEC-F: no LoanDocument-id -> type lookup exists in any input file, so the two
# *Document columns stay all-NULL and the two *Download columns are inferred (DEC-X).
DOCUMENT_CHARACTERISTICS = ["LEDocument", "CDDocument", "LEDownload", "CDDownload"]
MILESTONES = [
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


def single_or_null(s: pd.Series):
    """The value if every row agrees, else NULL — never a guess between them."""
    return s.iloc[0] if s.nunique() == 1 else np.nan


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
    ap.add_argument("--keep-translation-resources", action="store_true",
                    help="count /translations/en rows as pageviews and credit them "
                         "dwell (pre-audit behaviour; see DEC-Z)")
    args = ap.parse_args()
    timeout_s = args.session_timeout * 60

    ev = pd.read_parquet(OUT / "phase1_url_features.parquet")

    # DEC-P: /favicon.ico and /cart.json are browser asset requests. 4,548 of the
    # 4,675 sit between two real rows, truncating the preceding page's dwell.
    # Dropped before sessionization so dwell flows page-to-page.
    #
    # DEC-Z (audit, 2026-10-01): /translations/en is the same kind of row. DEC-S
    # established that the app requests it when a module page loads (bucket 2,
    # which has no language toggle, emits it at the same rate as bucket 3), so it
    # is not a page the borrower viewed. Left in, it takes the module's reading
    # time: the module row is followed by /translations/en a median 1 s later, and
    # the /translations/en row then carries the dwell — 291 hours, 8.9% of all
    # observed time, credited to a row with no characteristics. Dropped here,
    # exactly like the favicon, unless --keep-translation-resources.
    resource = ev.row_class == "non_pageview"
    if not args.keep_translation_resources:
        resource |= ev.path == TRANSLATION_RESOURCE
    dropped = int(resource.sum())
    ev = ev[~resource]

    # Spec §1 sort key, enforced here rather than trusted from Phase 1: every
    # order-dependent step below (sessions, dwell, parent page, download context)
    # depends on it. _source_row is unique, so the order is total.
    ev = (ev.sort_values(["user_hash", "eventdate", "_source_row"], kind="mergesort")
            .reset_index(drop=True))

    ev = sessionize(ev, timeout_s)
    ev = infer_download_type(ev)          # DEC-X
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
         "(/favicon.ico, /cart.json) and, unless --keep-translation-resources, the "
         "/translations/en resource the app requests on module page load.",
         "Exact. Not the raw row count of the log (spec var 28): those rows are not "
         "pages the borrower viewed.", "DEC-P/DEC-Z")
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
    # pages_C (spec §5 var 32) is sum(flag) over the user's OWN rows: an mp3 row
    # counts under the clip's own flags. time_C (var 33) credits an mp3 row's dwell
    # to its PARENT page's characteristics instead, never additionally to the
    # clip's own — attributing both would double count. An mp3 with no parent in
    # its session (the session opens on audio) is credited its own flags, as §5
    # prescribes, and counted in QA. AUDIT 2026-10-01: previously pages_C and
    # unknown_C also used the parent's flags, contradicting var 32 and the
    # codebook, and orphan mp3 dwell was sent to "unknown" instead of own flags.
    attr = ev[["user_hash", "time_on_page"]].copy()
    is_mp3 = ev.AudioMp3 == 1
    orphan_mp3 = is_mp3 & parent.isna()
    for c in CHARACTERISTICS:
        own = ev[c]
        par = ev[c].reindex(parent).reset_index(drop=True)
        par.index = ev.index
        attr[c] = own.where(~is_mp3 | orphan_mp3, par)

    for c in CHARACTERISTICS:
        short = c.replace("_provisional", "")
        own, flag = ev[c], attr[c]
        u[f"pages_{short}"] = ev.assign(_f=(own == 1)).groupby("user_hash")._f.sum()
        u[f"time_{short}"] = (attr.assign(_t=attr.time_on_page.where(flag == 1))
                                  .groupby("user_hash")._t.sum())
        u[f"unknown_{short}"] = ev.assign(_u=own.isna()).groupby("user_hash")._u.sum()
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

    # what no characteristic can account for: pages on the row's own flags (as
    # pages_C), time on the attributed flags (as time_C)
    u["pages_unattributable"] = (ev.assign(_x=ev[CHARACTERISTICS].isna().all(axis=1))
                                   .groupby("user_hash")._x.sum())
    unknown_attr = attr[CHARACTERISTICS].isna().all(axis=1)
    u["time_unattributable"] = (attr.assign(_t=attr.time_on_page.where(unknown_attr))
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
    loans = load_loans()
    for c in MILESTONES:
        note(c, "user", MILESTONE_DEFS[c],
             "Signed seconds; negative values are real and must not be clipped. "
             "Computed from the user's EARLIEST pilot loan by application date "
             "(DEC-V); see loans_in_pilot for users holding several.", "DEC-V")
    DOC_DEFS = {
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
    INFERRED_CAVEAT = (
        "INFERRED, NOT MEASURED. The download URL carries no document type, so the "
        "type is taken from the last Loan Estimate or Closing Disclosure page the "
        "borrower viewed in the same session, then settled per document by majority "
        "vote. Covers 82.5% of download events; the rest are NULL, not guessed. "
        "Validated at 87.0% self-consistency, and Closing Disclosure downloads land "
        "a median 12 days later than Loan Estimate ones, as they should. Set "
        "downloads_type_inferred to 0 to exclude these entirely.")
    for c in DOCUMENT_CHARACTERISTICS:
        if c in ("LEDownload", "CDDownload"):
            flag = (ev[c].fillna(False)).astype(bool)
            u[f"pages_{c}"] = ev.assign(_f=flag).groupby("user_hash")._f.sum()
            u[f"time_{c}"] = (ev.assign(_t=ev.time_on_page.where(flag))
                                .groupby("user_hash")._t.sum())
            note(f"pages_{c}", "user", f"Downloads where {DOC_DEFS[c]}.",
                 INFERRED_CAVEAT, "DEC-X")
            note(f"time_{c}", "user", f"Seconds on downloads where {DOC_DEFS[c]}.",
                 INFERRED_CAVEAT, "DEC-X")
        else:
            u[f"pages_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
            u[f"time_{c}"] = pd.Series(pd.NA, index=u.index, dtype="Int64")
            note(f"pages_{c}", "user", f"Pageviews where {DOC_DEFS[c]}.",
                 "NOT COMPUTED — kept as the measured-only counterpart of "
                 f"pages_{c.replace('Document','Download')}, which is inferred. In this "
                 "data the two would be identical row-for-row, since every borrower "
                 "document path is already a download.", "DEC-F", WAIT_DOCTYPE)
            note(f"time_{c}", "user", f"Seconds on pages where {DOC_DEFS[c]}.",
                 "NOT COMPUTED. Same reason.", "DEC-F", WAIT_DOCTYPE)

    u["downloads_type_inferred"] = (ev.assign(_x=ev.download_type_inferred.fillna(False))
                                      .groupby("user_hash")._x.sum())
    note("downloads_type_inferred", "user",
         "How many of the borrower's downloads had their type inferred.",
         "Use this to exclude inferred values: pages_LEDownload and pages_CDDownload "
         "are built entirely from these events.", "DEC-X")

    # AUDIT 2026-10-01: a download whose type could not be inferred was scored
    # False in both LEDownload and CDDownload, so pages_LEDownload = 0 could mean
    # "no LE download" or "a download of unknown type" (964 users). This is the
    # unknown_ companion every other pages_ column already has.
    is_doc = ev.path.str.startswith("/Download/LoanDocument/")
    u["downloads_type_unknown"] = (ev.assign(_x=is_doc & ev.download_type_inferred.isna())
                                     .groupby("user_hash")._x.sum())
    note("downloads_type_unknown", "user",
         "Borrower-document downloads whose type could not be inferred.",
         "The unknown_ companion of pages_LEDownload / pages_CDDownload: those count "
         "confirmed (inferred) cases only, so 0 there with a non-zero value here means "
         "'type unknown', not 'did not download'.", "DEC-X")

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
    u["pilot_bucket"] = gb.bucket.agg(single_or_null).reindex(u.index).astype("Int64")
    u["pilot_bucket_label"] = u.pilot_bucket.map(PILOT_BUCKET_LABELS)
    # reindex with fill_value rather than fillna: reindexing a bool series onto a
    # wider index yields object dtype, and fillna on that is deprecated.
    # AUDIT 2026-10-01: users with no bucket at all are NULL here, not False —
    # "not conflicting" is a claim about loans we cannot see.
    u["pilot_bucket_conflicting"] = gb.bucket.nunique().gt(1).reindex(u.index).astype("boolean")
    u["language_preference"] = gb.language_preference.agg(single_or_null).reindex(u.index)
    u["state"] = gb.state.agg(single_or_null).reindex(u.index)
    note("pilot_bucket_label", "loan", "Plain-language name of the pilot bucket.",
         "Sort or filter on this, or on pilot_bucket. Bucket 1 never appears: those "
         "borrowers had no Talkument access, so they generate no clickstream and have "
         "no row in this table. Compare bucket 1 on loan outcomes, not on this dataset.")
    note("pilot_bucket", "loan", "Pilot arm number, bridged loan_number to loannumber.",
         "NULL where a user holds loans in different buckets. Bucket 1 never appears: those "
         "borrowers had no Talkument access and so generate no clickstream.")
    note("pilot_bucket_conflicting", "loan", "True if the user's loans span more than one bucket.",
         f"{int(u.pilot_bucket_conflicting.sum()):,} users; excluded from pilot_bucket rather "
         "than assigned a guess. NULL for users linked to no pilot loan at all.")
    note("language_preference", "loan", "Applicant's stated language preference.",
         "Pre-treatment and balanced across buckets; the appropriate language covariate.")
    note("state", "loan", "Applicant state.", "NULL where a user's loans disagree.")

    # ------------------------------------------- loan outcomes & milestones
    u = attach_loan_data(u, ev, loans, appl)

    u = u.reset_index()

    # Grouping columns sit immediately after the id so the sheet can be sorted or
    # filtered by bucket without scrolling past 70 measure columns first.
    FRONT = ["user_hash", "pilot_bucket", "pilot_bucket_label", "pilot_bucket_conflicting",
             "loan_status", "loan_originated", "borrower_language", "language_preference",
             "activated_talkument", "provided_language",
             "expertise_level", "account_enabled", "state"]
    FRONT = [c for c in FRONT if c in u.columns]
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
        write_workbook(u, cb)

    qa(ev, sess, u, dropped, args)
    print(f"users {len(u):,}  columns {u.shape[1]}  sessions {len(sess):,}")
    print(f"dropped non-pageview rows: {dropped:,}")
    print(f"codebook entries: {len(cb)}")


# ================================================================ downloads
def infer_download_type(ev: pd.DataFrame) -> pd.DataFrame:
    """Spec §3 fallback: classify a document download when the URL cannot.

    Download paths are /Download/LoanDocument/{id} and carry no type token, so
    LE and CD downloads are indistinguishable from the URL (DEC-F). §3 prescribes
    a fallback — use the most recent LE-related or CD-related pageview within the
    same session — and requires a `download_type_inferred` flag on the output.

    Two steps, both validated (DEC-X):
      1. session context   the last LE/CD page before the download, same session
      2. document vote     a document has ONE type, so take the majority across
                           all its downloads and apply it everywhere, which both
                           removes self-contradiction and lifts coverage

    A third step — placing unlabelled documents by id proximity to a labelled one
    — was tested and REJECTED: 62.6% holdout accuracy against 50% chance, with
    1,009 of 2,154 LE documents misclassified. It is not used.
    """
    is_doc = ev.path.str.startswith("/Download/LoanDocument/")

    le = (ev.LoanEstimateRelated.fillna(0) == 1).values
    cd = (ev.CDRelated.fillna(0) == 1).values
    # A page flagged BOTH LoanEstimateRelated and CDRelated counts as LE context
    # (BOTH_LE_CD_CONTEXT). AUDIT 2026-10-01: this tie-break is the method, not a
    # corner case. Only three paths carry both flags — /Module/your-loan-estimate-
    # made-clear, /Module/people-and-process and a -1 variant — but they are the
    # context for 7,157 of the 7,161 LE-labelled download events. Strictly LE-only
    # context accounts for 4. "LE" therefore means "last LE/CD page was one of
    # those modules"; see DEC-Z.
    both = le & cd
    ctx = pd.Series(np.where(both, BOTH_LE_CD_CONTEXT,
                             np.where(le, "LE", np.where(cd, "CD", None))), index=ev.index)
    # the last qualifying page BEFORE this row, bounded by the session
    prior = (ctx.groupby([ev.user_hash, ev.session_id]).shift(1)
                .groupby([ev.user_hash, ev.session_id]).ffill())

    doc_id = ev.path.str.extract(r"/(\d+)$")[0]
    seen = pd.DataFrame({"doc_id": doc_id[is_doc], "guess": prior[is_doc]}).dropna()
    tally = seen.groupby(["doc_id", "guess"]).size().unstack(fill_value=0)
    for c in ("LE", "CD"):
        if c not in tally:
            tally[c] = 0
    winner = pd.Series(np.where(tally.LE > tally.CD, "LE",
                       np.where(tally.CD > tally.LE, "CD", None)), index=tally.index)

    typ = pd.Series(pd.NA, index=ev.index, dtype="object")
    typ[is_doc] = doc_id[is_doc].map(winner)

    ev["LEDownload"] = pd.array(np.where(typ.isna(), pd.NA, typ == "LE"), dtype="boolean")
    ev["CDDownload"] = pd.array(np.where(typ.isna(), pd.NA, typ == "CD"), dtype="boolean")
    ev["download_type_inferred"] = pd.array(np.where(typ.notna(), True, pd.NA), dtype="boolean")
    return ev


# ================================================================ loans
def load_loans():
    """The loan-application extract: outcomes, milestone dates, rate and credit.

    Required. Without it the loan outcome and milestone columns cannot be built,
    and a deliverable silently missing them is worse than a failed run.
    """
    if not LOAN_FILE.exists():
        raise SystemExit(f"Missing {LOAN_FILE}: loan outcomes and milestone dates "
                         "come only from this file.")
    d = pd.read_csv(LOAN_FILE)
    d = d[d.Loan_Number.notna()].copy()        # 39 trailing blank rows
    d["loannumber"] = d.Loan_Number.astype("int64").astype(str)
    for col, fmt in DATE_COLS.items():
        d[col] = pd.to_datetime(d[col], format=fmt, errors="coerce")
    # DEC-U: implausible values are nulled, not clipped, and counted in QA.
    d.loc[d.APR > 30, "APR"] = np.nan
    d.loc[d.Credit_Score_Decision < 300, "Credit_Score_Decision"] = np.nan
    return d


def attach_loan_data(u, ev, loans, appl):
    """Loan outcomes and spec vars 38-42, attributed to one loan per borrower.

    DEC-V: a borrower's loans are ordered by Application_Date and the EARLIEST
    is used. 7.2% of borrowers hold more than one, and `loans_in_pilot` exposes
    that so anyone can exclude them.
    """
    link = appl.dropna(subset=["user_hash"])[["user_hash", "loannumber"]].copy()
    link["loannumber"] = link.loannumber.astype(str)
    link = link.drop_duplicates()
    m = link.merge(loans, on="loannumber", how="inner")

    m = m.sort_values(["user_hash", "Application_Date"], kind="mergesort")
    primary = m.drop_duplicates("user_hash", keep="first").set_index("user_hash")
    counts = m.groupby("user_hash").loannumber.nunique()

    u["loans_in_pilot"] = counts.reindex(u.index).astype("Int64")
    note("loans_in_pilot", "user", "Number of the borrower's loans present in the "
         "loan-application extract.",
         "Greater than 1 for about 7% of borrowers. All loan-level columns below "
         "describe only the EARLIEST of them by application date.", "DEC-V")

    # DEC-Y: the lender's own loan-level language field. It reproduces the paper's
    # Table 2 exactly, so it is the field to use for loan-level work; the applicant
    # file's language_preference stays as the per-person alternative.
    LANG = {"SpanishIndicator": "Spanish", "EnglishIndicator": "English",
            "LanguageRefusalIndicator": "Refusal"}
    blp = primary.Borrower_Language_Preference.reindex(u.index)
    u["borrower_language"] = blp.map(lambda v: LANG.get(v, None if pd.isna(v) else "Other"))
    note("borrower_language", "loan",
         "The lender's language preference for the loan's borrower.",
         "PREFER THIS for loan-level work: it reproduces the paper's Table 2 exactly, "
         "to two decimals in all 12 cells. It agrees with the applicant file's "
         "language_preference on 99.72% of loans; the 68 disagreements are mostly "
         "loans where a co-applicant prefers Spanish but the borrower does not. Blank "
         "where no language was recorded — the paper counts those under 'Other'.",
         "DEC-Y")

    # DEC-Y: activation status, with UNKNOWN kept distinct from NO.
    at = primary.Activated_Talkument.reindex(u.index)
    u["activated_talkument"] = pd.array(
        np.where(at.isna(), pd.NA, at.eq("Yes")), dtype="boolean")
    note("activated_talkument", "loan",
         "Whether anyone on the loan activated Talkuments, per the lender.",
         "BLANK means unknown, NOT 'no' — scoring blanks as 'no' understates activation "
         "by about 1.2 points across the pilot. Agrees with our own first_login measure "
         "on all 16,953 loans where both exist, with zero disagreements. NOTE: every "
         "borrower in this file demonstrably used the software, so the 333 blanks here "
         "are gaps in the lender's field that our clickstream resolves, not "
         "non-activations.", "DEC-Y")

    STATUS = {"loan_status": "Loan_Status", "hmda_loan_type": "HMDA_Loan_Type",
              "hmda_loan_purpose": "HMDA_Loan_Purpose"}
    for out, src in STATUS.items():
        u[out] = primary[src].reindex(u.index)
    st = primary.Loan_Status.reindex(u.index)
    u["loan_originated"] = pd.array(st.eq("Loan Originated").where(st.notna()), dtype="boolean")
    u["coapplicant"] = primary.Coapplicant.reindex(u.index).astype("Int64")
    for out, src in [("credit_score", "Credit_Score_Decision"),
                     ("interest_rate", "Interest_Rate"), ("apr", "APR")]:
        u[out] = primary[src].reindex(u.index)

    note("loan_status", "loan", "Final disposition of the loan application.",
         "Six values; 'Loan Originated' is the funded outcome. THE STUDY'S OUTCOME "
         "VARIABLE.", "DEC-V")
    note("loan_originated", "loan", "True if loan_status is 'Loan Originated'.",
         "Convenience binary over loan_status.", "DEC-V")
    note("hmda_loan_type", "loan", "HMDA loan type: FHA, Conventional, VA, USDA-RHS or FSA.", "")
    note("hmda_loan_purpose", "loan",
         "HMDA purpose: Home Purchase, Cash-out refinancing, Refinancing, Home Improvement.", "")
    note("coapplicant", "loan", "Lender's co-applicant flag for the loan.",
         "Does NOT agree with counting distinct applicant hashes in "
         "talkument_loan_applicants.xlsx — 2,303 loans flagged 1 show a single hash "
         "and 1,601 flagged 0 show two. Prefer this field; see DEC-W.", "DEC-W")
    note("credit_score", "loan", "Credit score used for the decision.",
         "7 loans carried a 0 and were nulled rather than clipped (DEC-U).", "DEC-U")
    note("interest_rate", "loan", "Note rate.",
         "Null for 53% of loans — a rate exists only once the loan is locked.", "DEC-U")
    note("apr", "loan", "Annual percentage rate.",
         "One loan carried 1200.0 and was nulled rather than clipped (DEC-U).", "DEC-U")

    # ---- spec vars 38-42 -------------------------------------------------
    first = u["first_access"]; last = u["last_access"]
    app = primary.Application_Date.reindex(u.index)
    le = primary.LE_TIL_Sent_Date.reindex(u.index)
    lock = primary.Lock_Date.reindex(u.index)
    status = primary.Current_Status_Date.reindex(u.index)

    def secs(a, b):
        return (a - b).dt.total_seconds().astype("Int64")

    u["t_application_to_activation"] = secs(first, app)
    u["t_activation_to_le_sent"] = secs(le, first)
    u["t_activation_to_lock"] = secs(lock, first)
    u["t_last_access_to_current_status"] = secs(status, last)

    # var 40: first LE-related pageview at or after the LE was sent (spec §5).
    # Users whose only LE activity predates the send date get NULL, and that
    # count is itself reported — it is a behavioural finding, not missingness.
    le_ev = ev[ev.LoanEstimateRelated == 1][["user_hash", "eventdate"]]
    le_map = le_ev.merge(le.rename("le_sent"), left_on="user_hash", right_index=True, how="inner")
    after = le_map[le_map.eventdate >= le_map.le_sent]
    first_after = after.groupby("user_hash").eventdate.min()
    u["t_le_sent_to_first_le_visit"] = secs(first_after.reindex(u.index), le)
    return u


def build_notes(u) -> pd.DataFrame:
    """The 'Read Me First' sheet. The professor reads the workbook, not the repo,
    so every caveat that could change a conclusion has to live here."""
    R = []
    def H(t): R.append(("", t, "H"))
    def N(k, v): R.append((k, v, "N"))
    def W(k, v): R.append((k, v, "W"))

    H("What this file is")
    N("Grain", f"ONE ROW PER BORROWER who opened Talkuments — {len(u):,} people, "
               f"{u.shape[1]} columns. It is NOT one row per loan.")
    N("Sheets", "'User Data' is the data. 'Data Dictionary' defines every column, "
                "says how to read it, and names what any empty column is waiting on.")
    N("Source", "Talkument clickstream (337,581 events), the borrower account file, "
                "the pilot bucket assignment, and the loan application extract.")

    H("Five things to check before you analyse")
    W("1. Person vs loan",
      "This file is per person; activation tables in the paper are per loan. A loan can "
      "have several applicants and a person can hold several loans, so the two give "
      "different rates — we reconciled 46.3% (per person) against 54.23% (per loan). "
      "Aggregate to the loan before comparing against loan-level tables.")
    W("2. Do not sum the time_ columns",
      "A page can carry several characteristics, so time_ columns overlap by design and "
      "total about 1.4x real time. Use total_time_observed as the denominator, never the "
      "sum of the parts. Same applies to the pages_ columns.")
    W("3. pages_X counts only confirmed cases",
      "Each pages_X has a matching unknown_X giving the pageviews where that "
      "characteristic could not be determined. A low count can mean 'did not read it' OR "
      "'we could not classify it'. pct_pages_classified gives the overall picture; the "
      "median borrower is 95.8% classified.")
    W("4a. Which language field to use",
      "THREE exist and they are not interchangeable. borrower_language is the lender's "
      "loan-level field and reproduces the paper's Table 2 exactly — use it for "
      "loan-level work. language_preference is per applicant, from the applicant file, "
      "and marks a loan Spanish if ANY applicant does; it agrees 99.72% of the time. "
      "provided_language is the Talkuments account setting and is NOT a covariate at all:")
    W("4b. provided_language is NOT a covariate",
      "It reads 'es' for 280 borrowers in bucket 3 and 0 in bucket 2, because only bucket "
      "3 offered Spanish. It encodes the treatment, not the borrower. Use "
      "language_preference from the applicant file, which is pre-treatment and balanced "
      "across buckets (2.68% / 2.74% / 2.80% Spanish).")
    W("5. Two columns are inferred, not measured",
      "pages_LEDownload, pages_CDDownload and their time_ counterparts. Download URLs "
      "carry no document type, so the type comes from the last Loan Estimate or Closing "
      "Disclosure page viewed in the same session. 82.5% of downloads covered, 87.0% "
      "self-consistent. downloads_type_inferred says how many of a borrower's downloads "
      "this applies to — set it to 0 to exclude them.")

    H("Findings that affect interpretation")
    W("Origination looks high here",
      f"{100*u.loan_originated.mean():.1f}% of borrowers in this file originated, against "
      "about 47% across all pilot loans. That is selection, NOT a treatment effect: "
      "activating Talkuments and progressing through a loan are both downstream of "
      "staying engaged. Bucket 1 borrowers never had access and do not appear here at all.")
    W("The Loan Estimate precedes activation",
      "t_activation_to_le_sent is negative for 99.8% of borrowers — the LE is sent BEFORE "
      "the borrower first opens Talkuments, presumably triggering the invitation. The "
      "specification defines this variable in the opposite order, so its sign reads "
      "backwards. Negative durations throughout this file are real and have not been clipped.")
    W("Fewer than half ever logged in — and blank is not 'no'",
      "48.0% of borrowers given Talkuments logged in, once the 517 whose status is "
      "UNKNOWN are excluded rather than scored as 'no'. Counting blanks as 'no' gives "
      "46.8% and understates by 1.2 points — the same trap that made our first Table 2 "
      "replication miss by 2 points. Our measure agrees with the lender's activation "
      "field on all 16,953 loans where both exist, with zero disagreements.")
    N("Multi-loan borrowers",
      "7.2% hold more than one pilot loan and all loan-level columns describe their "
      "EARLIEST by application date. loans_in_pilot flags them. 1,180 borrowers hold "
      "loans in different buckets and are excluded from bucket comparisons "
      "(pilot_bucket_conflicting).")
    N("Spanish is a small group",
      "Spanish-preference borrowers are 2.58% of the pilot. After excluding those with "
      "loans in several buckets, 312 remain for language comparisons — 142 in bucket 2 "
      "and 170 in bucket 3. Adequate for large effects only.")

    N("Our data can fill a gap in theirs",
      "333 borrowers in this file have no activation status recorded by the lender, yet "
      "they demonstrably used the software — they generated clickstream. Across the "
      "whole pilot the lender has 471 loans with unknown activation. The clickstream "
      "resolves them.")

    H("Still missing")
    N("Document type lookup",
      "A table mapping LoanDocument id to document type would replace the inferred "
      "download columns with measured ones, and fill pages_LEDocument / pages_CDDocument, "
      "the only columns still entirely empty. Note these two would be identical to the "
      "LEDownload / CDDownload pair in this data, since every borrower document path is "
      "already a download.")
    N("Coverage of the loan extract",
      "It covers 25,318 of 27,650 pilot loans (91.6%). The 2,332 missing are spread "
      "evenly across buckets, so no bucket is favoured. 214 borrowers here reach no loan.")

    return pd.DataFrame(R, columns=["Topic", "Detail", "kind"])


# ================================================================ workbook
def write_workbook(u, cb) -> None:
    """One workbook, three sheets: the notes, the data, and the dictionary.

    The dictionary travels with the data deliberately — a codebook in a separate
    file gets separated from the data it describes.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    notes = build_notes(u)
    path = OUT / "user_level_dataset.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        notes.to_excel(xl, sheet_name="Read Me First", index=False)
        u.to_excel(xl, sheet_name="User Data", index=False)
        cb.to_excel(xl, sheet_name="Data Dictionary", index=False)

        head_font = Font(bold=True, color="FFFFFF", size=11)
        head_fill = PatternFill("solid", fgColor="14202A")
        wrap = Alignment(vertical="top", wrap_text=True)
        top = Alignment(vertical="top")

        # --- sheet 0: the notes ---
        ws0 = xl.sheets["Read Me First"]
        ws0.column_dimensions["A"].width = 30
        ws0.column_dimensions["B"].width = 108
        for r in range(1, len(notes) + 2):
            a = ws0.cell(r, 1); b = ws0.cell(r, 2)
            b.alignment = Alignment(vertical="top", wrap_text=True)
            a.alignment = Alignment(vertical="top", wrap_text=True)
            kind = notes.iloc[r - 2]["kind"] if r >= 2 else "head"
            if kind == "H":
                a.font = Font(bold=True, size=13, color="14202A")
                ws0.row_dimensions[r].height = 26
            elif kind == "W":
                a.font = Font(bold=True, color="9B3A2E")
                b.font = Font(color="9B3A2E")
                ws0.row_dimensions[r].height = 46
            else:
                a.font = Font(bold=True)
                ws0.row_dimensions[r].height = 42
        ws0.delete_cols(3)                       # hide the `kind` helper column
        for c in ws0[1]:
            c.font = head_font; c.fill = head_fill
            c.alignment = Alignment(vertical="center")
        ws0.row_dimensions[1].height = 24

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
         f"{dropped:,} non-pageview rows dropped before sessionization "
         f"(browser assets, DEC-P{'' if args.keep_translation_resources else '; /translations/en page resources, DEC-Z'}).", ""]

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

    L += ["## 7. Loan outcomes and milestone timers", "",
          "FAILS IF a milestone timer is non-null for a user with no matched loan, "
          "or if any timer was silently clipped at zero. Negative values are real: "
          "the Loan Estimate is normally sent BEFORE the borrower first opens "
          "Talkuments, which is what triggers the invitation.", ""]
    L += ["| timer | non-null | median days | negative | min days |", "|---|---|---|---|---|"]
    for c in ["t_activation_to_last_access", "t_application_to_activation",
              "t_activation_to_le_sent", "t_le_sent_to_first_le_visit",
              "t_activation_to_lock", "t_last_access_to_current_status"]:
        v = u[c].dropna().astype(float)
        L.append(f"| `{c}` | {len(v):,} ({len(v)/len(u):.1%}) | {v.median()/86400:,.1f} | "
                 f"{int((v<0).sum()):,} ({(v<0).mean():.1%}) | {v.min()/86400:,.1f} |")
    L.append("")
    orphan = int((u[["t_application_to_activation"]].notna().any(axis=1)
                  & u.loan_status.isna()).sum())
    L.append(f"- timers set for a user with no matched loan: **{orphan:,}** "
             f"({'PASS' if orphan == 0 else 'FAIL'})")
    L.append(f"- users with no loan match at all: {int(u.loan_status.isna().sum()):,} "
             f"({u.loan_status.isna().mean():.1%}) — the extract is partial, "
             "covering 25,318 of 27,650 pilot loans")
    L.append("")
    L += ["### Outcome distribution", "", "| status | users | share |", "|---|---|---|"]
    vc = u.loan_status.value_counts()
    for k, v in vc.items():
        L.append(f"| {k} | {v:,} | {v/vc.sum():.1%} |")
    L.append("")
    L.append("Note the selection effect: origination among borrowers who opened "
             "Talkuments is far higher than among all pilot loans, because "
             "activating and progressing through the loan are both downstream of "
             "staying engaged. This is not a treatment effect.")
    L.append("")

    (OUT / "qa_phase2.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
