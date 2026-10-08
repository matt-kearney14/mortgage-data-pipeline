#!/usr/bin/env python3
"""Independent recomputation of the user-level dataset from data/ (audit,
2026-10-01; rewritten 2026-10-08 for DEC-AA..AF).

Read-only. Writes diagnostics/output/audit_recompute.md.

Shares NO code with the pipeline: it re-reads the raw files with its own
loaders and re-implements every step from the written rules, mostly as plain
per-user loops rather than the pipeline's vectorised pandas, then compares cell
by cell with output/user_level_dataset.parquet. The one pipeline artefact it
uses is the path -> flag lookup output/path_dictionary_extended.csv, whose
cells are separately checked below against beta_coding and the template table.

Every check FAILS on any mismatching cell. What would make each fail:
  - test users kept, or the wrong users dropped (DEC-AA): every column
  - a language file counted as a page, or the wrong load language (DEC-AB):
    webpages_visited, the language columns, all dwell
  - a different sort, a session boundary at >= rather than >, dwell across a
    boundary: the session and time columns
  - a download typed by a different rule, or a 'context' cell left wrong (DEC-AC):
    pages_/time_ of the document and LE/CD columns, downloads_typed_by_*
  - clip dwell credited to the clip's own flags: the time_ columns
  - a wrong user -> loan join or the wrong 'earliest' loan: the milestone timers
"""
import bisect
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
OUT = ROOT / "diagnostics" / "output"; OUT.mkdir(parents=True, exist_ok=True)
TIMEOUT_S = 30 * 60
KEEP_TR = "--keep-translation-resources" in sys.argv
FL = ["Personalized", "GeneralFinancial", "MortgageRelated", "ProcessRelated",
      "BorrowerMortgageProcessRelated", "LenderMortgageProcessRelated", "LoanTermsRelated",
      "LoanEstimateRelated", "CDRelated", "LEDocument", "CDDocument", "Download", "Video",
      "Goal_to_inform", "Goal_to_Advise"]
L = [__doc__.strip(), ""]

# ------------------------------------------------------------------ raw load
e = pd.read_excel(D / "talkument_userinteractions.xlsx", sheet_name="user_usage", dtype=str)
e["row"] = np.arange(len(e))
e["ts"] = pd.to_datetime(e.eventdate, format="%Y-%m-%d %H:%M:%S")      # raises if any fails
n_raw, u_raw = len(e), e.user_hash.nunique()
loans = pd.read_csv(D / "loan_application_data_partial.csv", dtype=str).dropna(subset=["Loan_Number"])
for c, f in [("Application_Date", "%m/%d/%Y"), ("LE_TIL_Sent_Date", "%d%b%Y %H:%M:%S"),
             ("Lock_Date", "%d%b%Y %H:%M:%S"), ("Current_Status_Date", "%d%b%Y %H:%M:%S")]:
    loans[c] = pd.to_datetime(loans[c], format=f)
start = loans.Application_Date.min()
firsts = e.groupby("user_hash").ts.min()
testers = set(firsts.index[firsts < start])
e = e[~e.user_hash.isin(testers)]
e = e.sort_values(["user_hash", "ts", "row"], kind="mergesort").reset_index(drop=True)

# language (DEC-AB), on the full row sequence, before language files are dropped
acct = pd.read_excel(D / "talkument_useraccount.xlsx", sheet_name="users").drop_duplicates(
    "user_hash").set_index("user_hash")
lang = np.empty(len(e), dtype=object)
for uh, g in e.groupby("user_hash", sort=False):
    idx, paths, ts = g.index.values, g.path.values, g.ts.values.astype("int64") // 10**9
    tr = [i for i, p in enumerate(paths) if p.startswith("/translations/")]
    burst_lang, cur, last = {}, [], None
    for i in tr:                                     # loads: requests <= 2 s apart
        if last is not None and ts[i] - last > 2:
            ll = "es" if any(paths[j].startswith("/translations/es") for j in cur) else "en"
            burst_lang.update({j: ll for j in cur}); cur = []
        cur.append(i); last = ts[i]
    if cur:
        ll = "es" if any(paths[j].startswith("/translations/es") for j in cur) else "en"
        burst_lang.update({j: ll for j in cur})
    trt = [ts[i] for i in tr]
    seed = acct.provided_language.get(uh)
    seed = seed.strip().lower() if isinstance(seed, str) else None
    state = seed if seed in ("en", "es") else "en"
    for i in range(len(paths)):
        if i in burst_lang:
            state = burst_lang[i]
        elif trt:
            k = bisect.bisect_left(trt, ts[i])
            cands = [j for j in (k - 1, k) if 0 <= j < len(trt) and abs(trt[j] - ts[i]) <= 2]
            if cands:
                j = min(cands, key=lambda j: (abs(trt[j] - ts[i]), j))
                state = burst_lang[tr[j]]
        lang[idx[i]] = state
