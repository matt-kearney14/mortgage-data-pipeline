#!/usr/bin/env python3
"""Cross-validation suite: every quantity we can measure two independent ways.

Read-only. Writes diagnostics/output/crossvalidation.md.

Take one quantity, measure it two ways that share no code and no source field,
and force them to agree. A check that cannot fail is not a check, so each test
below states what would make it fail, and its PASS/FAIL is computed, not typed.
"""
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "diagnostics" / "output"; OUT.mkdir(parents=True, exist_ok=True)
u = pd.read_parquet(ROOT / "output/user_level_dataset.parquet")
ev = pd.read_parquet(ROOT / "output/phase2_events.parquet")
d = pd.read_csv(ROOT / "data/loan_application_data_partial.csv")
d = d[d.Loan_Number.notna()].copy()
d["loannumber"] = d.Loan_Number.astype("int64").astype(str)
appl = pd.read_excel(ROOT / "data/talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
appl["loannumber"] = appl.loannumber.astype(str)
acct = pd.read_excel(ROOT / "data/talkument_useraccount.xlsx", sheet_name="users")
buck = pd.read_excel(ROOT / "data/talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
buck["loan_number"] = buck.loan_number.astype(str)
L = [__doc__.strip(), ""]
verdict = lambda ok: "PASS" if ok else "FAIL"

# --- 1. loan type vs content read ------------------------------------------
L += ["## 1. Loan type predicts content read", "",
      "FAILS IF the user -> loan join is wrong: the table would come out flat instead "
      "of diagonal. Content is matched on the raw URL path string, so this tests the "
      "loan join only — it does NOT test the inferred path dictionary.", ""]
e = ev.merge(u[["user_hash", "hmda_loan_type"]], on="user_hash", how="left")
rows = {}
for label, slug in [("VA content", "your-va-fixed-rate-loan"),
                    ("FHA content", "your-fha-fixed-rate-loan"),
                    ("Conventional content", "your-conventional-fixed-loan")]:
    hit = e.path.str.contains(slug, regex=False)
    rows[label] = {lt: round(100 * e.loc[(e.hmda_loan_type == lt) & hit, "user_hash"].nunique()
                             / max(e.loc[e.hmda_loan_type == lt, "user_hash"].nunique(), 1), 1)
                   for lt in ["VA", "FHA", "Conventional"]}
t = pd.DataFrame(rows).T
diag, off = t.values.diagonal(), t.values[~np.eye(3, dtype=bool)]
ratio = float(diag.min() / max(off.max(), 1e-9))
L += [t.to_markdown(), "",
      f"Smallest diagonal {diag.min():.1f} vs largest off-diagonal {off.max():.1f} — "
      f"{ratio:.0f}x separation. {verdict(ratio > 5)} (threshold 5x).", ""]

# --- 2. APR vs note rate ----------------------------------------------------
L += ["## 2. APR is at least the note rate", "",
      "FAILS IF the rate columns are mismatched or mis-parsed. APR includes fees, so it "
      "cannot be lower than the note rate; a handful of exceptions are data entry.", ""]
b = d.dropna(subset=["Interest_Rate", "APR"]); b = b[b.APR < 30]
bad = int((b.APR < b.Interest_Rate).sum())
L += [f"- loans with both: {len(b):,}", f"- APR below note rate: **{bad}** ({bad/len(b):.2%}) "
      f"{verdict(bad / len(b) < 0.01)} (threshold 1%)",
      f"- median spread: {(b.APR - b.Interest_Rate).median():.3f} points", ""]

# --- 3. lock and rate co-occur ---------------------------------------------
L += ["## 3. A rate exists only once locked", "",
      "Not pass/fail — a structural property worth stating, since it makes any "
      "rate analysis a selected subsample.", ""]
L += [f"- `Interest_Rate` null: {d.Interest_Rate.isna().sum():,} ({d.Interest_Rate.isna().mean():.1%})",
      f"- locked but no rate: {int(d.dropna(subset=['Lock_Date']).Interest_Rate.isna().sum()):,}",
      f"- rate but no lock date: {int(d[d.Interest_Rate.notna()].Lock_Date.isna().sum()):,}", ""]

# --- 4. calendar coverage ---------------------------------------------------
ap = pd.to_datetime(d.Application_Date, format="%m/%d/%Y", errors="coerce")
cs = pd.to_datetime(d.Current_Status_Date, format="%d%b%Y %H:%M:%S", errors="coerce")
after = int((ev.eventdate > cs.max()).sum())
early = ev[ev.eventdate < ap.min()]
early_loans = int(u.set_index("user_hash").loans_in_pilot
                  .reindex(early.user_hash.unique()).notna().sum())
L += ["## 4. The two datasets cover the same period", "",
      "FAILS IF clickstream exists after the last loan status date, which would mean "
      "the extract is stale relative to the log.", ""]
L += [f"- clickstream: {ev.eventdate.min().date()} .. {ev.eventdate.max().date()}",
      f"- applications: {ap.min().date()} .. {ap.max().date()}",
      f"- status dates: {cs.min().date()} .. {cs.max().date()}",
      f"- events after the last status date: **{after}** ({verdict(after == 0)})",
      f"- events before the first application: {len(early):,} from "
      f"{early.user_hash.nunique()} borrowers, {early_loans} of whom match a loan in the extract", ""]

# --- 5. activation, two sources --------------------------------------------
L += ["## 5. Activation: lender field vs account file vs clickstream", "",
      "FAILS IF the lender's Activated_Talkument disagrees with first_login in "
      "talkument_useraccount.xlsx (any applicant on the loan) on any loan where both "
      "exist. Blank lender values are UNKNOWN and are excluded, not scored 'No'.", ""]
act_users = set(acct.loc[acct.first_login.notna(), "user_hash"])
click_users = set(u.user_hash)
link = appl.dropna(subset=["user_hash"]).groupby("loannumber").user_hash
by_login = link.apply(lambda s: s.isin(act_users).any())
by_click = link.apply(lambda s: s.isin(click_users).any())
x = d.set_index("loannumber")
known = x.Activated_Talkument.notna() & x.index.isin(by_login.index)
lender = x.Activated_Talkument[known].eq("Yes")
dis_login = int((lender != by_login.reindex(lender.index)).sum())
dis_click = int((lender != by_click.reindex(lender.index)).sum())
# at_least_one_activated vs Activated_Talkument, on loans where either is present
both = d[d.at_least_one_activated.notna() | d.Activated_Talkument.notna()]
agree2 = (both.at_least_one_activated.eq(1) == both.Activated_Talkument.eq("Yes")).mean()
tk = x.bucket.isin(["talkument", "talkument_multi"])
blank = x.index[tk & x.Activated_Talkument.isna()]
L += [f"- loans with a lender value and an applicant link: {int(known.sum()):,}",
      f"- lender vs first_login disagreements: **{dis_login}** ({verdict(dis_login == 0)})",
      f"- lender vs clickstream presence disagreements: {dis_click} (lender 'Yes' with no "
      "applicant in the log; expected small, since first_login can exist without a logged pageview)",
      f"- at_least_one_activated vs Activated_Talkument, where either is present: {agree2:.2%} agree",
      f"- Talkuments-bucket loans with a blank lender value: {len(blank):,}; of those, "
      f"{int(by_click.reindex(blank).astype('boolean').fillna(False).sum()):,} have an applicant "
      "in the clickstream (the clickstream resolves them as activated)", ""]

# --- 6. bucket label, two sources -------------------------------------------
L += ["## 6. Pilot bucket: pilot_buckets.xlsx vs the loan extract's own label", "",
      "FAILS IF any loan's bucket number disagrees with the extract's bucket name.", ""]
m = d.merge(buck, left_on="loannumber", right_on="loan_number", how="inner")
name = {1: "no talkument", 2: "talkument", 3: "talkument_multi"}
dis = int((m.bucket_y.map(name) != m.bucket_x).sum())
L += [f"- loans in both: {len(m):,} of {len(d):,} in the extract",
      f"- disagreements: **{dis}** ({verdict(dis == 0)})", ""]

(OUT / "crossvalidation.md").write_text("\n".join(L))
print("wrote", OUT / "crossvalidation.md")
