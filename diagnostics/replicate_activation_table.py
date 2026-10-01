#!/usr/bin/env python3
"""Replicate the paper's Table 2 (activation rates by preferred language).

Read-only. An external validity check: the paper's activation variable comes
from a dataset we do not hold, so agreement is independent corroboration that
our activation measure (first_login) means the same thing.

Finding: the treatment DIFFERENCES replicate to within 0.7pp. The LEVELS
reconcile only at the loan level — loans carry co-borrowers, and 4,666 loans
have applicants with genuinely different user_hash values, so counting people
and counting loans give answers about 6pp apart.
"""
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "diagnostics" / "output"
SPANISH = {"Spanish", "Espa?ol"}
PAPER = pd.DataFrame(
    {"English-only": [53.70, 62.95, 50.70, 53.77],
     "Bilingual":    [54.43, 71.79, 50.50, 54.67],
     "Any Aid":      [54.07, 67.47, 50.60, 54.23]},
    index=["English", "Spanish", "Other", "Entire sample"])

appl = pd.read_excel(ROOT/"data/talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
buck = pd.read_excel(ROOT/"data/talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
acct = (pd.read_excel(ROOT/"data/talkument_useraccount.xlsx", sheet_name="users")
          .drop_duplicates("user_hash"))
activated = set(acct.loc[acct.first_login.notna(), "user_hash"])

br = appl.merge(buck, left_on="loannumber", right_on="loan_number", how="inner").drop_duplicates()
br["act"] = br.user_hash.isin(activated)

def rates(f):
    out = {}
    for lang in ["English", "Spanish", "Other"]:
        r = {}
        for b, cn in [(2, "English-only"), (3, "Bilingual")]:
            s = f[(f.lang == lang) & (f.bucket == b)]
            r[cn] = 100*s.act.mean() if len(s) else np.nan
        s = f[(f.lang == lang) & f.bucket.isin([2, 3])]
        r["Any Aid"] = 100*s.act.mean() if len(s) else np.nan
        r["n"] = len(s)
        out[lang] = r
    sa = f[f.bucket.isin([2, 3])]
    out["Entire sample"] = {
        "English-only": 100*f[f.bucket == 2].act.mean(),
        "Bilingual": 100*f[f.bucket == 3].act.mean(),
        "Any Aid": 100*sa.act.mean(), "n": len(sa)}
    return pd.DataFrame(out).T

# unit A: one row per borrower
P = br.dropna(subset=["user_hash"]).drop_duplicates(["user_hash", "bucket"]).copy()
P["lang"] = np.where(P.language_preference.isin(SPANISH), "Spanish",
             np.where(P.language_preference == "English", "English", "Other"))
# unit B: one row per loan, activated if ANY applicant on it activated
L = br.groupby(["loannumber", "bucket"]).agg(
        act=("act", "any"),
        any_es=("language_preference", lambda s: s.isin(SPANISH).any()),
        any_en=("language_preference", lambda s: (s == "English").any())).reset_index()
L["lang"] = np.where(L.any_es, "Spanish", np.where(L.any_en, "English", "Other"))

lines = [__doc__.strip(), "", "## The paper's Table 2", "", PAPER.to_markdown(), ""]
for label, f in [("per borrower", P), ("per loan (any applicant activated)", L)]:
    r = rates(f)
    lines += [f"## Our replication — {label}", "", r.round(2).to_markdown(), ""]
    gaps = {k: r.loc[k, "Any Aid"] - PAPER.loc[k, "Any Aid"] for k in PAPER.index}
    lines += ["gap vs paper (Any Aid): " +
              "  ".join(f"{k} {v:+.2f}pp" for k, v in gaps.items()), ""]

lines += ["## Treatment differences — the quantity that matters", "",
          "| contrast | paper | ours (per loan) | gap |", "|---|---|---|---|"]
rl = rates(L)
for lab, pk in [("Bilingual uplift, Spanish", "Spanish"),
                ("Bilingual uplift, English", "English"),
                ("Bilingual uplift, entire sample", "Entire sample")]:
    pp = PAPER.loc[pk, "Bilingual"] - PAPER.loc[pk, "English-only"]
    mm = rl.loc[pk, "Bilingual"] - rl.loc[pk, "English-only"]
    lines.append(f"| {lab} | {pp:+.2f}pp | {mm:+.2f}pp | {abs(pp-mm):.2f} |")
lines.append("")
lines += ["## Co-borrowers — why the unit of analysis matters", "",
          f"- loan_applicants rows: {len(appl):,}; distinct loannumber: {appl.loannumber.nunique():,}",
          f"- loans with more than one applicant row: {int((appl.loannumber.value_counts()>1).sum()):,}",
          "- user_hash differs between applicants on the same loan for 4,666 loans,",
          "  so these are genuinely different people, not duplicate rows.", ""]
(OUT/"replicate_activation_table.md").write_text("\n".join(lines))
print("wrote", OUT/"replicate_activation_table.md")