e["lang"] = lang
prev = e.groupby("user_hash").lang.shift(1)
sw_es = ((prev == "en") & (e.lang == "es")).groupby(e.user_hash).sum()
sw_en = ((prev == "es") & (e.lang == "en")).groupby(e.user_hash).sum()

drop = e.path.isin(["/favicon.ico", "/cart.json"])
if not KEEP_TR:
    drop |= e.path.str.startswith("/translations/")
e = e[~drop].reset_index(drop=True)
newu = e.user_hash.ne(e.user_hash.shift())
gap = (e.ts - e.ts.shift()).dt.total_seconds()
e["sstart"] = (newu | (gap > TIMEOUT_S)).astype(int)
e["sid"] = e.groupby("user_hash").sstart.cumsum()
same = (e.user_hash.shift(-1) == e.user_hash) & (e.sstart.shift(-1) == 0)
e["top"] = np.where(same, (e.ts.shift(-1) - e.ts).dt.total_seconds(), np.nan)
e["mp3"] = e.path.str.endswith(".mp3")
dic = pd.read_csv(ROOT / "output/path_dictionary_extended.csv")
e = e.merge(dic[["path", "template", "content_kind"] + FL + [f + "__prov" for f in FL]],
            on="path", how="left", validate="m:1")

# ------------------------------------------------------------------ primary loan
appl = pd.read_excel(D / "talkument_loan_applicants.xlsx", sheet_name="loan_applicants", dtype=str)
m = (appl.dropna(subset=["user_hash"])[["user_hash", "loannumber"]].drop_duplicates()
         .merge(loans, left_on="loannumber", right_on="Loan_Number"))
prim = m.sort_values(["user_hash", "Application_Date"], kind="mergesort").drop_duplicates(
    "user_hash").set_index("user_hash")

# ------------------------------------------------------------------ download type (DEC-AC)
tmpl = pd.read_csv(ROOT / "docs/page_template_coding.csv", dtype=str, keep_default_na=False
                   ).set_index("template")
SRC = {"le_module": "LE", "cd_module": "CD", "application_docs": "SPL"}
isdoc = e.path.str.startswith("/Download/LoanDocument/")
e["docid"] = pd.to_numeric(e.path.str.extract(r"/(\d+)$")[0], errors="coerce")
typ, how = [None] * len(e), [None] * len(e)
last_kind = None
for i, (p, k, mp3, newsess) in enumerate(zip(e.path, e.content_kind, e.mp3, e.sstart)):
    if newsess:
        last_kind = None
    if isdoc.iat[i]:
        t = SRC.get(last_kind)
        if t:
            typ[i], how[i] = t, "page"
    elif not mp3 and not p.startswith(("/Download/", "/download/")):
        last_kind = k
pt = {}
for i in range(len(e)):
    if how[i] == "page":
        pt.setdefault(e.docid.iat[i], set()).add(typ[i])
for i in range(len(e)):
    if isdoc.iat[i] and typ[i] is None and len(pt.get(e.docid.iat[i], ())) == 1:
        typ[i], how[i] = next(iter(pt[e.docid.iat[i]])), "doc match"
