#!/usr/bin/env python3
"""Replicate the paper's Table 2 (activation rates by preferred language).

Read-only. Writes diagnostics/output/replicate_activation_table.md.

Three tabulations, so the reader can see what does and does not reproduce:

  A. per borrower, activation = first_login in talkument_useraccount.xlsx
  B. per loan, activated if ANY applicant has a first_login
  C. per loan, the lender's own fields: Activated_Talkument (blank = unknown,
     excluded) and Borrower_Language_Preference (blank = 'Other'), from
     data/loan_application_data_partial.csv. This is the tabulation that
     reproduces the paper.

What C does and does not show. It reproduces the paper because it tabulates
the lender's fields, which are presumably the paper's own source; it is
therefore NOT independent evidence for anything this pipeline computes. The
independent evidence is section 'Activation, two sources', which compares the
lender's flag with first_login loan by loan.
"""
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "diagnostics" / "output"
OUT.mkdir(parents=True, exist_ok=True)
SPANISH = {"Spanish", "Espa?ol"}
PAPER = pd.DataFrame(
    {"English-only": [53.70, 62.95, 50.70, 53.77],
     "Bilingual":    [54.43, 71.79, 50.50, 54.67],
     "Any Aid":      [54.07, 67.47, 50.60, 54.23]},
    index=["English", "Spanish", "Other", "Entire sample"])

appl = pd.read_excel(ROOT/"data/talkument_loan_applicants.xlsx", sheet_name="loan_applicants")
appl["loannumber"] = appl.loannumber.astype(str)
buck = pd.read_excel(ROOT/"data/talkument_pilot_buckets.xlsx", sheet_name="pilot_record")
buck["loan_number"] = buck.loan_number.astype(str)
acct = (pd.read_excel(ROOT/"data/talkument_useraccount.xlsx", sheet_name="users")
          .drop_duplicates("user_hash"))
loans = pd.read_csv(ROOT/"data/loan_application_data_partial.csv")
loans = loans[loans.Loan_Number.notna()].copy()
loans["loannumber"] = loans.Loan_Number.astype("int64").astype(str)
activated = set(acct.loc[acct.first_login.notna(), "user_hash"])

br = appl.merge(buck, left_on="loannumber", right_on="loan_number", how="inner").drop_duplicates()
br["act"] = br.user_hash.isin(activated)


def rates(f):
    """f has columns lang, bucket (2 = English-only, 3 = Bilingual), act (NaN = unknown)."""
    out = {}
    for lang in ["English", "Spanish", "Other", "Entire sample"]:
        g = f if lang == "Entire sample" else f[f.lang == lang]
        r = {}
        for b, cn in [(2, "English-only"), (3, "Bilingual")]:
            s = g[g.bucket == b].act.dropna().astype(float)
            r[cn] = 100 * s.mean() if len(s) else np.nan
        s = g[g.bucket.isin([2, 3])].act.dropna().astype(float)
        r["Any Aid"] = 100 * s.mean() if len(s) else np.nan
        r["n"] = len(s)
        out[lang] = r
    return pd.DataFrame(out).T


# A: one row per borrower x bucket
A = br.dropna(subset=["user_hash"]).drop_duplicates(["user_hash", "bucket"]).copy()
A["lang"] = np.where(A.language_preference.isin(SPANISH), "Spanish",
             np.where(A.language_preference == "English", "English", "Other"))
# B: one row per loan, activated if ANY applicant activated
B = br.groupby(["loannumber", "bucket"]).agg(
        act=("act", "any"),
        any_es=("language_preference", lambda s: s.isin(SPANISH).any()),
        any_en=("language_preference", lambda s: (s == "English").any())).reset_index()
B["lang"] = np.where(B.any_es, "Spanish", np.where(B.any_en, "English", "Other"))
# C: the lender's fields, loan grain
C = loans.assign(
    bucket=loans.bucket.map({"no talkument": 1, "talkument": 2, "talkument_multi": 3}),
    act=loans.Activated_Talkument.map({"Yes": 1.0, "No": 0.0}),
    lang=loans.Borrower_Language_Preference.map(
        {"SpanishIndicator": "Spanish", "EnglishIndicator": "English"}).fillna("Other"))

lines = [__doc__.strip(), "", "## The paper's Table 2", "", PAPER.to_markdown(), ""]
for label, f in [("A. per borrower (first_login)", A),
                 ("B. per loan, any applicant (first_login)", B),
                 ("C. per loan, lender's fields, blank activation excluded", C)]:
    r = rates(f)
    diff = (r[PAPER.columns] - PAPER).abs().max().max()
    lines += [f"## {label}", "", r.round(2).to_markdown(), "",
              f"largest absolute gap to the paper over all 12 cells: **{diff:.2f} pp**", ""]
C_blank_no = C.assign(act=C.act.fillna(0.0))
r = rates(C_blank_no)
lines += ["## C', as C but blank activation scored 'No'", "",
          f"largest gap to the paper: **{(r[PAPER.columns] - PAPER).abs().max().max():.2f} pp** — "
          "scoring unknown as 'No' is what breaks the replication.", ""]

# activation, two sources (the independent check)
lnk = appl.dropna(subset=["user_hash"]).groupby("loannumber").user_hash.apply(
    lambda s: s.isin(activated).any())
x = loans.set_index("loannumber")
k = x.Activated_Talkument.notna() & x.index.isin(lnk.index)
dis = int((x.Activated_Talkument[k].eq("Yes") != lnk.reindex(x.index[k])).sum())
lines += ["## Activation, two sources", "",
          "FAILS IF the lender's Activated_Talkument and first_login (any applicant on the "
          "loan) disagree on any loan where both exist.", "",
          f"- loans compared: {int(k.sum()):,}; disagreements: **{dis}** "
          f"({'PASS' if dis == 0 else 'FAIL'})", ""]

# language, two sources
lp = appl.groupby("loannumber").language_preference.agg(
    lambda s: "Spanish" if s.isin(SPANISH).any() else ("English" if (s == "English").any() else "Other"))
bl = x.Borrower_Language_Preference.map({"SpanishIndicator": "Spanish", "EnglishIndicator": "English"})
kk = bl.notna() & x.index.isin(lp.index)
agree = (bl[kk] == lp.reindex(x.index[kk]))
lines += ["## Language, two sources", "",
          "Lender's Borrower_Language_Preference (English/Spanish indicators only) against the "
          "applicant file's language_preference (Spanish if ANY applicant on the loan is).", "",
          f"- loans compared: {int(kk.sum()):,}; agree {agree.mean():.2%}; "
          f"disagree {int((~agree).sum())}", ""]

lines += ["## Unit of analysis", "",
          f"- loan_applicants rows: {len(appl):,}; distinct loannumber: {appl.loannumber.nunique():,}",
          f"- loans with more than one applicant row: {int((appl.loannumber.value_counts()>1).sum()):,}",
          f"- loans whose applicant rows carry more than one distinct user_hash: "
          f"{int((appl.dropna(subset=['user_hash']).groupby('loannumber').user_hash.nunique()>1).sum()):,}",
          "- the lender's Coapplicant flag does not agree with counting these (DEC-W), so",
          "  treat this as 'several Talkuments identities per loan', not 'several borrowers'.", ""]
(OUT/"replicate_activation_table.md").write_text("\n".join(lines))
print("wrote", OUT/"replicate_activation_table.md")
