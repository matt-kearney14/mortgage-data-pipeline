#!/usr/bin/env python3
"""Phase 2 — sessions, event context, aggregation, and the user-level dataset.

Produces one row per borrower with every variable in the specification. The
output is built to be ANALYSED BY SOMEONE ELSE:
  - every assumption that could be argued is a CLI flag, so changing it is a re-run
  - every count that could be misread ships beside its own quality column
  - the Read Me and Data Dictionary sheets are generated here, with every
    figure computed in the run

Event context (DEC-AC) is decided here and only here, because it needs
sessions: the type of each document download, and the Dashboard's CD flags.
Phase 1 leaves exactly those cells NULL (provenance `context`); this script
fills them and nothing else, and checks that it overwrote no measured value.

Run:
    python3 phase2_user_dataset.py
    python3 phase2_user_dataset.py --session-timeout 60
    python3 phase2_user_dataset.py --no-excel
    python3 phase2_user_dataset.py --keep-translation-resources

Outputs (output/):
    user_level_dataset.xlsx / .parquet     one row per borrower  <- the deliverable
    phase2_events.parquet                  pageview grain + session_id, time_on_page, download type
    phase2_sessions.parquet                one row per session
    codebook.csv                           every column, described
    qa_phase2.md                           checks, reported as measured
    session_timeout_sensitivity.csv
"""
from __future__ import annotations
import argparse

import numpy as np
import pandas as pd

import pipeline_common as pc

ROOT, OUT, DATA = pc.ROOT, pc.OUT, pc.DATA

# ---------------------------------------------------------------- parameters
SESSION_TIMEOUT_MIN = pc.SESSION_TIMEOUT_MIN      # DEC-N; our assumption, never the professor's
# DEC-R. A declaration, not a conversion: eventdate is tz-naive and is bucketed
# as-is. Changing this string changes only the labels in the codebook and QA.
TIMEZONE = "UTC"
SENSITIVITY_GRID = [5, 10, 15, 20, 30, 45, 60, 120, 240]
DL_STATS: dict = {}            # filled by type_downloads(), quoted in the codebook
FACTS: dict = {}               # figures computed during the run, quoted in Read Me First

# DEC-AC. Which page a download was clicked from says what it is. Keyed on the
# template table's content_kind, so a renamed or re-numbered page still maps.
DOWNLOAD_SOURCE_KIND = {"le_module": "LE", "cd_module": "CD", "application_docs": "SPL"}
# DEC-AC. A Closing Disclosure cannot exist this soon after the Loan Estimate
# was sent; a number-order "CD" inside this window is left unknown instead.
CD_MIN_DAYS_AFTER_LE = 3

PILOT_BUCKET_LABELS = {          # named in the loan extract itself (DEC-U)
    1: "1 - No Talkument",
    2: "2 - Talkument (English)",
    3: "3 - Talkument multilingual",
}
LOAN_FILE = pc.LOAN_FILE
# Two date formats coexist in that file (DEC-U).
DATE_COLS = {"Application_Date": "%m/%d/%Y",
             "Current_Status_Date": "%d%b%Y %H:%M:%S",
             "LE_TIL_Sent_Date": "%d%b%Y %H:%M:%S",
             "Lock_Date": "%d%b%Y %H:%M:%S"}

# Spec §5 var 32/33: the 18 expanded characteristics are these 17 plus AudioMp3
# (handled separately, DEC-T). Audio is a count and is not expanded.
CHARACTERISTICS = [
    "Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated_provisional",
    "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated", "LoanTermsRelated",
    "LoanEstimateRelated", "CDRelated", "LEDocument", "CDDocument", "Download",
    "LEDownload", "CDDownload", "Video", "Goal_to_inform", "Goal_to_Advise",
]
MILESTONES = [
    "t_application_to_activation", "t_activation_to_le_sent",
    "t_le_sent_to_first_le_visit", "t_activation_to_lock",
    "t_last_access_to_current_status",
]

CODEBOOK: list[dict] = []


def col(flag: str) -> str:
    """Output column for a template-table flag (DEC-E renames ProcessRelated)."""
    return "ProcessRelated_provisional" if flag == "ProcessRelated" else flag


def note(column, level, definition, quality="", decision="", waiting_on=""):
    """Register a column in the codebook. waiting_on names the input a column
    still needs, so the dictionary sheet answers "why is this blank"."""
    CODEBOOK.append(dict(column=column, level=level,
                         status="Not yet available" if waiting_on else "Ready",
                         definition=definition, how_to_read=quality,
                         waiting_on=waiting_on, decision_id=decision))


# ================================================================ sessionize
def sessionize(ev: pd.DataFrame, timeout_s: int) -> pd.DataFrame:
    """Spec §3 vars 22-24 and §4. Assumes the frame is already in sort order."""
    ev["session_start"], ev["session_id"] = pc.session_ids(ev.user_hash, ev.eventdate, timeout_s)
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


# ================================================================ event context (DEC-AC)
def type_downloads(ev: pd.DataFrame, le_sent: pd.Series) -> pd.DataFrame:
    """The type of every borrower-document download: LE, CD, SPL, or unknown.

    /Download/LoanDocument/{id} carries no type, so three kinds of evidence are
    used, strongest first. Each download records which one typed it.
      1. page          the page it was clicked from: the LE page serves the Loan
                       Estimate, the CD page the Closing Disclosure, Application
                       Documents Explained the Service Provider List
      2. doc match     the same document id typed by its page elsewhere (a
                       document has one type; ids typed two ways are excluded)
      3. number order  within a borrower, document ids are issued in order and
                       the LE comes first: an untyped id below the borrower's
                       CD ids is an LE, at or above them a CD. A "CD" downloaded
                       less than CD_MIN_DAYS_AFTER_LE days after the LE was sent
                       is impossible and is left unknown instead.
    Anything left is unknown (NULL), never guessed.
    """
    is_doc = ev.path.str.startswith(pc.DOWNLOAD_PREFIX)
    doc_id = pd.to_numeric(ev.path.str.extract(r"/(\d+)$")[0], errors="coerce")
    kind = ev.template.map(pc.TEMPLATES.set_index("template").content_kind)
    is_page = (ev.AudioMp3 == 0) & ~ev.path.str.startswith((pc.DOWNLOAD_PREFIX, "/download/"))
    src = pc.last_page_index(is_page, [ev.user_hash, ev.session_id])
    from_page = pd.Series(kind.reindex(src.dropna().astype(int)).values,
                          index=src.dropna().index).map(DOWNLOAD_SOURCE_KIND)
    typ = pd.Series(pd.NA, index=ev.index, dtype="object")
    how = pd.Series(pd.NA, index=ev.index, dtype="object")
    by_page = from_page.reindex(ev.index).where(is_doc)
    typ[by_page.notna()] = by_page.dropna(); how[by_page.notna()] = "page"

    # 2. doc match: ids whose page-typed downloads all agree
    seen = pd.DataFrame({"id": doc_id[by_page.notna()], "t": by_page.dropna()})
    nt = seen.groupby("id").t.nunique()
    unanimous = seen.drop_duplicates("id").set_index("id").t[nt == 1]
    m = is_doc & typ.isna() & doc_id.isin(unanimous.index)
    typ[m] = doc_id[m].map(unanimous); how[m] = "doc match"

    # 3. number order, per borrower, over the ids already typed LE or CD
    d = pd.DataFrame({"u": ev.user_hash, "id": doc_id, "ts": ev.eventdate, "t": typ})[is_doc]
    # anchors: ids this borrower has typed one way only (an id typed both ways by
    # its pages is ambiguous and anchors nothing)
    lab = d.dropna(subset=["t"])
    lab = lab[lab.groupby(["u", "id"]).t.transform("nunique") == 1].drop_duplicates(["u", "id"])
    le_max = lab[lab.t == "LE"].groupby("u").id.max()
    cd_min = lab[lab.t == "CD"].groupby("u").id.min()
    open_ = d[d.t.isna()].groupby(["u", "id"]).ts.min().reset_index()
    open_["le_max"] = open_.u.map(le_max); open_["cd_min"] = open_.u.map(cd_min)
    g = np.select([open_.cd_min.notna() & (open_.id >= open_.cd_min),
                   open_.cd_min.notna() & (open_.id < open_.cd_min),
                   open_.le_max.notna() & (open_.id <= open_.le_max),
                   open_.le_max.notna() & (open_.id > open_.le_max)],
                  ["CD", "LE", "LE", "CD"], default=None)
    open_["g"] = g
    # a borrower with no typed id at all but two or more documents: lowest is the LE
    none_typed = open_.le_max.isna() & open_.cd_min.isna()
    n_open = open_[none_typed].groupby("u").id.transform("size")
    lowest = open_[none_typed].groupby("u").id.transform("min")
    nt_idx = open_.index[none_typed]
    open_.loc[nt_idx, "g"] = np.where(n_open < 2, None,
                                      np.where(open_.loc[nt_idx, "id"] == lowest, "LE", "CD"))
    days = (open_.ts - open_.u.map(le_sent)).dt.total_seconds() / 86400
    too_soon = (open_.g == "CD") & (days < CD_MIN_DAYS_AFTER_LE)
    DL_STATS["cd_rejected_timing"] = int(too_soon.sum())
    open_.loc[too_soon, "g"] = None
    guess = open_.dropna(subset=["g"]).set_index(["u", "id"]).g
    key = pd.MultiIndex.from_arrays([ev.user_hash, doc_id])
    gm = pd.Series(guess.reindex(key).values, index=ev.index).where(is_doc & typ.isna())
    typ[gm.notna()] = gm.dropna(); how[gm.notna()] = "number order"

    ev["download_type"] = typ.where(is_doc)
    ev["download_type_source"] = how.where(is_doc)

    # ---- evidence, reported (never used to set a value)
    multi = seen.groupby("id").size() >= 2
    days_all = (ev.eventdate - ev.user_hash.map(le_sent)).dt.total_seconds() / 86400
    tim = (pd.DataFrame({"t": typ, "how": how, "d": days_all})[is_doc].dropna(subset=["t"])
             .groupby(["how", "t"]).d.agg(n="size", median_days="median",
                                          within_3d=lambda s: float((s < 3).mean())))
    DL_STATS.update(
        events=int(is_doc.sum()),
        typed=int(typ[is_doc].notna().sum()),
        by_source={k: int(v) for k, v in how[is_doc].value_counts().items()},
        by_type={k: int(v) for k, v in typ[is_doc].value_counts().items()},
        ids_page_typed_twice=int(multi.sum()),
        ids_conflicting=int((nt[multi.reindex(nt.index, fill_value=False)] > 1).sum()),
        timing=tim.reset_index(),
    )
    return ev