e["typ"], e["how"] = typ, how
le_sent = prim.LE_TIL_Sent_Date
for uh, g in e[isdoc].groupby("user_hash"):
    lab = g.dropna(subset=["typ"])
    lab = lab[lab.docid.map(lab.groupby("docid").typ.nunique()) == 1]   # one-way ids anchor
    le = lab[lab.typ == "LE"].docid; cd = lab[lab.typ == "CD"].docid
    op = g[g.typ.isna()].groupby("docid").ts.min()
    guess = {}
    for did, t0 in op.items():
        if len(cd):
            gss = "CD" if did >= cd.min() else "LE"
        elif len(le):
            gss = "LE" if did <= le.max() else "CD"
        else:
            gss = None if len(op) < 2 else ("LE" if did == op.index.min() else "CD")
        if gss == "CD" and uh in le_sent.index and pd.notna(le_sent[uh]) and \
                (t0 - le_sent[uh]).total_seconds() / 86400 < 3:
            gss = None
        guess[did] = gss
    for i in g.index[g.typ.isna()]:
        if guess.get(e.docid[i]):
            e.at[i, "typ"], e.at[i, "how"] = guess[e.docid[i]], "number order"

# fill 'context' cells
rows = {t: tmpl.loc[f"download:{t}"] for t in ("LE", "CD", "SPL")}
first_cd = e[(e.template == "/Module/closing-disclosure-made-clear") | (e.typ == "CD")].groupby(
    "user_hash").ts.min()
seen = e.ts >= e.user_hash.map(first_cd)
for f in FL:
    ctx = e[f + "__prov"] == "context"
    for i in e.index[ctx & isdoc]:
        t = e.typ[i]
        vals = {r[f] for r in rows.values()}
        e.at[i, f] = float(rows[t][f]) if t else (float(vals.pop()) if len(vals) == 1 else np.nan)
    dash = ctx & (e.template == "/Dashboard")
    e.loc[dash, f] = seen[dash].astype(float)
e["LEDownload"] = np.where(isdoc, np.where(e.typ.isna(), np.nan, (e.typ == "LE").astype(float)), 0.0)
e["CDDownload"] = np.where(isdoc, np.where(e.typ.isna(), np.nan, (e.typ == "CD").astype(float)), 0.0)
CH = FL + ["LEDownload", "CDDownload"]

# ------------------------------------------------------------------ user level
P = pd.read_parquet(ROOT / "output/user_level_dataset.parquet").set_index("user_hash")
L += [f"raw log {n_raw:,} rows, {u_raw:,} users; pilot start {start.date()}; "
      f"{len(testers)} test user(s) dropped; {len(e):,} pageviews, {e.user_hash.nunique():,} users. "
      f"Pipeline: {len(P):,} users, {int(P.webpages_visited.sum()):,} pageviews.", ""]
g = e.groupby("user_hash")
S = e.groupby(["user_hash", "sid"]).agg(a=("ts", "min"), b=("ts", "max"), n=("ts", "size")).reset_index()
S["dur"] = (S.b - S.a).dt.total_seconds()
S["ise"] = (S.a - S.groupby("user_hash").b.shift()).dt.total_seconds()
gs = S.groupby("user_hash")
M = pd.DataFrame({
    "webpages_visited": g.size(), "unique_webpages_visited": g.path.nunique(),
    "spanish_webpages_visited": g.lang.apply(lambda s: (s == "es").sum()),
    "english_webpages_visited": g.lang.apply(lambda s: (s == "en").sum()),
    "language_switches_to_spanish": sw_es, "language_switches_to_english": sw_en,
    "audio_clips_clicked": g.mp3.sum(), "days_accessed": g.ts.apply(lambda s: s.dt.normalize().nunique()),
    "num_sessions": g.sid.max(), "total_time_observed": g.top.sum(),
    "pages_time_not_observable": g.top.apply(lambda s: s.isna().sum()),
    "total_session_time": gs.dur.sum(), "median_session_duration": gs.dur.median(),
    "mean_session_duration": gs.dur.mean().round(1), "mean_pages_in_session": gs.n.mean().round(2),
    "mean_inter_session_elapsed": gs.ise.mean().round(1),
    "single_page_sessions": gs.n.apply(lambda s: (s == 1).sum()),
    "t_activation_to_last_access": (g.ts.max() - g.ts.min()).dt.total_seconds(),
})
# clip dwell goes to the most recent page of its session (spec §5 var 33): a loop
par = np.full(len(e), -1)
lastp = -1
for i, (mp3, ss) in enumerate(zip(e.mp3, e.sstart)):
    if ss:
        lastp = -1
    if not mp3:
        lastp = i
    par[i] = lastp
