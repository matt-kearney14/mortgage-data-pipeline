#!/usr/bin/env python3
"""PIPELINE STEP 1 — the path dictionary: every path in the log -> its flags,
with the source of every cell recorded in a matching __prov column.

Rebuilt 2026-10-08 (DEC-AD). Replaces the sibling-inference build, which left
8% of events unresolved and had no basis for flags the professor never coded
on a page. Each cell now comes from exactly one of these, in this order:

  coded               the professor's own cell for this exact path (beta_coding)
  coded_same_page     his cell for another path of the same page (a -1/-2 variant)
  coded_same_clip     his cell for the same audio clip (CC_n) under another prefix
  coded_by_us         docs/page_template_coding.csv, filling a cell he left blank
  navigation          a navigation page: 0 by rule (also from the template table)
  clip_from_page      an audio clip he did not code: the flags of the page it plays on
  rule                fixed by what the row is (a clip is not a video, a page or a
                      document; Audio is a count, below)
  context             decided per event in phase 2 (download type, Dashboard CD)
  page_resource       not a pageview; no flags (DEC-Z, DEC-AB)
  unresolved          no template matches the path: NULL, listed for review,
                      NEVER defaulted to 0 (CLAUDE.md, Data hygiene 2)

Audio (DEC-AE) is the spec's count: the number of distinct audio clips that play
on the page. Which clips belong to which page is measured from the log: a clip
belongs to the page it is played from. Pages that share content (the FHA,
conventional, VA and ARM versions of one FAQ) share their clips, through the
template table's content_kind column. Clip rows get Audio 0.

Writes:
  output/path_dictionary_extended.csv          one row per distinct log path
  output/dictionary_review_for_professor.xlsx  what we coded, for him to confirm
  diagnostics/output/dictionary_inference_report.md
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline_common as pc                                     # noqa: E402

OUT, DIAG = pc.OUT, pc.ROOT / "diagnostics" / "output"
OUT.mkdir(exist_ok=True); DIAG.mkdir(parents=True, exist_ok=True)
FLAGS = pc.FLAGS
CLIP_HOST_MIN_SHARE = 0.20   # DEC-AE: a clip belongs to a page kind holding >= this share of its plays
CLIP_FIXED = {"Download": 0, "Video": 0, "LEDocument": 0, "CDDocument": 0}   # a clip is none of these
PROFESSOR = {"coded", "coded_same_page", "coded_same_clip"}


def cc_id(p):
    m = re.search(r"CC_(\d+)\.mp3$", p)
    return m.group(1) if m else None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot-start", help="override the pilot start date (DEC-AA), YYYY-MM-DD")
    a = ap.parse_args()
    if a.pilot_start:
        pc.PILOT_START = pd.Timestamp(a.pilot_start)
    pc.check_inputs([pc.EVENTS_FILE, pc.LOAN_FILE, pc.CODING_FILE, pc.TEMPLATE_FILE])

    ev, info = pc.load_events()
    N = len(ev)
    vc = (ev[pc.PATH].value_counts().rename_axis("path").reset_index(name="events")
            .sort_values(["events", "path"], ascending=[False, True], kind="mergesort"))
    T = pc.TEMPLATES.set_index("template")

    # ---------------------------------------------------------- the professor's sheet
    beta = pd.read_excel(pc.CODING_FILE, sheet_name="beta_coding")
    beta["CODING SCHEME"] = beta["CODING SCHEME"].astype(str)
    beta = beta[beta["CODING SCHEME"].str.startswith("/")].drop_duplicates("CODING SCHEME")
    for f in FLAGS + ["Audio"]:
        if f not in beta:
            beta[f] = np.nan                       # LEDocument is not in his sheet
    beta = beta.set_index("CODING SCHEME")
    prof_page = beta[~beta.index.str.endswith(".mp3")].copy()
    prof_page["template"] = prof_page.index.map(pc.template_of)
    prof_clip = beta[beta.index.str.endswith(".mp3")].copy()
    prof_clip["cc"] = prof_clip.index.map(cc_id)
    prof_clip = prof_clip.groupby("cc").first()    # one row per clip id

    # ---------------------------------------------------------- page cells
    def page_cells(path, tpl):
        """flag -> (value, prov) for a page path."""
        row = T.loc[tpl]
        same = prof_page[prof_page.template == tpl]
        out = {}
        for f in FLAGS:
            if path in beta.index and pd.notna(beta.at[path, f]):
                out[f] = (float(beta.at[path, f]), "coded")
                continue
            v = same[f].dropna()
            if len(v) and v.nunique() == 1:
                out[f] = (float(v.iloc[0]), "coded_same_page")
                continue
            ours = row[f]
            if ours == "context":
                out[f] = (np.nan, "context")
            elif ours == "":
                out[f] = (np.nan, "page_resource" if row.page_group == "page_resource"
                          else "unresolved")
            else:
                out[f] = (float(ours), "navigation" if row.page_group == "navigation"
                          else "coded_by_us")
        return out

    # ---------------------------------------------------------- clip -> page (DEC-AE)
    # A clip's page is the most recent real page in the same session. Downloads,
    # page resources and other clips are skipped; navigation pages cannot host.
    is_mp3 = ev[pc.PATH].str.endswith(".mp3")
    tpl_ev = ev[pc.PATH].map(lambda p: None if p.endswith(".mp3") else pc.template_of(p))
    group = tpl_ev.map(T.page_group)
    can_host = group.isin(["own_loan", "mortgage_process", "money_skills"]) & \
        ~ev[pc.PATH].str.startswith(pc.DOWNLOAD_PREFIX)
    _, sid = pc.session_ids(ev[pc.USER], ev[pc.TIME], pc.SESSION_TIMEOUT_MIN * 60)
    is_page = ~is_mp3 & group.ne("page_resource") & ~ev[pc.PATH].str.startswith(
        (pc.DOWNLOAD_PREFIX, "/download/"))
    host_i = pc.last_page_index(is_page, [ev[pc.USER], sid])
    host_tpl = pd.Series(np.nan, index=ev.index, dtype=object)
    ok = is_mp3 & host_i.notna()
    host_tpl[ok] = tpl_ev.reindex(host_i[ok].astype(int)).values
    host_tpl[ok] = host_tpl[ok].where(can_host.reindex(host_i[ok].astype(int)).values)
    plays = pd.DataFrame({"cc": ev[pc.PATH][is_mp3].map(cc_id), "host": host_tpl[is_mp3]})
    plays["kind"] = plays.host.map(T.content_kind)
    by_kind = plays.dropna(subset=["kind"]).groupby(["cc", "kind"]).size().rename("plays").reset_index()
    by_kind["share"] = by_kind.plays / plays.groupby("cc").size().reindex(by_kind.cc).values
    clip_kinds = by_kind[by_kind.share >= CLIP_HOST_MIN_SHARE]
    audio_per_kind = clip_kinds.groupby("kind").cc.nunique()
    primary_host = (plays.dropna(subset=["host"]).groupby(["cc", "host"]).size()
                         .rename("n").reset_index()
                         .sort_values(["cc", "n", "host"], ascending=[True, False, True])
                         .drop_duplicates("cc").set_index("cc").host)

    # ---------------------------------------------------------- build every path
    records = []
    for path, n in zip(vc.path, vc.events):
        rec = {"path": path, "events": int(n)}
        if path.endswith(".mp3"):
            cc = cc_id(path)
            host = primary_host.get(cc)
            rec.update(row_class="audio_clip", template=None, clip_id=cc, clip_page=host,
                       content_kind=T.content_kind.get(host) if host else None)
            hostcells = page_cells(host, host) if host else {}
            for f in FLAGS:
                if f in CLIP_FIXED:
                    v, p = CLIP_FIXED[f], "rule"
                elif path in beta.index and pd.notna(beta.at[path, f]):
                    v, p = float(beta.at[path, f]), "coded"
                elif cc in prof_clip.index and pd.notna(prof_clip.at[cc, f]):
                    v, p = float(prof_clip.at[cc, f]), "coded_same_clip"
                elif host and pd.notna(hostcells[f][0]):
                    v, p = hostcells[f][0], "clip_from_page"
                else:
                    v, p = np.nan, "unresolved"
                rec[f], rec[f + "__prov"] = v, p
            rec["Audio"], rec["Audio__prov"] = 0, "rule"
            rec["Audio_professor"] = (beta.at[path, "Audio"] if path in beta.index
                                      else prof_clip.Audio.get(cc, np.nan))
            records.append(rec)
            continue

        tpl = pc.template_of(path)
        if tpl is None:
            rec.update(row_class="unmapped", template=None)
            for f in FLAGS + ["Audio"]:
                rec[f], rec[f + "__prov"] = np.nan, "unresolved"
            records.append(rec)
            continue
        row = T.loc[tpl]
        rec.update(row_class=row.page_group, template=tpl, content_kind=row.content_kind)
        for f, (v, p) in page_cells(path, tpl).items():
            rec[f], rec[f + "__prov"] = v, p
        if row.page_group == "page_resource":
            rec["Audio"], rec["Audio__prov"] = np.nan, "page_resource"
        else:
            rec["Audio"] = int(audio_per_kind.get(row.content_kind, 0))
            rec["Audio__prov"] = "rule"
        rec["Audio_professor"] = beta.Audio.get(path, np.nan)
        records.append(rec)

    d = pd.DataFrame(records)
    front = ["path", "events", "row_class", "template", "content_kind", "clip_id", "clip_page"]
    for c in front:
        if c not in d:
            d[c] = None
    d = d[front + [c for f in FLAGS + ["Audio"] for c in (f, f + "__prov")] + ["Audio_professor"]]
    d.to_csv(pc.DICTIONARY_FILE, index=False)

    # ---------------------------------------------------------- checks
    # FAILS IF our template table contradicts a cell the professor coded on that
    # page: the table should only ever fill his blanks.
    clash = []
    for path, r in prof_page.iterrows():
        if r.template is None or r.template not in T.index:
            continue
        for f in FLAGS:
            ours = T.at[r.template, f]
            if pd.notna(r[f]) and ours not in ("", "context") and float(ours) != float(r[f]):
                clash.append((path, f, r[f], ours))
    clash = pd.DataFrame(clash, columns=["path", "flag", "professor", "template_table"])
    # FAILS IF a professor-coded cell did not reach the dictionary unchanged
    lost = 0
    dd = d.set_index("path")
    for path in beta.index.intersection(dd.index):
        for f in FLAGS:
            if pd.notna(beta.at[path, f]) and not (dd.at[path, f + "__prov"] == "coded"
                                                    and dd.at[path, f] == beta.at[path, f]):
                if not (dd.at[path, "row_class"] == "audio_clip" and f in CLIP_FIXED):
                    lost += 1

    # ---------------------------------------------------------- report
    ev_j = ev[[pc.PATH]].join(d.set_index("path"), on=pc.PATH)
    pv = ev_j[ev_j.row_class != "page_resource"]
    L = ["# Path dictionary — build report", "",
         f"Script `diagnostics/build_path_dictionary.py`. Pilot start {info['pilot_start'].date()} "
         f"(DEC-AA): {len(info['test_users'])} test user(s) and {info['test_events']:,} events "
         f"removed before anything else; {N:,} events and {ev[pc.USER].nunique():,} users remain.", "",
         "## 1. Where every cell comes from (share of pageviews)", "",
         "Page resources (language files, browser assets) are not pageviews and are excluded.",
         "`context` cells are filled per event in phase 2; `unresolved` stays NULL.", "",
         "| flag | professor | coded by us | navigation rule | clip from page | rule | context | unresolved |",
         "|---|---|---|---|---|---|---|---|"]
    for f in FLAGS + ["Audio"]:
        s = pv[f + "__prov"].value_counts(normalize=True)
        prof = sum(s.get(k, 0) for k in PROFESSOR)
        L.append(f"| `{f}` | {prof:.1%} | {s.get('coded_by_us', 0):.1%} | {s.get('navigation', 0):.1%} | "
                 f"{s.get('clip_from_page', 0):.1%} | {s.get('rule', 0):.1%} | {s.get('context', 0):.1%} | "
                 f"{s.get('unresolved', 0):.1%} |")
    L += ["", "## 2. Checks", "",
          f"- template table cells contradicting a professor-coded cell on the same page: "
          f"**{len(clash)}** ({'PASS' if clash.empty else 'FAIL'})",
          f"- professor-coded cells that did not reach the dictionary unchanged (excluding the "
          f"fixed clip rules below): **{lost}** ({'PASS' if lost == 0 else 'FAIL'})",
          f"- paths matching no template (NULL, for review): "
          f"**{int((d.row_class == 'unmapped').sum())}** paths, "
          f"{int(d.events[d.row_class == 'unmapped'].sum()):,} events",
          f"- audio clips with no page they are ever played from: "
          f"{int(d[(d.row_class == 'audio_clip') & d.clip_page.isna()].shape[0])}", "",
          "Deliberate departures from the professor's sheet (DEC-AE): `Audio` is the count of "
          "clips on a page, not his 0/1, and an audio clip row is Audio 0, Video 0, Download 0. "
          "His original Audio value is kept in `Audio_professor`.", "",
          "## 3. Audio clips per page kind (DEC-AE)", "",
          f"A clip belongs to a page kind holding at least {CLIP_HOST_MIN_SHARE:.0%} of its plays.", "",
          "| page kind | clips | clip ids |", "|---|---|---|"]
    for k, g in clip_kinds.groupby("kind"):
        ids = sorted(g.cc.astype(int))
        L.append(f"| {k} | {len(ids)} | {', '.join(f'CC_{i}' for i in ids)} |")
    (DIAG / "dictionary_inference_report.md").write_text("\n".join(L) + "\n")

    # ---------------------------------------------------------- review workbook
    tp = (d.groupby("template", dropna=True).events.sum())
    ours = pc.TEMPLATES.copy()
    ours.insert(3, "events", ours.template.map(tp).fillna(0).astype(int))
    ours.insert(4, "Audio_clips", ours.content_kind.map(audio_per_kind).fillna(0).astype(int))
    for f in FLAGS:                                 # mark which cells are the professor's
        pr = prof_page.groupby("template")[f].agg(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
        ours[f] = [f"{v} (professor)" if pd.notna(pr.get(t, np.nan)) else v
                   for t, v in zip(ours.template, ours[f])]
    unm = d[d.row_class == "unmapped"][["path", "events"]]
    clips = d[d.row_class == "audio_clip"][["path", "events", "clip_id", "clip_page"] +
                                           [c for f in FLAGS for c in (f, f + "__prov")]]
    with pd.ExcelWriter(OUT / "dictionary_review_for_professor.xlsx", engine="openpyxl") as xl:
        pd.DataFrame({"Read me": [
            "Every page template and how each flag was set. A value marked '(professor)' is yours; "
            "every other value was coded by us from the page's content, following your pattern, and "
            "is open to your correction in docs/page_template_coding.csv.",
            "'context' means the value is set per event: a borrower's document downloads by their "
            "type, and the Dashboard's CD flags once the borrower has a Closing Disclosure.",
            "Audio_clips is the number of distinct audio clips that play on that kind of page "
            "(Audio, as your coding_dictionary defines it).",
            "Unmapped_paths lists any path no template covers; those stay blank in the data."]}
        ).to_excel(xl, sheet_name="Read me", index=False)
        ours.to_excel(xl, sheet_name="Page_templates", index=False)
        clips.to_excel(xl, sheet_name="Audio_clips", index=False)
        clash.to_excel(xl, sheet_name="Conflicts_with_professor", index=False)
        unm.to_excel(xl, sheet_name="Unmapped_paths", index=False)
        from openpyxl.styles import Alignment, Font
        for ws in xl.sheets.values():                  # readable without resizing by hand
            ws.freeze_panes = "B2"
            for c in ws[1]:
                c.font = Font(bold=True)
            for col in ws.columns:
                letter = col[0].column_letter
                longest = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[letter].width = min(max(10, longest + 2), 60)
                for c in col[1:]:
                    c.alignment = Alignment(vertical="top", wrap_text=longest > 60)
        xl.sheets["Read me"].column_dimensions["A"].width = 120

    print("\n".join(L[:6]))
    print(d.row_class.value_counts().to_string())
    print(f"template conflicts {len(clash)}  lost professor cells {lost}  "
          f"unmapped paths {int((d.row_class == 'unmapped').sum())}")


if __name__ == "__main__":
    main()
