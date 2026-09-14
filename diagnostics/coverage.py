#!/usr/bin/env python3
"""Read-only: dictionary coverage of beta_coding against the event log.

Answers Project_Brief 'Next actions' #1. Modifies no source file.
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "data" / "talkument_userinteractions.xlsx"
CODING = ROOT / "docs" / "Clickstream_path_frequencies_and_coding_scheme.xlsx"
OUT = ROOT / "diagnostics" / "output"
OUT.mkdir(parents=True, exist_ok=True)

ev = pd.read_excel(EVENTS, sheet_name="user_usage")
beta = pd.read_excel(CODING, sheet_name="beta_coding")
alpha = pd.read_excel(CODING, sheet_name="alphabetical", header=1)

ev["path"] = ev["path"].astype(str)
beta["CODING SCHEME"] = beta["CODING SCHEME"].astype(str)

lines = []
def w(s=""):
    lines.append(s)

w("# Dictionary Coverage Check")
w()
w("Read-only. Script: diagnostics/coverage.py")
w()
w("- Event rows: **{:,}**".format(len(ev)))
w("- Distinct event paths: **{:,}**".format(ev["path"].nunique()))
w("- beta_coding rows: **{:,}** (distinct keys: {:,})".format(len(beta), beta["CODING SCHEME"].nunique()))
w()

vc = ev["path"].value_counts()
paths = set(ev["path"])

for label, transform in [
    ("exact", lambda s: s),
    ("trimmed+lowercased", lambda s: s.str.strip().str.lower()),
]:
    ep = transform(pd.Series(sorted(paths)))
    bk = set(transform(beta["CODING SCHEME"]))
    ev_t = transform(ev["path"])
    md = int(ep.isin(bk).sum())
    mr = int(ev_t.isin(bk).sum())
    w("## Match mode: {}".format(label))
    w()
    w("| metric | value |")
    w("|---|---|")
    w("| distinct paths matched | {:,} / {:,} ({:.2%}) |".format(md, len(ep), md/len(ep)))
    w("| event rows matched | {:,} / {:,} ({:.2%}) |".format(mr, len(ev_t), mr/len(ev_t)))
    w("| dictionary keys never seen in log | {:,} / {:,} |".format(len(bk - set(ev_t)), len(bk)))
    w()

bk_exact = set(beta["CODING SCHEME"])
unmatched = vc[~vc.index.isin(bk_exact)]
w("## Top 40 unmapped paths by event volume (exact match)")
w()
w("Unmapped distinct paths: **{:,}**; unmapped events: **{:,}** ({:.2%} of all events)".format(
    len(unmatched), int(unmatched.sum()), unmatched.sum()/len(ev)))
w()
w("| rank | events | % of log | path |")
w("|---|---|---|---|")
for i, (p, c) in enumerate(unmatched.head(40).items(), 1):
    w("| {} | {:,} | {:.3%} | `{}` |".format(i, c, c/len(ev), p[:160]))
w()

never = sorted(bk_exact - paths)
w("## beta_coding keys with zero events in the log ({})".format(len(never)))
w()
for k in never[:60]:
    w("- `{}`".format(k[:160]))
if len(never) > 60:
    w("- ... and {} more".format(len(never)-60))
w()

w("## Sample of dictionary keys (are they literal paths or patterns?)")
w()
for s in beta["CODING SCHEME"].head(20).tolist():
    w("- `{}`".format(str(s)[:160]))
w()

w("### Prefix-match test (does each event path START WITH some dictionary key?)")
keys = sorted([k for k in bk_exact if k and k != "nan"], key=len, reverse=True)
def prefix_hit(p):
    for k in keys:
        if p.startswith(k):
            return k
    return None
uniq = vc.index.to_series()
hits = uniq.map(prefix_hit)
md2 = int(hits.notna().sum())
mr2 = int(vc[hits.notna().values].sum())
w()
w("| metric | value |")
w("|---|---|")
w("| distinct paths with a prefix match | {:,} / {:,} ({:.2%}) |".format(md2, len(uniq), md2/len(uniq)))
w("| event rows with a prefix match | {:,} / {:,} ({:.2%}) |".format(mr2, len(ev), mr2/len(ev)))
w()
unm2 = vc[hits.isna().values]
w("### Top 25 paths with no prefix match ({:,} distinct, {:,} events, {:.2%})".format(
    len(unm2), int(unm2.sum()), unm2.sum()/len(ev)))
w()
w("| events | path |")
w("|---|---|")
for p, c in unm2.head(25).items():
    w("| {:,} | `{}` |".format(c, p[:160]))
w()

w("## Cross-check: `alphabetical` frequency sheet vs event log")
alpha["path"] = alpha["path"].astype(str)
a_paths = set(alpha["path"])
w()
w("- `alphabetical` distinct paths: {:,}".format(len(a_paths)))
w("- overlap with event log distinct paths: {:,}".format(len(a_paths & paths)))
w("- in sheet only: {:,}; in log only: {:,}".format(len(a_paths - paths), len(paths - a_paths)))
w("- sheet Frequency total: {:,} vs event log rows: {:,}".format(int(alpha["Frequency"].sum()), len(ev)))
w()

(OUT / "coverage_report.md").write_text("\n".join(lines))
unmatched.rename("events").to_frame().to_csv(OUT / "unmapped_paths.csv")
print("WROTE", OUT / "coverage_report.md")

# ---------------------------------------------------------------------------
# Appendix: structure of the unmapped tail, and download-type identifiability
# ---------------------------------------------------------------------------
lines2 = []
def w2(s=""):
    lines2.append(s)

w2()
w2("## Appendix A. Structure of the unmapped tail")
w2()
w2("Raw '63% unmapped' is misleading. Grouped by first path segment:")
w2()

def seg1(p):
    parts = [x for x in p.split("/") if x]
    return "/" + parts[0] if parts else "/(root)"

g = unmatched.groupby(unmatched.index.to_series().map(seg1)).agg(["sum", "count"])
g.columns = ["events", "distinct_paths"]
g = g.sort_values("events", ascending=False)
w2("| first segment | unmapped events | % of log | distinct paths |")
w2("|---|---|---|---|")
for seg, r in g.iterrows():
    w2("| `{}` | {:,} | {:.2%} | {:,} |".format(seg, int(r.events), r.events/len(ev), int(r.distinct_paths)))
w2()

NAV = ["/MyMortgage", "/Login", "/(root)", "/Dashboard", "/SelectLoan",
       "/favicon.ico", "/Logout", "/Contact", "/About", "/Privacy", "/Terms",
       "/cart.json", "/Glossary", "/survey", "/AwarenessQuestions"]
nav_events = int(g.reindex(NAV)["events"].fillna(0).sum())
dl_mask = unmatched.index.to_series().str.startswith("/Download/LoanDocument")
dl_events = int(unmatched[dl_mask.values].sum())
dl_distinct = int(dl_mask.sum())
content_events = int(unmatched.sum()) - nav_events - dl_events

w2("Collapsing that into three causes:")
w2()
w2("| cause | events | % of log | distinct paths | nature |")
w2("|---|---|---|---|---|")
w2("| Navigation / chrome / auth (no content to classify) | {:,} | {:.2%} | {:,} | expected; needs an explicit rule, not a silent 0 |".format(
    nav_events, nav_events/len(ev), int(g.reindex(NAV)["distinct_paths"].fillna(0).sum())))
w2("| `/Download/LoanDocument/{{id}}` per-loan documents | {:,} | {:.2%} | {:,} | pattern-matchable; type NOT in the URL (see Appendix B) |".format(
    dl_events, dl_events/len(ev), dl_distinct))
w2("| Real content pages absent from the dictionary | {:,} | {:.2%} | {:,} | genuine dictionary gap |".format(
    content_events, content_events/len(ev), len(unmatched) - dl_distinct - int(g.reindex(NAV)["distinct_paths"].fillna(0).sum())))
w2()

w2("## Appendix B. Are download URLs type-identifiable? (spec §7-B)")
w2()
shape = ev.loc[ev["path"].str.startswith("/Download", na=False), "path"].str.replace(r"\d+", "{N}", regex=True)
w2("Every `/Download` event path, with digit runs masked:")
w2()
w2("| shape | events |")
w2("|---|---|")
for s, c in shape.value_counts().items():
    w2("| `{}` | {:,} |".format(s, c))
w2()
le_hits = int(ev["path"].str.contains(r"/Download/LoanDocument/.*LE", regex=True, na=False).sum())
cd_hits = int(ev["path"].str.contains(r"/Download/LoanDocument/.*CD", regex=True, na=False).sum())
w2("Inherited regexes, evaluated against all {:,} event rows:".format(len(ev)))
w2()
w2("| regex | matching rows |")
w2("|---|---|")
w2("| `/Download/LoanDocument/.*LE` | **{:,}** |".format(le_hits))
w2("| `/Download/LoanDocument/.*CD` | **{:,}** |".format(cd_hits))
w2()
w2("**Answer: NO.** Download URLs carry a numeric LoanDocument id and no document-type token. "
   "`LEDocument`, `CDDocument`, `LEDownload` and `CDDownload` are therefore identically zero as built. "
   "Resolving them requires a LoanDocument-id -> document-type lookup that is not present in any file in `data/`.")
w2()

w2("## Appendix C. `alphabetical` sheet frequency total")
w2()
w2("- Sheet `Frequency` sums to **{:,}**; the event log has **{:,}** rows (ratio {:.2f}x).".format(
    int(alpha["Frequency"].sum()), len(ev), alpha["Frequency"].sum()/len(ev)))
w2("- Distinct paths agree ({:,} sheet vs {:,} log), so the sheet enumerates the same universe but its counts are not row counts of this log.".format(
    len(a_paths), ev["path"].nunique()))
w2()

(OUT / "coverage_report.md").write_text("\n".join(lines) + "\n".join(lines2))
print("APPENDED")
