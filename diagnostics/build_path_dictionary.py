#!/usr/bin/env python3
"""Build an extended path -> characteristic dictionary covering every path in the
event log, with explicit provenance on every cell.

Read-only with respect to all source files. Writes:
  output/path_dictionary_extended.csv          one row per distinct log path
  output/dictionary_review_for_professor.xlsx  inferred + unresolved rows to confirm
  diagnostics/output/dictionary_inference_report.md

Inference rule (see report for leave-one-out validation):
  format-scoped flags  {Audio, Video, Personalized} inherit from coded paths of the
                       same presentation format  (/Module, /Faq, /Slideshow, ...)
  topic-scoped flags   (all others) inherit from coded paths of the same content
                       topic slug (budgeting-basics, your-va-fixed-rate-loan, ...)
  audio clips          inherit from a coded clip with the same CC_{n} id, which is
                       the clip's identity; the path prefix is only the page it was
                       played from
  navigation pages     all content flags set to 0 by rule, provenance 'navigation'
  no basis             left NULL and routed to the professor review sheet.
                       NEVER silently defaulted to 0 (CLAUDE.md, Data hygiene #2).
"""
import re
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "data" / "talkument_userinteractions.xlsx"
CODING = ROOT / "docs" / "Clickstream_path_frequencies_and_coding_scheme.xlsx"
OUT = ROOT / "output"
DIAG = ROOT / "diagnostics" / "output"
OUT.mkdir(exist_ok=True); DIAG.mkdir(parents=True, exist_ok=True)

FLAGS = ['Personalized','GeneralFinancial','MortgageRelated','ProcessRelated',
 'BorrowerMortgageProcessRelated','LenderMortgageProcessRelated','LoanTermsRelated',
 'LoanEstimateRelated','CDRelated','CDDocument','Download','Audio','Video',
 'Goal_to_inform','Goal_to_Advise']
FMT_SCOPED = {'Audio', 'Video', 'Personalized'}

# Language flags are deliberately excluded from inheritance: the professor coded
# English(Y/N)=1 and Spanish(Y/N)=0 on every single row, so the dictionary carries
# zero language information. Language is emitted separately as `path_language`.

NAV = {'/MyMortgage','/Login','/Login/','/Logout','/Dashboard','/SelectLoan','/Contact',
       '/About','/Privacy','/Terms','/Glossary','/survey','/'}
NAV_PREFIX = ('/Login/','/Dashboard/','/AwarenessQuestions')
NON_PAGEVIEW = {'/favicon.ico','/cart.json'}

# ---------------------------------------------------------------- load
ev = pd.read_excel(EVENTS, sheet_name="user_usage")
ev["path"] = ev["path"].astype(str)
vc = ev["path"].value_counts()
N = len(ev)

beta = pd.read_excel(CODING, sheet_name="beta_coding")
beta["CODING SCHEME"] = beta["CODING SCHEME"].astype(str)
beta = beta.rename(columns={'English(Y/N)': 'English_raw', 'Spanish (Y/N)': 'Spanish_raw'})
coded = beta[beta["CODING SCHEME"].str.startswith("/")].drop_duplicates("CODING SCHEME").copy()

# ---------------------------------------------------------------- parse
def topic_universe(paths):
    """Topic slugs are the /Module/<slug> names, which name content units directly."""
    t = {p.split("/", 2)[2] for p in paths
         if p.startswith("/Module/") and not p.endswith(".mp3") and p.count("/") >= 2}
    t = {re.sub(r"-\d+$", "", s) for s in t if s}          # fold -1 / -2 variants
    return sorted({s for s in t if s}, key=len, reverse=True)

TOPICS = topic_universe(set(vc.index) | set(coded["CODING SCHEME"]))

def cc_id(p):
    m = re.search(r"CC_(\d+)\.mp3$", p)
    return m.group(1) if m else None

