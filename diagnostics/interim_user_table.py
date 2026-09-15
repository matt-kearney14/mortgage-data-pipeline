#!/usr/bin/env python3
"""Interim user-level table — ONLY variables that are built and verified.

Not the Phase 2 deliverable. Deliberately excludes everything that depends on
sessionization (not built), and the nine variables blocked on missing inputs
(DEC-F). Every column here is computable from output/phase1_url_features.parquet
with no further assumptions.

Writes output/user_level_interim.xlsx and .parquet.
"""
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
df = pd.read_parquet(OUT / "phase1_url_features.parquet")

# /favicon.ico and /cart.json are browser asset requests, not pageviews (DEC-H).
# Excluded from every count here; Phase 2 plan D3 makes this permanent.
pv = df[df.row_class != "non_pageview"].copy()

FLAGS = ["Personalized", "GeneralFinancial", "MortgageRelated",
         "ProcessRelated_provisional", "BorrowerMortgageProcessRelated",
         "LenderMortgageProcessRelated", "LoanTermsRelated", "LoanEstimateRelated",
         "CDRelated", "Download", "Video", "Goal_to_inform", "Goal_to_Advise"]

g = pv.groupby("user_hash")
u = pd.DataFrame(index=g.size().index)

# --- volume: exact, no coverage caveat ---------------------------------------
u["webpages_visited"] = g.size()
u["unique_webpages_visited"] = g.path.nunique()
u["audio_clips_clicked"] = g.AudioMp3.sum()            # spec: sum(AudioMp3)
u["spanish_webpages_visited"] = g.Spanish_YN.sum()
u["english_webpages_visited"] = g.English_YN.sum()
u["days_accessed"] = g.eventdate.apply(lambda s: s.dt.date.nunique())

# --- span: spec var 37, needs no external file -------------------------------
span = g.eventdate.agg(["min", "max"])
u["first_access"] = span["min"]
u["last_access"] = span["max"]
u["t_activation_to_last_access_s"] = (span["max"] - span["min"]).dt.total_seconds().astype("Int64")

# --- per-characteristic page counts, each with its unknown count -------------
# NULL is not zero (DEC-L): pages_X counts only confirmed 1s, pages_X_unknown
# counts rows where the characteristic could not be determined. Reporting both
# is what stops a low count being read as low engagement.
for f in FLAGS:
    short = f.replace("_provisional", "")
    u[f"pages_{short}"] = g[f].apply(lambda s: int((s == 1).sum()))
    u[f"unknown_{short}"] = g[f].apply(lambda s: int(s.isna().sum()))

u["pct_pages_classified"] = (1 - u["unknown_MortgageRelated"] / u["webpages_visited"]).round(4)

# --- account attributes, for grouping ----------------------------------------
acct = pd.read_excel(ROOT / "data" / "talkument_useraccount.xlsx", sheet_name="users")
acct = acct.drop_duplicates("user_hash").set_index("user_hash")
u["provided_language"] = acct["provided_language"].reindex(u.index)
u["expertise_level"] = acct["expertise_level"].reindex(u.index)

# pilot arm, bridged loan_number -> loannumber (indirect; see inventory Q5)
appl = pd.read_excel(ROOT / "data" / "talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
buck = pd.read_excel(ROOT / "data" / "talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
bridge = (appl.dropna(subset=["user_hash"])
              .merge(buck, left_on="loannumber", right_on="loan_number", how="inner"))
# a user with conflicting buckets across loans is left NA rather than guessed
bmap = bridge.groupby("user_hash")["bucket"].agg(lambda s: s.iloc[0] if s.nunique() == 1 else np.nan)
u["pilot_bucket"] = bmap.reindex(u.index)

u = u.reset_index()
u.to_parquet(OUT / "user_level_interim.parquet", index=False)
u.to_excel(OUT / "user_level_interim.xlsx", index=False)
print(f"users {len(u):,}  columns {u.shape[1]}")
print(f"  pilot_bucket resolved for {int(u.pilot_bucket.notna().sum()):,}")
print(f"  expertise_level present for {int(u.expertise_level.notna().sum()):,}")