def resolve_context(ev: pd.DataFrame) -> pd.DataFrame:
    """Fill the cells phase 1 left as `context` — and only those.

      document downloads   flags of the template table's download:<type> row; an
                           unknown type keeps only the flags all types share
      Dashboard            CDRelated / CDDocument = 1 once the borrower has CD
                           evidence (a CD page view or a CD download) at or before
                           that view, else 0: no CD exists for them yet
    """
    T = pc.TEMPLATES.set_index("template")
    types = {t: T.loc[f"download:{t}"] for t in ("LE", "CD", "SPL")}
    is_doc = ev.download_type.notna() | ev.path.str.startswith(pc.DOWNLOAD_PREFIX)
    first_cd = ev.loc[(ev.template == "/Module/closing-disclosure-made-clear")
                      | (ev.download_type == "CD")].groupby("user_hash").eventdate.min()
    cd_seen = ev.eventdate >= ev.user_hash.map(first_cd)          # NaT -> False
    is_dash = ev.template == "/Dashboard"
    filled = 0
    for f in pc.FLAGS:
        c, p = col(f), col(f) + "__prov"
        ctx = ev[p] == "context"
        assert ev.loc[ctx, c].isna().all(), f"{c}: a 'context' cell already holds a value"
        # downloads
        d = ctx & is_doc
        val = pd.Series(pd.NA, index=ev.index, dtype="Float64")
        for t, row in types.items():
            val[d & (ev.download_type == t)] = float(row[f])
        shared = {row[f] for row in types.values()}
        unk = d & ev.download_type.isna()
        if len(shared) == 1:
            val[unk] = float(shared.pop())
        ev.loc[d & val.notna(), c] = val[d & val.notna()].astype(int)
        ev.loc[d & val.notna(), p] = np.where(unk[d & val.notna()], "download_any_type",
                                              "download_" + ev.download_type[d & val.notna()].astype(str))
        ev.loc[d & val.isna(), p] = "download_type_unknown"
        # Dashboard CD flags
        dash = ctx & is_dash
        ev.loc[dash, c] = cd_seen[dash].astype(int)
        ev.loc[dash, p] = np.where(cd_seen[dash], "dashboard_cd_seen", "dashboard_no_cd_yet")
        filled += int(ctx.sum())
        left = int((ev[p] == "context").sum())
        assert left == 0, f"{c}: {left} context cells unresolved"
    # spec §3 vars 13-14: a download whose document is an LE / a CD. NULL where
    # the type is unknown; 0 on every row that is not a document download.
    for t, c in (("LE", "LEDownload"), ("CD", "CDDownload")):
        v = pd.array(np.where(is_doc, (ev.download_type == t), False), dtype="boolean")
        v[(is_doc & ev.download_type.isna()).values] = pd.NA
        ev[c] = pd.Series(v, index=ev.index).astype("Int8")
    FACTS["context_cells"] = filled
    FACTS["dash_views"] = int(is_dash.sum())
    FACTS["dash_cd"] = int((is_dash & cd_seen).sum())
    return ev