def parse(p):
    """-> (format, topic, cc_id)"""
    if p.endswith(".mp3"):
        return ("AUDIO", None, cc_id(p))
    parts = [x for x in p.split("/") if x]
    if not parts:
        return ("/(root)", None, None)
    fmt = "/" + parts[0]
    rest = "/".join(parts[1:])
    rest_folded = re.sub(r"-\d+$", "", rest)
    for t in TOPICS:
        if rest_folded.startswith(t):
            return (fmt, t, None)
    return (fmt, None, None)

for df in (coded,):
    parsed = df["CODING SCHEME"].apply(lambda p: pd.Series(parse(p), index=["fmt","topic","cc"]))
    for c in ("fmt","topic","cc"):
        df[c] = parsed[c]

coded_pages = coded[coded.fmt != "AUDIO"]
coded_audio = coded[coded.fmt == "AUDIO"].drop_duplicates("cc").set_index("cc")

# ---------------------------------------------------------------- inference
def topic_value(topic, flag, exclude=None):
    if topic is None: return None, None
    s = coded_pages[(coded_pages.topic == topic)]
    if exclude is not None: s = s[s["CODING SCHEME"] != exclude]
    v = s[flag].dropna()
    if not len(v): return None, None
    src = s.loc[v.index, "CODING SCHEME"].iloc[0]
    return v.mode().iloc[0], src

def format_value(fmt, flag, exclude=None):
    if fmt is None: return None, None
    s = coded_pages[(coded_pages.fmt == fmt)]
    if exclude is not None: s = s[s["CODING SCHEME"] != exclude]
    v = s[flag].dropna()
    if not len(v): return None, None
    src = s.loc[v.index, "CODING SCHEME"].iloc[0]
    return v.mode().iloc[0], src

def infer(fmt, topic, flag, exclude=None):
    if flag in FMT_SCOPED:
        val, src = format_value(fmt, flag, exclude)
        return val, src, "inferred_format"
    val, src = topic_value(topic, flag, exclude)
    return val, src, "inferred_topic"

# ---------------------------------------------------------------- LOO validation
loo = []
cp = coded_pages[coded_pages.topic.notna()].reset_index(drop=True)
for _, row in cp.iterrows():
    for f in FLAGS:
        if pd.isna(row[f]): continue
        pred, _, _ = infer(row.fmt, row.topic, f, exclude=row["CODING SCHEME"])
        if pred is not None:
            loo.append((f, int(pred == row[f])))
loo_df = (pd.DataFrame(loo, columns=["flag","correct"])
          .groupby("flag")["correct"].agg(["sum","count"]))
loo_df["accuracy"] = loo_df["sum"] / loo_df["count"]

# ---------------------------------------------------------------- build
coded_idx = coded.set_index("CODING SCHEME")
records = []
for path in vc.index:
    fmt, topic, cc = parse(path)
    rec = {"path": path, "events": int(vc[path]), "format": fmt, "topic": topic}

    # language is read off the path, never from the dictionary
    if "/es/" in path or path.startswith("/translations/es"):
        rec["path_language"] = "es"
    elif "/en/" in path or path.startswith("/translations/en"):
        rec["path_language"] = "en"
    else:
        rec["path_language"] = "unknown"

    if path in NON_PAGEVIEW:
        rec["row_class"] = "non_pageview"
        for f in FLAGS: rec[f], rec[f+"__prov"] = None, "non_pageview"
        records.append(rec); continue

    if path in NAV or path.startswith(NAV_PREFIX):
        rec["row_class"] = "navigation"
        for f in FLAGS: rec[f], rec[f+"__prov"] = 0, "navigation"
        records.append(rec); continue

    if path.startswith("/Download/LoanDocument/"):
        rec["row_class"] = "download_document"
        for f in FLAGS: rec[f], rec[f+"__prov"] = None, "unresolved_download_type"
        rec["Download"], rec["Download__prov"] = 1, "rule_download_path"
        records.append(rec); continue

    if path.startswith("/download/samples/"):
        rec["row_class"] = "download_sample"
        for f in FLAGS: rec[f], rec[f+"__prov"] = None, "unresolved"
        rec["Download"], rec["Download__prov"] = 1, "rule_download_path"
        records.append(rec); continue

    exact = coded_idx.loc[path] if path in coded_idx.index else None
    src_audio = coded_audio.loc[cc] if (cc is not None and cc in coded_audio.index) else None
    rec["row_class"] = ("coded" if exact is not None
                        else "audio_clip" if cc is not None
                        else "content_page")

    for f in FLAGS:
        if exact is not None and not pd.isna(exact[f]):
            rec[f], rec[f+"__prov"] = exact[f], "coded"
        elif src_audio is not None and not pd.isna(src_audio[f]):
            rec[f], rec[f+"__prov"] = src_audio[f], "inferred_audio_cc"
            rec.setdefault("inference_source", src_audio.name and coded_audio.loc[cc,"CODING SCHEME"] if "CODING SCHEME" in coded_audio.columns else None)
        else:
            val, src, kind = infer(fmt, topic, f)
            if val is not None:
                rec[f], rec[f+"__prov"] = val, kind
                rec["inference_source"] = src
            else:
                rec[f], rec[f+"__prov"] = None, "unresolved"
    records.append(rec)

