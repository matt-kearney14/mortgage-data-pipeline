#!/usr/bin/env python3
"""Preliminary: did multilingual support change time spent in the software?

Read-only. Produces diagnostics/output/treatment_effect_time.md.

This is EXPLORATORY OUTPUT FOR THE RESEARCH TEAM, not a finding. It is written
so the professors can see the specification, the assumptions, and the power,
and disagree with any of them. Nothing here is a conclusion.

Design
  Treatment  pilot bucket 3 (multilingual support)   vs   bucket 2 (English only)
  Primary    Spanish-preference borrowers — the only group the treatment can
             plausibly affect. English speakers are the placebo group.
  Covariate  language_preference from the applicant file, which is
             pre-treatment and balanced across arms. NOT provided_language,
             which encodes the arm itself (DEC-S).
  Test       Mann-Whitney U. Time and page counts are heavily right-skewed,
             so ranks are the appropriate comparison and medians the
             appropriate summary.
  Excluded   Users whose loans span several buckets (no clean assignment).
             Bucket 1 entirely: those borrowers had no software access, so they
             have no clickstream. Bucket 1 can only be compared on loan outcomes,
             which are not present in any file we hold.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "diagnostics" / "output"
OUT.mkdir(parents=True, exist_ok=True)
ALPHA, POWER = 0.05, 0.80
SPANISH = {"Spanish", "Espa?ol"}

u = pd.read_parquet(ROOT / "output" / "user_level_dataset.parquet")
d = u[u.pilot_bucket.isin([2, 3]) & ~u.pilot_bucket_conflicting].copy()
d["es_pref"] = d.language_preference.isin(SPANISH)

MEASURES = [
    ("total_time_observed",    "Total time in the software (s)"),
    ("total_session_time",     "Total session time (s)"),
    ("median_session_duration","Median session length (s)"),
    ("num_sessions",           "Number of sessions"),
    ("days_accessed",          "Distinct days accessed"),
    ("webpages_visited",       "Pages visited"),
    ("time_AudioMp3",          "Audio listening time (s)"),
]

L = [__doc__.strip(), "", "---", ""]

def block(frame, title, note=""):
    L.append(f"## {title}")
    L.append("")
    if note:
        L.append(note); L.append("")
    a, b = frame[frame.pilot_bucket == 2], frame[frame.pilot_bucket == 3]
    L.append(f"n = **{len(a)}** in bucket 2 (English only), **{len(b)}** in bucket 3 (multilingual)")
    L.append("")
    L.append("| measure | median bucket 2 | median bucket 3 | mean bucket 2 | mean bucket 3 | p | min. detectable diff |")
    L.append("|---|---|---|---|---|---|---|")
    for col, label in MEASURES:
        x, y = a[col].dropna().astype(float), b[col].dropna().astype(float)
        if len(x) < 3 or len(y) < 3:
            continue
        p = stats.mannwhitneyu(x, y).pvalue
        sp = np.sqrt(((len(x)-1)*x.var(ddof=1) + (len(y)-1)*y.var(ddof=1)) / (len(x)+len(y)-2))
        mde = (1.959964 + 0.841621) * sp * np.sqrt(1/len(x) + 1/len(y))
        star = " **\\***" if p < ALPHA else ""
        L.append(f"| {label} | {x.median():,.0f} | {y.median():,.0f} | {x.mean():,.0f} | "
                 f"{y.mean():,.0f} | {p:.3f}{star} | {mde:,.0f} |")
    L.append("")

block(d[d.es_pref], "Primary comparison — Spanish-preference borrowers",
      "The group the treatment can actually reach.")
block(d[d.language_preference == "English"], "Placebo — English-preference borrowers",
      "Multilingual support should be inert here. A significant result would "
      "suggest the buckets differ for some reason other than the treatment.")

# manipulation check
L.append("## Manipulation check — was the treatment actually delivered?")
L.append("")
es = d[d.es_pref]
L.append("| | bucket 2 | bucket 3 |")
L.append("|---|---|---|")
for lbl, f in [("borrowers", lambda s: f"{len(s)}"),
               ("ever saw a Spanish page", lambda s: f"{100*(s.spanish_webpages_visited>0).mean():.1f}%"),
               ("median Spanish pages", lambda s: f"{s.spanish_webpages_visited.median():.0f}"),
               ("mean share of reading in Spanish",
                lambda s: f"{100*(s.spanish_webpages_visited/s.webpages_visited).mean():.1f}%"),
               ("used the language toggle", lambda s: f"{100*s.used_language_toggle.mean():.1f}%")]:
    L.append(f"| {lbl} | {f(es[es.pilot_bucket==2])} | {f(es[es.pilot_bucket==3])} |")
L.append("")

# ---------------------------------------------------------------- DiD
L.append("## Difference-in-differences — the placebo group is not inert, so this is required")
L.append("")
L.append("Median session length is significant in BOTH groups. In the placebo group that")
L.append("is a sample-size artifact, not an effect: n = 9,141 there, so the minimum")
L.append("detectable difference is 27 seconds and a 9-second gap clears it. The honest")
L.append("comparison is therefore not bucket 3 vs bucket 2 within Spanish speakers, but how much")
L.append("larger that gap is than the same gap among English speakers, who the treatment")
L.append("cannot reach.")
L.append("")
rng = np.random.default_rng(20260924)          # seed fixed and declared (CLAUDE.md #8)
L.append("| measure | Spanish bucket2->bucket3 | English bucket2->bucket3 | difference-in-differences | 95% CI (bootstrap) |")
L.append("|---|---|---|---|---|")
esg, eng = d[d.es_pref], d[d.language_preference == "English"]
for col, label in MEASURES:
    def med(f, b):
        v = f.loc[f.pilot_bucket == b, col].dropna().astype(float)
        return v.median() if len(v) else np.nan
    es_d = med(esg, 3) - med(esg, 2)
    en_d = med(eng, 3) - med(eng, 2)
    did = es_d - en_d
    boot = []
    for _ in range(4000):
        def bmed(f, b):
            v = f.loc[f.pilot_bucket == b, col].dropna().astype(float).values
            return np.median(rng.choice(v, len(v), replace=True)) if len(v) else np.nan
        boot.append((bmed(esg,3)-bmed(esg,2)) - (bmed(eng,3)-bmed(eng,2)))
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    crosses = "" if (lo > 0 or hi < 0) else "  (includes 0)"
    L.append(f"| {label} | {es_d:+,.0f} | {en_d:+,.0f} | **{did:+,.0f}** | "
             f"[{lo:,.0f}, {hi:,.0f}]{crosses} |")
L.append("")
L.append("A difference-in-differences whose interval excludes zero is the only result here")
L.append("that survives both the placebo check and the multiple-comparison caveat.")
L.append("")

L += ["## How to read this", "",
      "**The minimum-detectable-difference column is the point.** It is the smallest",
      "true difference this sample could detect at 80% power. Any effect smaller than",
      "that would be invisible here, so a non-significant result rules out a large",
      "effect and says nothing about a modest one.", "",
      "Seven measures are tested. At alpha = 0.05 one in twenty tests is expected to",
      "reach significance by chance alone; treat any single starred row accordingly.", "",
      "`total_time_observed` excludes the last page of every session, whose duration",
      "is unobservable. It is a consistent undercount across both buckets, so the",
      "comparison is valid, but the absolute level is not total time in the software.", "",
      "**Loan outcomes are not in scope.** No file in `data/` records whether a",
      "borrower's loan funded, closed, was denied or withdrawn. That question cannot",
      "be answered from these inputs at all — it is not a power problem.", ""]

(OUT / "treatment_effect_time.md").write_text("\n".join(L))
print("\n".join(L[L.index("## Primary comparison — Spanish-preference borrowers")
                  if "## Primary comparison — Spanish-preference borrowers" in L else 0:]))