for c in CH:
    own = e[c]
    att = np.where(e.mp3 & (par >= 0), own.values[np.maximum(par, 0)], own.values)
    out = c
    M[f"pages_{out}"] = (own == 1).groupby(e.user_hash).sum()
    M[f"time_{out}"] = e.top.where(att == 1).groupby(e.user_hash).sum()
    if c not in ("LEDownload", "CDDownload"):
        M[f"unknown_{out}"] = own.isna().groupby(e.user_hash).sum()
for h, name in (("page", "downloads_typed_by_page"), ("doc match", "downloads_typed_by_doc_match"),
                ("number order", "downloads_typed_by_number_order")):
    M[name] = (e.how == h).groupby(e.user_hash).sum()
M["downloads_type_unknown"] = (isdoc & e.typ.isna()).groupby(e.user_hash).sum()

M["loans_in_pilot"] = m.groupby("user_hash").loannumber.nunique()
first, last = g.ts.min(), g.ts.max()
sec = lambda a, b: (a - b).dt.total_seconds()
M["t_application_to_activation"] = sec(first, prim.Application_Date.reindex(first.index))
M["t_activation_to_le_sent"] = sec(prim.LE_TIL_Sent_Date.reindex(first.index), first)
M["t_activation_to_lock"] = sec(prim.Lock_Date.reindex(first.index), first)
M["t_last_access_to_current_status"] = sec(prim.Current_Status_Date.reindex(last.index), last)
x = e.loc[e.LoanEstimateRelated == 1, ["user_hash", "ts"]].join(prim.LE_TIL_Sent_Date.rename("sent"), on="user_hash")
fa = x[x.ts >= x.sent].groupby("user_hash").ts.min()
M["t_le_sent_to_first_le_visit"] = sec(fa.reindex(first.index), prim.LE_TIL_Sent_Date.reindex(first.index))

# ------------------------------------------------------------------ compare
rename = {"pages_ProcessRelated": "pages_ProcessRelated", "time_ProcessRelated": "time_ProcessRelated",
          "unknown_ProcessRelated": "unknown_ProcessRelated"}
L += ["## Cell-by-cell comparison with output/user_level_dataset.parquet", "",
      "| column | mismatching users | |", "|---|---|---|"]
total = 0
users_ok = set(M.index) == set(P.index)
for c in M.columns:
    pcn = rename.get(c, c)
    if pcn not in P.columns:
        total += len(P); L.append(f"| `{c}` | column missing | FAIL |"); continue
    a = M[c].reindex(P.index).astype(float); b = P[pcn].astype(float)
    bad = int((~np.isclose(a, b, equal_nan=True)).sum()); total += bad
    L.append(f"| `{c}` | {bad:,} | {'PASS' if bad == 0 else 'FAIL'} |")
L += ["", f"- same set of users as the pipeline: {'PASS' if users_ok else 'FAIL'}",
      f"**{len(M.columns)} columns, {total:,} mismatching cells — "
      f"{'PASS' if total == 0 and users_ok else 'FAIL'}.**", ""]

# ------------------------------------------------------------------ dictionary checks
beta = pd.read_excel(ROOT / "docs/Clickstream_path_frequencies_and_coding_scheme.xlsx", sheet_name="beta_coding")
beta["k"] = beta["CODING SCHEME"].astype(str)
bb = beta[beta.k.str.startswith("/")].drop_duplicates("k").set_index("k")
mism = 0
for f in FL:
    if f not in bb:
        continue
    c = dic[dic[f + "__prov"] == "coded"].set_index("path")[f]
    r = bb[f].reindex(c.index)
    mism += int((~((c == r) | (c.isna() & r.isna()))).sum())
ours = dic[dic.template.notna()]
tm = 0
for f in FL:
    s = ours[ours[f + "__prov"].isin(["coded_by_us", "navigation"])]
    want = s.template.map(tmpl[f]).astype(float)
    tm += int((s[f].astype(float) != want).sum())
L += ["## Dictionary cells against their stated source", "",
      f"- cells stamped `coded` that differ from beta_coding: **{mism}** "
      f"({'PASS' if mism == 0 else 'FAIL'})",
      f"- cells stamped `coded_by_us`/`navigation` that differ from docs/page_template_coding.csv: "
      f"**{tm}** ({'PASS' if tm == 0 else 'FAIL'})", ""]

(OUT / "audit_recompute.md").write_text("\n".join(L))
print("\n".join(L[L.index("## Cell-by-cell comparison with output/user_level_dataset.parquet"):]))