# ================================================================ build
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-timeout", type=int, default=SESSION_TIMEOUT_MIN)
    ap.add_argument("--no-excel", action="store_true")
    ap.add_argument("--keep-translation-resources", action="store_true",
                    help="count /translations/* rows as pageviews and credit them dwell "
                         "(pre-audit behaviour; see DEC-Z)")
    args = ap.parse_args()
    timeout_s = args.session_timeout * 60
    pc.check_inputs([pc.ACCOUNT_FILE, pc.APPLICANT_FILE, pc.BUCKET_FILE, pc.LOAN_FILE,
                     pc.TEMPLATE_FILE])
    if not (OUT / "phase1_url_features.parquet").exists():
        raise SystemExit("Missing output/phase1_url_features.parquet. Run clickstream_processor.py first.")

    ev = pd.read_parquet(OUT / "phase1_url_features.parquet")
    FACTS["n_events"] = len(ev)

    # Language switches are read on the full row sequence, before the language
    # files themselves are dropped below (DEC-AB).
    st = ev.language_state
    prev = st.groupby(ev.user_hash).shift(1)
    sw = pd.DataFrame({"u": ev.user_hash, "es": (prev == "en") & (st == "es"),
                       "en": (prev == "es") & (st == "en")}).groupby("u")[["es", "en"]].sum()

    # DEC-P / DEC-Z / DEC-AB: browser assets and the app's language files are not
    # pageviews. Left in, they cut a page's dwell short and take its reading time.
    resource = ev.row_class == "page_resource"
    if args.keep_translation_resources:
        resource &= ~ev.path.str.startswith(pc.TRANSLATION_PREFIX)
    dropped = int(resource.sum())
    ev = ev[~resource]
    # Spec §1 sort key, enforced rather than trusted: every order-dependent step
    # below depends on it. _source_row is unique, so the order is total.
    ev = (ev.sort_values(["user_hash", "eventdate", "_source_row"], kind="mergesort")
            .reset_index(drop=True))
    ev = sessionize(ev, timeout_s)

    # timeout sensitivity, regenerated every run so the figure is never quoted alone
    rows = []
    for t in SENSITIVITY_GRID:
        e2 = sessionize(ev[["user_hash", "eventdate"]].copy(), t * 60)
        d = e2.groupby(["user_hash", "session_id"]).eventdate.agg(
            lambda s: (s.max() - s.min()).total_seconds())
        p = e2.groupby(["user_hash", "session_id"]).size()
        rows.append(dict(timeout_min=t, sessions=int(e2.session_start.sum()),
                         median_duration_s=float(d.median()), mean_duration_s=round(float(d.mean()), 1),
                         median_pages=float(p.median()), mean_pages=round(float(p.mean()), 2),
                         pct_single_page=round(100 * float((p == 1).mean()), 1)))
    sens = pd.DataFrame(rows).set_index("timeout_min", drop=False)
    t2 = min(SENSITIVITY_GRID, key=lambda t: abs(t - 2 * args.session_timeout))
    base = sens.loc[args.session_timeout] if args.session_timeout in sens.index else None
    dbl = (None if base is None else
           (100 * (sens.loc[t2, "sessions"] / base.sessions - 1),
            100 * (sens.loc[t2, "mean_duration_s"] / base.mean_duration_s - 1)))

    loans = load_loans()
    appl = pd.read_excel(DATA / "talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
    primary, counts = primary_loans(loans, appl)
    ev = type_downloads(ev, primary.LE_TIL_Sent_Date)          # DEC-AC
    ev = resolve_context(ev)                                    # DEC-AC
    parent = pc.last_page_index(ev.AudioMp3 == 0, [ev.user_hash, ev.session_id])
    share = pd.concat([ev[col(f) + "__prov"] for f in pc.FLAGS]).value_counts(normalize=True)
    grp = lambda ks: float(sum(share.get(k, 0) for k in ks))
    FACTS["prov_share"] = dict(
        professor=grp(["coded", "coded_same_page", "coded_same_clip"]),
        ours=grp(["coded_by_us"]), navigation=grp(["navigation"]),
        event=float(share[share.index.str.startswith(("download_", "dashboard_", "clip_", "rule"))
                          & (share.index != "download_type_unknown")].sum()),
        unknown=grp(["download_type_unknown", "unresolved"]))
    import json
    FACTS["run_info"] = json.loads((OUT / "run_info.json").read_text())

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

    note("user_hash", "id", "Pseudonymous borrower identifier. The join key to "
         "talkument_useraccount.xlsx and, via loan number, to the pilot arms.",
         "One row per borrower; unique. Pre-pilot test accounts are removed (DEC-AA).", "DEC-AA")
    u["webpages_visited"] = g.size()
    note("webpages_visited", "user", "Total pageviews. Excludes browser asset requests "
         "(/favicon.ico, /cart.json) and the language files the app loads with a page "
         "(/translations/en, /translations/es).",
         "Exact. Not the raw row count of the log (spec var 28): those rows are not "
         "pages the borrower viewed.", "DEC-P/DEC-Z/DEC-AB")
    u["unique_webpages_visited"] = g.path.nunique()
    note("unique_webpages_visited", "user", "Distinct URL paths visited.", "Exact.")
    u["spanish_webpages_visited"] = g.Spanish_YN.sum()
    u["english_webpages_visited"] = g.English_YN.sum()
    for c in ("spanish_webpages_visited", "english_webpages_visited"):
        note(c, "user", "Pageviews shown in that language. Each borrower starts in their "
             "account language; the language then follows the language files the app loads "
             "with a page (Spanish if the load includes /translations/es, English if it is "
             "/translations/en alone) and carries forward until the next load says otherwise.",
             "Exhaustive: the two always sum to webpages_visited.", "DEC-AB/DEC-K")
    u["audio_clips_clicked"] = g.AudioMp3.sum()
    note("audio_clips_clicked", "user", "Count of .mp3 requests. Spec defines this as "
         "sum(AudioMp3), explicitly not sum(Audio).", "Exact; row-level test on the path.")
    u["days_accessed"] = g.eventdate.apply(lambda s: s.dt.date.nunique())
    note("days_accessed", "user", f"Distinct calendar dates with at least one event, "
         f"bucketed in {TIMEZONE}.", "eventdate is timezone-naive at second resolution.", "DEC-R")

    u["language_switches_to_spanish"] = sw.es.reindex(u.index).fillna(0).astype(int)
    u["language_switches_to_english"] = sw.en.reindex(u.index).fillna(0).astype(int)
    u["used_language_toggle"] = (u.language_switches_to_spanish + u.language_switches_to_english).gt(0)
    for c, lang in (("language_switches_to_spanish", "Spanish"), ("language_switches_to_english", "English")):
        note(c, "user", f"Times the borrower's interface changed to {lang}.",
             "A change is a page load in the other language. Starting in the account "
             "language is not a change.", "DEC-AB")
    note("used_language_toggle", "user", "True if the borrower's interface language ever changed.",
         "Only possible in pilot bucket 3, the only bucket with the toggle.", "DEC-AB")

    u["num_sessions"] = g.session_id.nunique()
    note("num_sessions", "user", f"Distinct sessions at a {args.session_timeout}-minute "
         "inactivity timeout.",
         ("" if dbl is None else f"Robust to the timeout: {args.session_timeout}->{t2} min "
          f"changes the session count by {dbl[0]:+.1f}%. ") +
         "See session_timeout_sensitivity.csv.", "DEC-N")

    su = sess.groupby("user_hash")
    u["total_session_time"] = su.session_duration.sum()
    u["mean_session_duration"] = su.session_duration.mean().round(1)
    u["median_session_duration"] = su.session_duration.median()
    note("total_session_time", "user", "Sum of session durations, seconds.",
         "Sensitive to the timeout. Like every duration here it ends at the last page's "
         "arrival: time on a session's last page cannot be observed.", "DEC-N")
    note("mean_session_duration", "user", "Mean session duration, seconds.",
         ("HIGHLY timeout-sensitive" + ("" if dbl is None else
          f": {args.session_timeout}->{t2} min moves the population mean by {dbl[1]:+.0f}%")) +
         ". Report the median alongside it.", "DEC-N")
    note("median_session_duration", "user", "Median session duration, seconds.",
         "Preferred over the mean; session durations are heavily right-skewed.", "DEC-N")
    u["mean_pages_in_session"] = su.pages_in_session.mean().round(2)
    note("mean_pages_in_session", "user", "Mean pageviews per session.", "")
    u["mean_inter_session_elapsed"] = su.inter_session_elapsed.mean().round(1)
    note("mean_inter_session_elapsed", "user", "Mean seconds between the end of one "
         "session and the start of the next.", "NULL for single-session borrowers.")
    u["single_page_sessions"] = su.pages_in_session.apply(lambda s: int((s == 1).sum()))
    note("single_page_sessions", "user", "Sessions consisting of one pageview "
         "(duration 0 by definition, not NULL).",
         "A high share across the population suggests revisiting the timeout.", "DEC-N")

    u["total_time_observed"] = g.time_on_page.sum()
    u["zero_dwell_pages"] = g.zero_dwell.sum()
    u["pages_time_not_observable"] = g.session_end.sum()
    FACTS["last_pages"] = int(ev.session_end.sum())
    note("total_time_observed", "user", "Sum of observed time_on_page, seconds.",
         "Equals total_session_time by construction; QA check 1 verifies this. Excludes "
         "each session's last page (see pages_time_not_observable).")
    note("pages_time_not_observable", "user", "Pageviews whose time on page cannot be "
         "observed: the last page of each session, which has no next click to end it.",
         "Equals num_sessions. Their time is left blank, never filled or estimated, so "
         "every time_ column is a lower bound by that page's reading time.", "DEC-AF")
    note("zero_dwell_pages", "user", "Pageviews with a measured dwell of exactly 0 "
         "seconds, mostly redirects and same-second requests.",
         f"{ev.zero_dwell.mean():.1%} of all pageviews population-wide. Exclude these "
         "before any dwell analysis.", "DEC-O")

    # ----------------------------------------- per-characteristic expansion
    # pages_C (spec §5 var 32) is sum(flag) over the user's OWN rows: an mp3 row
    # counts under the clip's own flags. time_C (var 33) credits an mp3 row's dwell
    # to its PARENT page's characteristics instead, never additionally to the
    # clip's own. An mp3 with no parent in its session is credited its own flags.
    attr = ev[["user_hash", "time_on_page"]].copy()
    is_mp3 = ev.AudioMp3 == 1
    orphan_mp3 = is_mp3 & parent.isna()
    for c in CHARACTERISTICS:
        par = ev[c].reindex(parent).reset_index(drop=True)
        par.index = ev.index
        attr[c] = ev[c].where(~is_mp3 | orphan_mp3, par)
    FACTS["orphan_mp3"] = int(orphan_mp3.sum())
    FACTS["proc_not_superset"] = int((((ev.BorrowerMortgageProcessRelated == 1)
                                       | (ev.LenderMortgageProcessRelated == 1))
                                      & (ev.ProcessRelated_provisional != 1)).sum())
    D = DL_STATS
    srcs = D["by_source"]
    DL_NOTE = (
        f"Download type is decided per download (DEC-AC): {srcs.get('page', 0):,} by the page "
        f"it was clicked from, {srcs.get('doc match', 0):,} by the same document id typed "
        f"elsewhere, {srcs.get('number order', 0):,} by document number order within the "
        f"borrower; {D['events'] - D['typed']:,} of {D['events']:,} stay unknown (NULL, "
        "counted in downloads_type_unknown). Filter on downloads_typed_by_number_order to "
        "drop the weakest evidence.")
    DEFS = {
        "LEDocument": "the page shows the borrower's Loan Estimate (the LE page, the "
                      "Dashboard, or a downloaded LE)",
        "CDDocument": "the page shows the borrower's Closing Disclosure (the CD page, a "
                      "downloaded CD, or the Dashboard once the borrower has a CD)",
        "LEDownload": "the event is a download of the borrower's Loan Estimate",
        "CDDownload": "the event is a download of the borrower's Closing Disclosure",
    }
    for c in CHARACTERISTICS:
        short = c.replace("_provisional", "")
        own, flag = ev[c], attr[c]
        u[f"pages_{short}"] = ev.assign(_f=(own == 1)).groupby("user_hash")._f.sum()
        u[f"time_{short}"] = (attr.assign(_t=attr.time_on_page.where(flag == 1))
                                  .groupby("user_hash")._t.sum())
        what = DEFS.get(short, f"{short} == 1")
        extra = ("  Flagged '????' in the professor's own sheet; his values are kept and the "
                 "column is output under a _provisional name." if short == "ProcessRelated" else "")
        dec = ("DEC-AC" if short in DEFS else "DEC-E/DEC-AD" if short == "ProcessRelated"
               else "DEC-AD")
        dq = (" " + DL_NOTE) if short in DEFS or short in ("LoanEstimateRelated", "CDRelated") else ""
        note(f"pages_{short}", "user", f"Pageviews where {what}.{extra}",
             "Counts confirmed 1s only." + ("" if short in ("LEDownload", "CDDownload")
                                            else f" Read together with unknown_{short}.") + dq, dec)
        note(f"time_{short}", "user", f"Seconds on pageviews where {what}. Audio-clip time is "
             "credited to the page that played the clip.",
             "Observed dwell only: a page that ends its session adds nothing (its time "
             "cannot be observed). The time_* columns OVERLAP — a page can carry several "
             "characteristics, so they do not sum to total time." + dq, dec)
        if short in ("LEDownload", "CDDownload"):
            continue                       # their unknown is downloads_type_unknown
        u[f"unknown_{short}"] = ev.assign(_u=own.isna()).groupby("user_hash")._u.sum()
        note(f"unknown_{short}", "user", f"Pageviews where {short} could not be determined.",
             "The denominator caveat for pages_/time_" + short + ". After DEC-AD the only "
             "source of unknowns is a document download of unknown type.", "DEC-L")

    # ---- AudioMp3 as a characteristic (DEC-T): raw dwell on mp3 rows, i.e. actual
    # listening time; it ALSO counts toward the parent page's characteristics.
    u["pages_AudioMp3"] = ev.assign(_f=ev.AudioMp3 == 1).groupby("user_hash")._f.sum()
    u["time_AudioMp3"] = (ev.assign(_t=ev.time_on_page.where(ev.AudioMp3 == 1))
                            .groupby("user_hash")._t.sum())
    note("pages_AudioMp3", "user", "Pageviews that are an .mp3 request.",
         "Identical to audio_clips_clicked by construction — the specification names "
         "this same quantity twice. Kept so all 18 expanded characteristics are present.", "DEC-T")
    note("time_AudioMp3", "user", "Seconds spent on .mp3 rows — actual listening time.",
         "Overlaps the other time_* columns by design: this same dwell is also credited "
         "to the characteristics of the page that played the clip, per spec §5 var 33.", "DEC-T")

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
    note("pct_pages_classified", "user", "Share of the borrower's pageviews carrying at least "
         "one determined characteristic.",
         "A low value means this borrower's totals rest on little classified data.", "DEC-AD")

    # ---- download evidence per borrower (DEC-AC)
    is_doc = ev.path.str.startswith(pc.DOWNLOAD_PREFIX)
    for src_name, colname in (("page", "downloads_typed_by_page"),
                              ("doc match", "downloads_typed_by_doc_match"),
                              ("number order", "downloads_typed_by_number_order")):
        u[colname] = (ev.assign(_x=ev.download_type_source == src_name)
                        .groupby("user_hash")._x.sum())
    u["downloads_type_unknown"] = (ev.assign(_x=is_doc & ev.download_type.isna())
                                     .groupby("user_hash")._x.sum())
    note("downloads_typed_by_page", "user", "Document downloads typed by the page they were "
         "clicked from (LE page, CD page, Application Documents Explained).",
         "The strongest evidence: the page serves that document.", "DEC-AC")
    note("downloads_typed_by_doc_match", "user", "Document downloads typed because the same "
         "document id was typed by its page in another download.",
         f"A document has one type: of {D['ids_page_typed_twice']:,} ids typed by page more "
         f"than once, {D['ids_conflicting']:,} disagreed and are not used.", "DEC-AC")
    note("downloads_typed_by_number_order", "user", "Document downloads typed by document "
         "number order within the borrower (the LE's number is lower than the CD's).",
         f"The weakest evidence. A 'CD' guessed less than {CD_MIN_DAYS_AFTER_LE} days after "
         f"the LE was sent is left unknown ({D['cd_rejected_timing']:,} such). Subtract "
         "this from pages_LEDownload / pages_CDDownload for a stricter count.", "DEC-AC")
    note("downloads_type_unknown", "user",
         "Document downloads whose type could not be determined.",
         "The unknown_ companion of pages_LEDownload / pages_CDDownload: those count typed "
         "downloads only, so 0 there with a non-zero value here means 'type unknown', not "
         "'did not download'.", "DEC-AC")

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
         "Computable for every borrower; needs no external file.")

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
    for c in MILESTONES:
        note(c, "user", MILESTONE_DEFS[c],
             "Signed seconds; negative values are real and must not be clipped. "
             "Computed from the borrower's EARLIEST pilot loan by application date "
             "(DEC-V). The loan dates are calendar dates (midnight) while Talkuments times "
             "have seconds, so a same-day pair reads as up to a day apart and its sign can be "
             "wrong. Blank where the date does not exist; milestone_blank_reason says why.",
             "DEC-V/DEC-AF")

    # -------------------------------------------------- joinable attributes
    acct = (pd.read_excel(DATA / "talkument_useraccount.xlsx", sheet_name="users")
              .drop_duplicates("user_hash").set_index("user_hash"))
    u["provided_language"] = acct.provided_language.reindex(u.index)
    u["expertise_level"] = acct.expertise_level.reindex(u.index)
    u["account_enabled"] = acct.account_enabled.reindex(u.index)
    note("expertise_level", "account", "Account expertise level.",
         "Use with caution. There is no level 3, and levels 2 and 4 appear only among "
         "users with events, so this may be assigned on engagement rather than at signup.")
    note("account_enabled", "account", "Account enabled flag.", "")

    buck = pd.read_excel(DATA / "talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
    br = (appl.dropna(subset=["user_hash"])
              .merge(buck, left_on="loannumber", right_on="loan_number", how="inner"))
    gb = br.groupby("user_hash")
    u["pilot_bucket"] = gb.bucket.agg(single_or_null).reindex(u.index).astype("Int64")
    u["pilot_bucket_label"] = u.pilot_bucket.map(PILOT_BUCKET_LABELS)
    # NULL (not False) for users with no bucket at all: "not conflicting" would be
    # a claim about loans we cannot see.
    u["pilot_bucket_conflicting"] = gb.bucket.nunique().gt(1).reindex(u.index).astype("boolean")
    u["language_preference"] = gb.language_preference.agg(single_or_null).reindex(u.index)
    u["state"] = gb.state.agg(single_or_null).reindex(u.index)
    FACTS["b1_users"] = int(gb.bucket.agg(lambda b: bool((b == 1).any())).reindex(u.index)
                            .eq(True).sum())
    b1 = (f"Bucket 1 (no Talkument) is never a resolved value here: {FACTS['b1_users']:,} "
          "borrowers in this file do hold a bucket-1 loan, but every one of them also holds a "
          "bucket 2 or 3 loan, so they are pilot_bucket_conflicting. Compare bucket 1 on "
          "loan outcomes, not on this dataset.")
    note("pilot_bucket_label", "loan", "Plain-language name of the pilot bucket.",
         "Sort or filter on this, or on pilot_bucket. " + b1)
    note("pilot_bucket", "loan", "Pilot bucket number, bridged loan_number to loannumber.",
         "NULL where a borrower holds loans in different buckets or none. " + b1)
    note("pilot_bucket_conflicting", "loan", "True if the borrower's loans span more than one bucket.",
         f"{int(u.pilot_bucket_conflicting.sum()):,} borrowers; excluded from pilot_bucket rather "
         "than assigned a guess. NULL for borrowers linked to no pilot loan at all.")
    note("language_preference", "loan", "Applicant's stated language preference.",
         "Pre-treatment and balanced across buckets (see Read Me First); the appropriate "
         "per-person language covariate. NULL where a borrower's applicant rows disagree.")
    note("state", "loan", "Applicant state.", "NULL where a borrower's loans disagree.")
    pl_es = u.provided_language.eq("es")
    FACTS["es_b3"] = int((pl_es & u.pilot_bucket.eq(3)).sum())
    FACTS["es_b2"] = int((pl_es & u.pilot_bucket.eq(2)).sum())
    note("provided_language", "account", "Account language setting.",
         f"NOT a pre-treatment covariate: in this file it reads 'es' for {FACTS['es_b3']:,} "
         f"borrowers in pilot bucket 3 and {FACTS['es_b2']:,} in bucket 2, so it reflects the "
         "treatment, not the borrower.")

    # ------------------------------------------- loan outcomes & milestones
    u = attach_loan_data(u, ev, loans, appl, primary, counts)
    pilot_facts(appl, buck, loans)
    u = u.reset_index()

    # Grouping columns sit immediately after the id so the sheet can be sorted or
    # filtered by bucket without scrolling past the measure columns first.
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
    missing = sorted(set(u.columns) - set(cb.column))
    assert not missing, f"columns with no Data Dictionary entry: {missing}"
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
    sens.to_csv(OUT / "session_timeout_sensitivity.csv", index=False)

    if not args.no_excel:
        write_workbook(u, cb)
    qa(ev, sess, u, dropped, args, attr)
    print(f"users {len(u):,}  columns {u.shape[1]}  sessions {len(sess):,}")
    print(f"dropped non-pageview rows: {dropped:,}")
    print(f"downloads typed {DL_STATS['typed']:,} of {DL_STATS['events']:,} "
          f"{DL_STATS['by_source']}")
    print(f"codebook entries: {len(cb)}")


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
    d = d[d.Loan_Number.notna()].copy()        # trailing blank rows
    d["loannumber"] = d.Loan_Number.astype("int64").astype(str)
    for c, fmt in DATE_COLS.items():
        parsed = pd.to_datetime(d[c], format=fmt, errors="coerce")
        bad = d[c].notna() & parsed.isna()
        if bad.any():
            raise SystemExit(f"{LOAN_FILE.name}: {int(bad.sum()):,} {c} value(s) do not match the "
                             f"expected format {fmt!r} (e.g. {d.loc[bad, c].iloc[0]!r}).")
        d[c] = parsed
    # DEC-U: implausible values are nulled, not clipped, and counted in QA.
    FACTS["apr_nulled"] = int((d.APR > 30).sum())
    FACTS["score_nulled"] = int((d.Credit_Score_Decision < 300).sum())
    d.loc[d.APR > 30, "APR"] = np.nan
    d.loc[d.Credit_Score_Decision < 300, "Credit_Score_Decision"] = np.nan
    return d


def primary_loans(loans, appl):
    """DEC-V: each borrower's EARLIEST loan by Application_Date (ties keep
    applicant-file order), and how many pilot loans they hold."""
    link = appl.dropna(subset=["user_hash"])[["user_hash", "loannumber"]].copy()
    link["loannumber"] = link.loannumber.astype(str)
    m = link.drop_duplicates().merge(loans, on="loannumber", how="inner")
    m = m.sort_values(["user_hash", "Application_Date"], kind="mergesort")
    FACTS["tied_earliest_by_user"] = m.groupby("user_hash").Application_Date.apply(
        lambda d: int((d == d.min()).sum()))
    return (m.drop_duplicates("user_hash", keep="first").set_index("user_hash"),
            m.groupby("user_hash").loannumber.nunique())


def attach_loan_data(u, ev, loans, appl, primary, counts):
    """Loan outcomes and spec vars 38-42 from each borrower's primary loan (DEC-V)."""
    u["loans_in_pilot"] = counts.reindex(u.index).astype("Int64")
    FACTS["multi_loan"] = float((u.loans_in_pilot > 1).sum() / u.loans_in_pilot.notna().sum())
    FACTS["no_loan"] = int(u.loans_in_pilot.isna().sum())
    FACTS["tied_earliest"] = int((FACTS.pop("tied_earliest_by_user").reindex(u.index) > 1).sum())
    note("loans_in_pilot", "user", "Number of the borrower's loans present in the "
         "loan-application extract.",
         f"Greater than 1 for {FACTS['multi_loan']:.1%} of borrowers with a loan; NULL for "
         f"the {FACTS['no_loan']:,} who reach no loan in the extract. All loan-level columns "
         "below describe only the EARLIEST loan by application date "
         f"({FACTS['tied_earliest']:,} borrowers have two loans on that earliest date; "
         "applicant-file order breaks the tie).", "DEC-V")

    # DEC-Y: the lender's own loan-level language field. It reproduces the paper's
    # Table 2 exactly, so it is the field to use for loan-level work; the applicant
    # file's language_preference stays as the per-person alternative.
    LANG = {"SpanishIndicator": "Spanish", "EnglishIndicator": "English",
            "LanguageRefusalIndicator": "Refusal"}
    blp = primary.Borrower_Language_Preference.reindex(u.index)
    u["borrower_language"] = blp.map(lambda v: LANG.get(v, None if pd.isna(v) else "Other"))
    note("borrower_language", "loan",
         "The lender's language preference for the loan's borrower.",
         "PREFER THIS for loan-level work: tabulated with the lender's activation field "
         "it reproduces the paper's Table 2 to two decimals in all 12 cells "
         "(diagnostics/replicate_activation_table.py, which also measures its agreement "
         "with the applicant file's language_preference). Blank where no language was "
         "recorded — the paper counts those under 'Other'.",
         "DEC-Y")

    # DEC-Y: activation status, with UNKNOWN kept distinct from NO.
    at = primary.Activated_Talkument.reindex(u.index)
    u["activated_talkument"] = pd.array(
        np.where(at.isna(), pd.NA, at.eq("Yes")), dtype="boolean")
    has_loan = u.loans_in_pilot.notna()
    FACTS["act_blank_with_loan"] = int((u.activated_talkument.isna() & has_loan).sum())
    FACTS["act_no"] = int(u.activated_talkument.eq(False).sum())
    note("activated_talkument", "loan",
         "Whether anyone on the loan activated Talkuments, per the lender.",
         "BLANK means unknown, NOT 'no'. Every borrower in this file used the software, "
         f"so the column holds no 'no' ({FACTS['act_no']:,}) and its blanks are of two "
         f"kinds: {FACTS['act_blank_with_loan']:,} borrowers whose loan has no lender "
         f"value, and {FACTS['no_loan']:,} who reach no loan at all. The lender field "
         "agrees with talkument_useraccount first_login on every loan where both exist "
         "(diagnostics/crossvalidate.py).", "DEC-Y")

    nh = (appl.assign(loannumber=appl.loannumber.astype(str))
              .groupby("loannumber").applicant_email_hash.nunique())
    lh = loans.set_index("loannumber").Coapplicant
    nh = nh.reindex(lh.index)
    FACTS["co1_single"] = int(((lh == 1) & (nh == 1)).sum())
    FACTS["co0_multi"] = int(((lh == 0) & (nh >= 2)).sum())

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
         "Does NOT agree with counting distinct applicant_email_hash values in "
         "talkument_loan_applicants.xlsx — "
         f"{FACTS['co1_single']:,} loans flagged 1 show a single hash and "
         f"{FACTS['co0_multi']:,} flagged 0 show two or more. Prefer this field; see "
         "DEC-W.", "DEC-W")
    note("credit_score", "loan", "Credit score used for the decision.",
         f"{FACTS['score_nulled']:,} loans carried a score below 300 and were nulled rather "
         "than clipped (DEC-U).", "DEC-U")
    note("interest_rate", "loan", "Note rate.",
         f"Null for {loans.Interest_Rate.isna().mean():.0%} of loans in the extract — a "
         "rate exists only once the loan is locked.", "DEC-U")
    note("apr", "loan", "Annual percentage rate.",
         f"{FACTS['apr_nulled']:,} loan(s) carried an APR above 30 and were nulled rather "
         "than clipped (DEC-U).", "DEC-U")

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
    FACTS["le_only_before"] = int(len(set(le_map.dropna(subset=["le_sent"]).user_hash)
                                      - set(first_after.index)))
    # Milestone dates are DATES (midnight); first_access has seconds. A borrower who
    # first opens Talkuments on the day the LE was sent reads negative regardless
    # of the true order, so that sign is undetermined for them.
    FACTS["le_same_day"] = int((le.notna() & (first.dt.normalize() == le)).sum())
    FACTS["le_before_day"] = int((first.dt.normalize() > le).sum())
    FACTS["le_after_day"] = int((first.dt.normalize() < le).sum())
    u["t_le_sent_to_first_le_visit"] = secs(first_after.reindex(u.index), le)

    # DEC-AF: a blank timer is left blank — the date does not exist — and says why.
    has_loan = u.loans_in_pilot.notna()
    why = {
        "t_application_to_activation": [(app.isna(), "no application date")],
        "t_activation_to_le_sent": [(le.isna(), "LE not sent")],
        "t_le_sent_to_first_le_visit": [(le.isna(), "LE not sent"),
                                        (le.notna(), "no LE page visit on or after the LE was sent")],
        "t_activation_to_lock": [(lock.isna(), "loan never locked")],
        "t_last_access_to_current_status": [(status.isna(), "no current status date")],
    }
    parts = pd.Series("", index=u.index)
    for c, rules in why.items():
        blank = u[c].isna() & has_loan
        for cond, text in rules:
            hit = blank & cond
            parts[hit] += f"{c}: {text}; "
            blank &= ~hit
    u["milestone_blank_reason"] = parts.str.rstrip("; ").where(has_loan, "not in loan extract")
    FACTS["reason_counts"] = {k: int(v) for k, v in u.milestone_blank_reason.str.split("; ")
                              .explode().replace("", np.nan).dropna()
                              .str.replace(r"^t_\w+: ", "", regex=True).value_counts().items()}
    note("milestone_blank_reason", "user",
         "Why any of the five loan milestone timers is blank for this borrower.",
         "'not in loan extract' means the borrower reaches no loan in "
         "loan_application_data_partial.csv, so all five are blank. Otherwise each blank timer "
         "is listed with its reason (e.g. 'loan never locked'). Empty when no timer is blank. "
         "Blank timers are never filled: the date does not exist.", "DEC-AF")
    return u