d = pd.DataFrame(records)
d = d[["path","events","row_class","format","topic","path_language","inference_source"]
      + [c for f in FLAGS for c in (f, f+"__prov")]]
d.to_csv(OUT / "path_dictionary_extended.csv", index=False)

# ---------------------------------------------------------------- report
L = []
def w(s=""): L.append(s)

w("# Extended Path Dictionary — build & validation report")
w()
w("Script: `diagnostics/build_path_dictionary.py`. Sources unmodified.")
w()
w("## 1. Leave-one-out validation of the inference rule")
w()
w("Each coded page row is held out and predicted from the remaining coded rows.")
w("A flag with no non-null value anywhere in its scope is not predicted and not counted —")
w("it is reported as unresolved instead of being scored as a free win.")
w()
w("| flag | scope | correct | tested | accuracy |")
w("|---|---|---|---|---|")
for f in FLAGS:
    if f in loo_df.index:
        r = loo_df.loc[f]
        w("| `{}` | {} | {} | {} | {:.1%} |".format(
            f, "format" if f in FMT_SCOPED else "topic", int(r["sum"]), int(r["count"]), r["accuracy"]))
    else:
        w("| `{}` | {} | — | 0 | not predictable (no coded values in scope) |".format(
            f, "format" if f in FMT_SCOPED else "topic"))
w()
if loo_df["count"].sum():
    w("**Overall: {:.1%}** ({} of {} held-out cells).".format(
        loo_df["sum"].sum()/loo_df["count"].sum(), int(loo_df["sum"].sum()), int(loo_df["count"].sum())))
w()

w("## 2. Path rows by class")
w()
cls = d.groupby("row_class").agg(paths=("path","size"), events=("events","sum")).sort_values("events", ascending=False)
w("| class | distinct paths | events | % of log |")
w("|---|---|---|---|")
for k, r in cls.iterrows():
    w("| {} | {:,} | {:,} | {:.2%} |".format(k, r.paths, r.events, r.events/N))
w("| **total** | **{:,}** | **{:,}** | 100% |".format(cls.paths.sum(), cls.events.sum()))
w()

w("## 3. Event coverage per flag, before vs after")
w()
w("'Covered' = the event's path carries a non-null value for that flag.")
w()
w("| flag | before (coded only) | after (coded + inferred + rule) | change |")
w("|---|---|---|---|")
ev_j = ev.join(d.set_index("path"), on="path")
before_src = coded_idx
ev_b = ev.join(before_src[FLAGS], on="path", rsuffix="_b")
for f in FLAGS:
    bef = ev_b[f].notna().sum() / N
    aft = ev_j[f].notna().sum() / N
    w("| `{}` | {:.1%} | {:.1%} | {:+.1f} pp |".format(f, bef, aft, 100*(aft-bef)))
w()

