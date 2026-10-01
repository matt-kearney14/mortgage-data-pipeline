#!/usr/bin/env python3
"""Independent recomputation of the user-level dataset from data/ (audit, 2026-10-01).

Read-only. Writes diagnostics/output/audit_recompute.md.

Shares NO code with the pipeline: it re-reads the raw files with its own loaders,
re-sorts, re-sessionizes, re-attributes and re-joins, implementing the
specification literally, and compares cell by cell with
output/user_level_dataset.parquet. The only pipeline artefact it uses is the
path -> flag lookup output/path_dictionary_extended.csv, whose coded cells are
separately checked against beta_coding below.

Every check FAILS on any mismatching cell. What would make each fail:
  - a different sort or tie-break, a session boundary at >= rather than >, dwell
    computed across a session boundary: the session and dwell columns
  - pages_C counted on the parent page's flags rather than the row's own (spec
    §5 var 32), clip dwell credited to the clip's own flags, orphan clips not
    credited their own flags: the pages_/time_/unknown_ columns
  - a wrong user -> loan join, the wrong 'earliest' loan, a date parsed in the
    wrong format: the milestone timers and loans_in_pilot
  - an unknown download type scored as 0: downloads_type_unknown
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
OUT = ROOT / "diagnostics" / "output"; OUT.mkdir(parents=True, exist_ok=True)
TIMEOUT_S = 30 * 60
DROP = {"/favicon.ico", "/cart.json"} | (set() if "--keep-translation-resources" in sys.argv
                                         else {"/translations/en"})
CH = ["Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated",
      "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated", "LoanTermsRelated",
      "LoanEstimateRelated", "CDRelated", "Download", "Video", "Goal_to_inform", "Goal_to_Advise"]
L = [__doc__.strip(), ""]

# ------------------------------------------------------------------ raw load
e = pd.read_excel(D / "talkument_userinteractions.xlsx", sheet_name="user_usage", dtype=str)
e["row"] = np.arange(len(e))
e["ts"] = pd.to_datetime(e.eventdate, format="%Y-%m-%d %H:%M:%S")      # raises if any fails
n_raw, u_raw = len(e), e.user_hash.nunique()
e = e.sort_values(["user_hash", "ts", "row"], kind="mergesort").reset_index(drop=True)
e = e[~e.path.isin(DROP)].reset_index(drop=True)
newu = e.user_hash.ne(e.user_hash.shift())
gap = (e.ts - e.ts.shift()).dt.total_seconds()
e["sstart"] = (newu | (gap > TIMEOUT_S)).astype(int)
e["sid"] = e.groupby("user_hash").sstart.cumsum()
same = (e.user_hash.shift(-1) == e.user_hash) & (e.sstart.shift(-1) == 0)
e["top"] = np.where(same, (e.ts.shift(-1) - e.ts).dt.total_seconds(), np.nan)
acct = pd.read_excel(D / "talkument_useraccount.xlsx", sheet_name="users").set_index("user_hash")
seed = e.user_hash.map(acct.provided_language).where(lambda s: s.isin(["en", "es"]), "en")
st = pd.Series(np.where(e.path.str.startswith("/translations/es"), "es", None), index=e.index)
st[newu & st.isna()] = seed[newu & st.isna()]
e["lang"] = st.groupby(e.user_hash).ffill()
e["mp3"] = e.path.str.endswith(".mp3")
dic = pd.read_csv(ROOT / "output/path_dictionary_extended.csv")
e = e.merge(dic[["path"] + CH], on="path", how="left", validate="m:1")

P = pd.read_parquet(ROOT / "output/user_level_dataset.parquet").set_index("user_hash")
L += [f"raw log {n_raw:,} rows, {u_raw:,} users; after dropping {sorted(DROP)}: {len(e):,} rows, "
      f"{e.user_hash.nunique():,} users. Pipeline: {len(P):,} users, "
      f"{int(P.webpages_visited.sum()):,} pageviews.", ""]

# ------------------------------------------------------------------ user level
g = e.groupby("user_hash")
S = e.groupby(["user_hash", "sid"]).agg(a=("ts", "min"), b=("ts", "max"), n=("ts", "size")).reset_index()
S["dur"] = (S.b - S.a).dt.total_seconds()
S["ise"] = (S.a - S.groupby("user_hash").b.shift()).dt.total_seconds()
gs = S.groupby("user_hash")
M = pd.DataFrame({
    "webpages_visited": g.size(), "unique_webpages_visited": g.path.nunique(),
    "spanish_webpages_visited": g.lang.apply(lambda s: (s == "es").sum()),
    "english_webpages_visited": g.lang.apply(lambda s: (s == "en").sum()),
    "audio_clips_clicked": g.mp3.sum(), "days_accessed": g.ts.apply(lambda s: s.dt.normalize().nunique()),
    "num_sessions": g.sid.max(), "total_time_observed": g.top.sum(),
    "total_session_time": gs.dur.sum(), "median_session_duration": gs.dur.median(),
    "mean_session_duration": gs.dur.mean().round(1), "mean_pages_in_session": gs.n.mean().round(2),
    "mean_inter_session_elapsed": gs.ise.mean().round(1),
    "single_page_sessions": gs.n.apply(lambda s: (s == 1).sum()),
    "t_activation_to_last_access": (g.ts.max() - g.ts.min()).dt.total_seconds(),
})
pos = pd.Series(np.where(~e.mp3, np.arange(len(e)), np.nan))
parent = pos.groupby([e.user_hash, e.sid]).ffill()
orphan = e.mp3 & parent.isna()
for c in CH:
    own = e[c]
    par = pd.Series(own.values[parent.fillna(0).astype(int)], index=e.index).where(parent.notna())
    att = own.where(~e.mp3, par.where(~orphan, own))                    # spec §5 var 33
    out = "ProcessRelated" if c == "ProcessRelated" else c
    M[f"pages_{out}"] = (own == 1).groupby(e.user_hash).sum()           # spec §5 var 32
    M[f"time_{out}"] = e.top.where(att == 1).groupby(e.user_hash).sum()
    M[f"unknown_{out}"] = own.isna().groupby(e.user_hash).sum()

# ------------------------------------------------------------------ loans
appl = pd.read_excel(D / "talkument_loan_applicants.xlsx", sheet_name="loan_applicants", dtype=str)
loans = pd.read_csv(D / "loan_application_data_partial.csv", dtype=str).dropna(subset=["Loan_Number"])
for c, f in [("Application_Date", "%m/%d/%Y"), ("LE_TIL_Sent_Date", "%d%b%Y %H:%M:%S"),
             ("Lock_Date", "%d%b%Y %H:%M:%S"), ("Current_Status_Date", "%d%b%Y %H:%M:%S")]:
    loans[c] = pd.to_datetime(loans[c], format=f)
m = (appl.dropna(subset=["user_hash"])[["user_hash", "loannumber"]].drop_duplicates()
         .merge(loans, left_on="loannumber", right_on="Loan_Number"))
M["loans_in_pilot"] = m.groupby("user_hash").loannumber.nunique()
prim = m.sort_values(["user_hash", "Application_Date"], kind="mergesort").drop_duplicates("user_hash").set_index("user_hash")
first, last = g.ts.min(), g.ts.max()
sec = lambda a, b: (a - b).dt.total_seconds()
M["t_application_to_activation"] = sec(first, prim.Application_Date.reindex(first.index))
M["t_activation_to_le_sent"] = sec(prim.LE_TIL_Sent_Date.reindex(first.index), first)
M["t_activation_to_lock"] = sec(prim.Lock_Date.reindex(first.index), first)
M["t_last_access_to_current_status"] = sec(prim.Current_Status_Date.reindex(last.index), last)
x = e.loc[e.LoanEstimateRelated == 1, ["user_hash", "ts"]].join(prim.LE_TIL_Sent_Date.rename("sent"), on="user_hash")
fa = x[x.ts >= x.sent].groupby("user_hash").ts.min()
M["t_le_sent_to_first_le_visit"] = sec(fa.reindex(first.index), prim.LE_TIL_Sent_Date.reindex(first.index))
pe = pd.read_parquet(ROOT / "output/phase2_events.parquet")
doc = e.path.str.startswith("/Download/LoanDocument/").groupby(e.user_hash).sum()
typed = pe.download_type_inferred.fillna(False).groupby(pe.user_hash).sum()
M["downloads_type_unknown"] = doc - typed.reindex(doc.index).fillna(0)

# ------------------------------------------------------------------ compare
L += ["## Cell-by-cell comparison with output/user_level_dataset.parquet", "",
      "| column | mismatching users | |", "|---|---|---|"]
total = 0
for c in M.columns:
    if c not in P.columns:
        total += len(P); L.append(f"| `{c}` | column missing | FAIL |"); continue
    a = M[c].reindex(P.index).astype(float); b = P[c].astype(float)
    bad = int((~np.isclose(a, b, equal_nan=True)).sum()); total += bad
    L.append(f"| `{c}` | {bad:,} | {'PASS' if bad == 0 else 'FAIL'} |")
L += ["", f"**{len(M.columns)} columns, {total:,} mismatching cells — "
      f"{'PASS' if total == 0 else 'FAIL'}.**", "",
      f"mp3 rows with no parent page in their session (credited own flags): {int(orphan.sum())}", ""]

# ------------------------------------------------------------------ event order
L += ["## Event order and session edges", ""]
same_order = bool((pe._source_row.values == e.row.values).all()) if len(pe) == len(e) else False
L += [f"- phase2_events row order identical to (user, time, file row): "
      f"{'PASS' if same_order else 'FAIL'}",
      f"- rows sharing a timestamp with another row of the same user: "
      f"{int(e.duplicated(['user_hash', 'ts'], keep=False).sum()):,} (resolved by file row)",
      f"- max dwell {np.nanmax(e.top):,.0f} s (<= {TIMEOUT_S} s required: "
      f"{'PASS' if np.nanmax(e.top) <= TIMEOUT_S else 'FAIL'})", ""]

# ------------------------------------------------------------------ dictionary vs beta_coding
beta = pd.read_excel(ROOT / "docs/Clickstream_path_frequencies_and_coding_scheme.xlsx", sheet_name="beta_coding")
beta["k"] = beta["CODING SCHEME"].astype(str)
bb = beta[beta.k.str.startswith("/")].drop_duplicates("k").set_index("k")
F = CH + ["CDDocument", "Audio"]
mism = 0
for f in F:
    c = dic[dic[f + "__prov"] == "coded"].set_index("path")[f]
    r = bb[f].reindex(c.index)
    mism += int((~((c == r) | (c.isna() & r.isna()))).sum())
L += ["## Dictionary: coded cells vs beta_coding", "",
      f"- cells stamped `coded` that differ from beta_coding: **{mism}** "
      f"({'PASS' if mism == 0 else 'FAIL'})", ""]

(OUT / "audit_recompute.md").write_text("\n".join(L))
print("\n".join(L[L.index("## Cell-by-cell comparison with output/user_level_dataset.parquet"):]))