def pilot_facts(appl, buck, loans) -> None:
    """Pilot-wide figures the Read Me quotes. Loan grain; computed every run."""
    b = buck.assign(loan_number=buck.loan_number.astype(str))
    in_ext = b.loan_number.isin(set(loans.loannumber))
    FACTS["pilot_loans"] = len(b)
    FACTS["extract_loans"] = int(in_ext.sum())
    FACTS["missing_by_bucket"] = {int(k): (int((~in_ext & (b.bucket == k)).sum()),
                                           float((~in_ext[b.bucket == k]).mean()))
                                  for k in sorted(b.bucket.unique())}
    FACTS["orig_all"] = float(loans.Loan_Status.eq("Loan Originated").mean())
    tk = loans.bucket.isin(["talkument", "talkument_multi"])
    FACTS["act_blank_loans"] = int((tk & loans.Activated_Talkument.isna()).sum())
    a = appl.assign(loannumber=appl.loannumber.astype(str))
    es = (a.groupby("loannumber").language_preference
            .apply(lambda v: v.isin(["Spanish", "Espa?ol"]).any()))
    eb = b.set_index("loan_number").bucket
    FACTS["es_share_by_bucket"] = {int(k): float(es.reindex(eb.index[eb == k]).dropna().mean())
                                   for k in sorted(eb.unique())}