w("## 4. Rows needing the professor")
w()
unres = d[d[[f+"__prov" for f in FLAGS]].eq("unresolved").any(axis=1)]
w("- paths with at least one unresolved flag: **{:,}** ({:,} events, {:.2%} of log)".format(
    len(unres), int(unres.events.sum()), unres.events.sum()/N))
inf = d[d[[f+"__prov" for f in FLAGS]].isin(["inferred_topic","inferred_format","inferred_audio_cc"]).any(axis=1)]
w("- paths carrying at least one inferred flag: **{:,}** ({:,} events, {:.2%} of log)".format(
    len(inf), int(inf.events.sum()), inf.events.sum()/N))
w()
TOPIC_SCOPED = [f for f in FLAGS if f not in FMT_SCOPED]
d["n_unresolved_topic_flags"] = d[[f+"__prov" for f in TOPIC_SCOPED]].eq("unresolved").sum(axis=1)

w("### 4a. Content pages the professor still needs to code")
w()
w("Content pages where topic-scoped flags could not be inferred because **no coded page")
w("shares their topic**. Ranked by event volume — this is the whole list, and coding the")
w("top few closes most of the remaining gap.")
w()
orphan = d[(d.row_class == "content_page") & (d.n_unresolved_topic_flags > 0)] \
           .sort_values("events", ascending=False)
w("| events | % of log | path | topic | unresolved flags |")
w("|---|---|---|---|---|")
for _, r in orphan.iterrows():
    w("| {:,} | {:.2%} | `{}` | {} | {}/{} |".format(
        r.events, r.events/N, r.path, r.topic or "—", int(r.n_unresolved_topic_flags), len(TOPIC_SCOPED)))
w()
w("Total: **{:,} paths, {:,} events ({:.2%} of the log)**.".format(
    len(orphan), int(orphan.events.sum()), orphan.events.sum()/N))
w()

w("### 4b. Flags no page row can ever receive")
w()
w("These are blank on every coded page row in `beta_coding`, so there is nothing to")
w("inherit. Their apparent coverage below comes only from navigation pages (set to 0 by")
w("rule) and audio clips — **no content page carries a value**.")
w()
w("| flag | coded page rows with a value | content-page coverage after inference |")
w("|---|---|---|")
pages_only = d[d.row_class.isin(["coded","content_page"])]
ev_pages = ev.join(d.set_index("path"), on="path")
ev_pages = ev_pages[ev_pages.row_class.isin(["coded","content_page"])]
for f in TOPIC_SCOPED:
    n_coded = int(coded_pages[f].notna().sum())
    cov = ev_pages[f].notna().sum() / max(len(ev_pages), 1)
    if n_coded == 0:
        w("| `{}` | **0 of {}** | {:.1%} |".format(f, len(coded_pages), cov))
w()

(DIAG / "dictionary_inference_report.md").write_text("\n".join(L))

# ---------------------------------------------------------------- review workbook
with pd.ExcelWriter(OUT / "dictionary_review_for_professor.xlsx", engine="openpyxl") as xl:
    cls.reset_index().to_excel(xl, sheet_name="Summary", index=False)
    loo_df.reset_index().to_excel(xl, sheet_name="Rule_validation", index=False)
    cols = ["path","events","row_class","format","topic","inference_source"] + \
           [c for f in FLAGS for c in (f, f+"__prov")]
    inf_pages = inf[inf.row_class != "audio_clip"][cols].sort_values("events", ascending=False)
    inf_pages.to_excel(xl, sheet_name="Inferred_pages", index=False)
    inf[inf.row_class == "audio_clip"][cols].sort_values("events", ascending=False) \
        .to_excel(xl, sheet_name="Inferred_audio", index=False)
    orphan[cols].to_excel(xl, sheet_name="Needs_coding", index=False)

print("distinct paths:", len(d))
print(cls.to_string())
print("\nWROTE output/path_dictionary_extended.csv")
print("WROTE output/dictionary_review_for_professor.xlsx")
print("WROTE diagnostics/output/dictionary_inference_report.md")