def build_notes(u) -> pd.DataFrame:
    """The 'Read Me First' sheet. The professor reads the workbook, not the repo,
    so every caveat that could change a conclusion has to live here. Every figure
    is computed in this run (FACTS); none is typed in by hand."""
    F = FACTS
    R = []
    def H(t): R.append(("", t, "H"))
    def N(k, v): R.append((k, v, "N"))
    def W(k, v): R.append((k, v, "W"))

    tot = float(u.total_time_observed.sum())
    overlap = sum(float(u[f"time_{c.replace('_provisional', '')}"].sum())
                  for c in CHARACTERISTICS) / tot
    d = DL_STATS
    sp = u[u.pilot_bucket.isin([2, 3]) & u.pilot_bucket_conflicting.eq(False)
           & u.language_preference.isin(["Spanish", "Espa?ol"])]
    spb = sp.pilot_bucket.value_counts()
    mb = F["missing_by_bucket"]
    neg = u.t_activation_to_le_sent.dropna()
    nle = F["le_same_day"] + F["le_before_day"] + F["le_after_day"]

    H("What this file is")
    N("Grain", f"ONE ROW PER BORROWER who opened Talkuments — {len(u):,} people, "
               f"{u.shape[1]} columns. It is NOT one row per loan.")
    N("Sheets", "'User Data' is the data. 'Data Dictionary' defines every column, "
                "says how to read it, and names what any empty column is waiting on.")
    N("Source", f"Talkument clickstream ({F['n_events']:,} logged events, of which "
                f"{int(u.webpages_visited.sum()):,} are pageviews — browser asset requests "
                "and the language files the app loads with a page are not), the borrower "
                "account file, the pilot bucket assignment, and the loan application extract.")
    ri = F["run_info"]
    N("Test accounts removed",
      f"{ri['test_users']:,} account(s) whose activity began before the pilot opened on "
      f"{ri['pilot_start']} (the earliest loan application in the extract) are testers, "
      f"not borrowers, and are removed with all {ri['test_events']:,} of their events. "
      "The date comes from the data each run; --pilot-start overrides it (DEC-AA).")
    pv = F["prov_share"]
    N("How pages were classified",
      f"Of all page-characteristic cells, "
      f"{pv['professor']:.0%} are the professor's own coding, {pv['ours']:.0%} were coded by "
      f"us from the page's content where he left them blank (labelled; listed page by page "
      f"in output/dictionary_review_for_professor.xlsx for him to confirm), "
      f"{pv['navigation']:.0%} are navigation pages set to 0, and {pv['event']:.0%} were "
      f"set from the event itself (download type, the Dashboard's CD flags, audio clips "
      f"taking their page's values). {pv['unknown']:.2%} remain unknown: downloads whose "
      "document type cannot be determined.")

    H("Five things to check before you analyse")
    W("1. Person vs loan",
      "This file is per person; activation tables in the paper are per loan. A loan can "
      "have several applicants and a person can hold several loans, so the two give "
      "different rates. Aggregate to the loan before comparing against loan-level tables "
      "(diagnostics/replicate_activation_table.py does both).")
    W("2. Do not sum the time_ columns",
      "A page can carry several characteristics, so time_ columns overlap by design and "
      f"total {overlap:.2f}x real time. Use total_time_observed as the denominator, never "
      "the sum of the parts. Same applies to the pages_ columns.")
    W("3. Blank means 'cannot be known', never 'no'",
      "Each pages_X has a matching unknown_X (for the download types, "
      "downloads_type_unknown); after this run the only unknowns are downloads of "
      f"undeterminable type. Time on the LAST page of each session cannot be observed — "
      f"there is no next click — so it is blank, not estimated ({F['last_pages']:,} "
      "pageviews; pages_time_not_observable per borrower); every time_X is observed time "
      "only. Milestone timers are blank where the date does not exist, and "
      "milestone_blank_reason says why for each borrower.")
    W("4a. Which language field to use",
      "THREE exist and they are not interchangeable. borrower_language is the lender's "
      "loan-level field — use it for loan-level work. language_preference is per "
      "applicant, from the applicant file. provided_language is the Talkuments account "
      "setting and is NOT a covariate at all:")
    W("4b. provided_language is NOT a covariate",
      f"In this file it reads 'es' for {F['es_b3']:,} borrowers in bucket 3 and "
      f"{F['es_b2']:,} in bucket 2, because only bucket 3 offered Spanish. It encodes the "
      "treatment, not the borrower. Use language_preference, which is pre-treatment: "
      "loans with a Spanish-preference applicant are " +
      " / ".join(f"{v:.2%}" for v in F["es_share_by_bucket"].values()) +
      " of buckets " + " / ".join(str(k) for k in F["es_share_by_bucket"]) + ".")
    bs = d["by_source"]
    W("5. How a download's document type is known",
      "Download URLs carry no document type. Each download is typed by the strongest "
      f"evidence available: the page it was clicked from ({bs.get('page', 0):,}), the same "
      f"document typed that way elsewhere ({bs.get('doc match', 0):,}), or document "
      f"number order within the borrower ({bs.get('number order', 0):,}; a 'CD' less than "
      f"{CD_MIN_DAYS_AFTER_LE} days after the LE was sent is rejected). "
      f"{d['typed']/d['events']:.1%} of {d['events']:,} downloads are typed; the rest stay "
      "unknown. The three downloads_typed_by_* columns let you drop the weaker evidence.")
    N("Audio is a count",
      "Audio is the number of distinct audio clips that play on a page, as the "
      "professor's coding_dictionary defines it, measured from which page each clip is "
      "played from. It replaces the professor's 0/1, which is kept in the path dictionary "
      "(Audio_professor).")

    H("Findings that affect interpretation")
    W("Origination looks high here",
      f"{100*u.loan_originated.mean():.1f}% of borrowers in this file originated, against "
      f"{100*F['orig_all']:.1f}% across all loans in the extract. That is selection, NOT a "
      "treatment effect: activating Talkuments and progressing through a loan are both "
      "downstream of staying engaged.")
    W("The Loan Estimate usually precedes activation — but only to the day",
      f"t_activation_to_le_sent is negative for {(neg < 0).mean():.1%} of borrowers. The "
      "LE date has no time of day, so that overstates it: by calendar day the LE came "
      f"first for {F['le_before_day']:,} of {nle:,} borrowers "
      f"({F['le_before_day']/nle:.1%}), on the same day for {F['le_same_day']:,} "
      f"({F['le_same_day']/nle:.1%}, order unknowable), and after for "
      f"{F['le_after_day']:,}. The specification defines this variable in the opposite "
      "order, so its sign reads backwards. Negative durations are real and not clipped.")
    W("Blank activation is not 'no'",
      "activated_talkument is the lender's field. Blank means unknown. In this file it is "
      f"blank for {F['act_blank_with_loan']:,} borrowers whose loan has no lender value and "
      f"for the {F['no_loan']:,} who reach no loan at all; it is never 'no', because "
      "everyone here used the software. Across the pilot's Talkuments buckets "
      f"{F['act_blank_loans']:,} loans have no lender value.")
    N("Multi-loan borrowers",
      f"{F['multi_loan']:.1%} of borrowers with a loan hold more than one in the extract, "
      "and all loan-level columns describe their EARLIEST by application date. "
      f"loans_in_pilot flags them. {int(u.pilot_bucket_conflicting.sum()):,} borrowers hold "
      "loans in different buckets and are excluded from bucket comparisons "
      "(pilot_bucket_conflicting).")
    N("Spanish is a small group",
      f"After excluding conflicting buckets, {len(sp):,} Spanish-preference borrowers "
      f"remain for language comparisons — {int(spb.get(2, 0)):,} in bucket 2 and "
      f"{int(spb.get(3, 0)):,} in bucket 3. Adequate for large effects only.")

    H("Still missing")
    N("The professor's confirmation of our coding",
      "Cells we coded where his sheet was blank, the ProcessRelated '????', and the two "
      "pages where his GeneralFinancial differs from the rest of their topic are listed "
      "for him in output/dictionary_review_for_professor.xlsx and "
      "docs/Professor_Questions.md. Changing a value is an edit to "
      "docs/page_template_coding.csv and a re-run.")
    N("Document type lookup (optional)",
      "A table mapping LoanDocument id to document type would replace the typing evidence "
      "for downloads with a measured value.")
    W("Coverage of the loan extract — NOT even across buckets",
      f"It covers {F['extract_loans']:,} of {F['pilot_loans']:,} pilot loans "
      f"({F['extract_loans']/F['pilot_loans']:.1%}). The missing loans are " +
      ", ".join(f"{n:,} ({r:.1%}) of bucket {k}" for k, (n, r) in mb.items()) +
      ". Bucket 1 is missing at more than twice the rate of buckets 2 and 3, so any "
      "bucket-1-vs-treatment comparison of loan outcomes rests on a less complete "
      f"control group. {F['no_loan']:,} borrowers here reach no loan.")

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
def qa(ev, sess, u, dropped, args, attr) -> None:
    ri = FACTS["run_info"]
    L = ["# Phase 2 QA", "",
         f"Timeout {args.session_timeout} min · timezone {TIMEZONE} · "
         f"{len(u):,} borrowers · {len(sess):,} sessions · {len(ev):,} pageviews", "",
         f"Pre-pilot test accounts removed in phase 1 (DEC-AA): {ri['test_users']} "
         f"({ri['test_events']:,} events), pilot start {ri['pilot_start']}.",
         f"{dropped:,} non-pageview rows dropped before sessionization (browser assets, DEC-P"
         f"{'' if args.keep_translation_resources else '; language files, DEC-Z/DEC-AB'}).", ""]

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

    L += ["## 4. Audio time is credited to the parent page, not the clip", "",
          "FAILS IF any time_C differs from an independent recomputation that finds each "
          "clip's page by counting pages within the session (not by the forward-fill the "
          "pipeline uses) and credits the clip's dwell to that page's flags.", ""]
    tot = float(u.total_time_observed.sum())
    k = (ev.AudioMp3 == 0).astype(int).groupby([ev.user_hash, ev.session_id]).cumsum()
    worst, bad_cols = (None, 0.0), 0
    for c in CHARACTERISTICS:
        page_flag = ev[c].where(ev.AudioMp3 == 0).groupby([ev.user_hash, ev.session_id, k]).transform("first")
        credited = ev[c].where((ev.AudioMp3 == 0) | (k == 0), page_flag)
        expect = float(ev.time_on_page.where(credited == 1).sum())
        own = float(ev.time_on_page.where(ev[c] == 1).sum())
        got = float(u[f"time_{c.replace('_provisional', '')}"].sum())
        bad_cols += int(got != expect)
        if abs(own - expect) > worst[1]:
            worst = (c, abs(own - expect), expect, own, got)
    L += [f"- time_ columns disagreeing with the recomputation: **{bad_cols}** of "
          f"{len(CHARACTERISTICS)} ({'PASS' if bad_cols == 0 else 'FAIL'})"]
    if worst[0]:
        L += [f"- most sensitive: `{worst[0]}` — {worst[4]:,.0f} s credited; "
              f"{worst[3]:,.0f} s if clips kept their own flags"]
    L += [f"- mp3 rows with no parent page in their session: {FACTS['orphan_mp3']:,} "
          "(credited their own flags, spec §5; spec §8 diagnostic 16)", ""]

    L += ["## 4b. Event context (DEC-AC)", "",
          "FAILS IF a download flagged LEDownload/CDDownload is not also Download and the "
          "matching Document, if a non-download row is a download of either type, or if a "
          "Dashboard view carries CDDocument 1 before the borrower's first CD evidence.", ""]
    is_doc = ev.path.str.startswith(pc.DOWNLOAD_PREFIX)
    v1 = int(((ev.LEDownload == 1) & ~((ev.Download == 1) & (ev.LEDocument == 1))).sum()
             + ((ev.CDDownload == 1) & ~((ev.Download == 1) & (ev.CDDocument == 1))).sum())
    v2 = int((~is_doc & ((ev.LEDownload == 1) | (ev.CDDownload == 1))).sum())
    cdev = ev.loc[(ev.template == "/Module/closing-disclosure-made-clear")
                  | (ev.download_type == "CD")].groupby("user_hash").eventdate.min()
    dash = ev.template == "/Dashboard"
    v3 = int((dash & (ev.CDDocument == 1) & ~(ev.eventdate >= ev.user_hash.map(cdev))).sum())
    D = DL_STATS
    L += [f"- download flag identity violations: **{v1}** ({'PASS' if v1 == 0 else 'FAIL'})",
          f"- non-download rows typed as a download: **{v2}** ({'PASS' if v2 == 0 else 'FAIL'})",
          f"- Dashboard CD before any CD evidence: **{v3}** ({'PASS' if v3 == 0 else 'FAIL'})",
          f"- context cells filled: {FACTS['context_cells']:,}; Dashboard views {FACTS['dash_views']:,}, "
          f"of which after the borrower's first CD evidence {FACTS['dash_cd']:,}",
          f"- downloads typed: {D['typed']:,} of {D['events']:,} ({D['typed']/D['events']:.1%}); "
          f"by source {D['by_source']}; by type {D['by_type']}",
          f"- document ids typed by page more than once: {D['ids_page_typed_twice']:,}, "
          f"of which typed two different ways: {D['ids_conflicting']:,}",
          f"- number-order 'CD' rejected for falling within {CD_MIN_DAYS_AFTER_LE} days of the "
          f"LE being sent: {D['cd_rejected_timing']:,}", "",
          "Timing evidence (reported, not used to set the page-typed values): days from the LE "
          "being sent to the download. A CD should rarely be within 3 days.", "",
          "| evidence | type | downloads | median days after LE sent | within 3 days |",
          "|---|---|---|---|---|"]
    for _, r in D["timing"].iterrows():
        L.append(f"| {r['how']} | {r['t']} | {int(r['n']):,} | {r['median_days']:.1f} | "
                 f"{r['within_3d']:.0%} |")
    L.append("")

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
              "pages_MortgageRelated", "time_MortgageRelated", "pages_LEDocument",
              "pages_CDDownload", "milestone_blank_reason"]:
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
             f"covering {FACTS['extract_loans']:,} of {FACTS['pilot_loans']:,} pilot loans")
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

    L += ["## 8. Specification §8 hard identities", "",
          "Each FAILS on the stated condition; numbers are users (or rows) violating it.", ""]
    ps = sess.groupby("user_hash").pages_in_session.sum().reindex(u.user_hash.values).values
    starts = ev.groupby("user_hash").session_start.sum().reindex(u.user_hash.values).values
    gs = ev.groupby(["user_hash", "session_id"])
    pages18 = [c for c in u.columns if c.startswith("pages_") and c != "pages_unattributable"]
    checks = [
        ("2. unique_webpages_visited <= webpages_visited",
         int((u.unique_webpages_visited > u.webpages_visited).sum())),
        ("3. sum(pages_in_session) == webpages_visited", int((ps != u.webpages_visited).sum())),
        ("5. num_sessions == rows with session_start == 1", int((starts != u.num_sessions).sum())),
        ("6. audio_clips_clicked == sum(AudioMp3) <= webpages_visited",
         int(((u.audio_clips_clicked != u.pages_AudioMp3)
              | (u.audio_clips_clicked > u.webpages_visited)).sum())),
        (f"7. pages_C <= webpages_visited for all {len(pages18)} pages_ columns",
         int(sum((u[c].fillna(0) > u.webpages_visited).sum() for c in pages18))),
        ("8. every session has exactly one start and one end (sessions)",
         int(((gs.session_start.sum() != 1) | (gs.session_end.sum() != 1)).sum())),
        ("9. no negative time_on_page / session_duration / inter_session_elapsed / "
         "t_activation_to_last_access (rows+users)",
         int((ev.time_on_page < 0).sum() + (sess.session_duration < 0).sum()
             + (sess.inter_session_elapsed < 0).sum()
             + (u.t_activation_to_last_access < 0).sum())),
        ("10. t_le_sent_to_first_le_visit >= 0", int((u.t_le_sent_to_first_le_visit < 0).sum())),
    ]
    L += ["| identity | violations | |", "|---|---|---|"]
    L += [f"| {k} | {v:,} | {'PASS' if v == 0 else 'FAIL'} |" for k, v in checks]
    L += ["", "Diagnostics (spec §8, report don't fail):", "",
          f"- 15. single-pageview sessions: {int((sess.pages_in_session == 1).sum()):,} "
          f"({(sess.pages_in_session == 1).mean():.1%})",
          f"- 16. mp3 rows with no in-session parent: {FACTS['orphan_mp3']:,}",
          f"- 17. borrower-document downloads typed: {DL_STATS['typed']:,} of "
          f"{DL_STATS['events']:,} ({DL_STATS['typed']/DL_STATS['events']:.1%}), see §4b",
          f"- 18. ProcessRelated a superset of Borrower ∪ Lender? rows with Borrower or "
          f"Lender = 1 but ProcessRelated != 1: {FACTS['proc_not_superset']:,} "
          "(expected to fail, spec §7-E)",
          f"- var 40: users whose LE-related activity all predates the LE send date "
          f"(NULL by design): {FACTS['le_only_before']:,}", ""]

    (OUT / "qa_phase2.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
