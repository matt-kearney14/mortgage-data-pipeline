#!/usr/bin/env python3
"""Coding_Dictionary_Coverage.xlsx — the professor's 42 coding_dictionary
variables against what the dataset delivers, colour-coded, with coverage figures
computed from this run's outputs.

Read-only. Run after phase2_user_dataset.py. Writes output/Coding_Dictionary_Coverage.xlsx.
"""
import textwrap
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "output"
pe = pd.read_parquet(R / "phase2_events.parquet"); P = pd.read_parquet(R / "user_level_dataset.parquet")
S = pd.read_parquet(R / "phase2_sessions.parquet")
cd = pd.read_excel(ROOT / "docs/Clickstream_path_frequencies_and_coding_scheme.xlsx",
                   sheet_name="coding_dictionary", header=None)
dic = cd.iloc[3:45, :3].reset_index(drop=True); dic.columns = ["var", "level", "desc"]
assert len(dic) == 42
NPV, NU = len(pe), len(P)
PROF = ["coded", "coded_same_page", "coded_same_clip"]


def flag(f, short=None):
    short = short or f
    pr = pe[f + "__prov"]
    known = pe[f].notna().mean(); prof = pr.isin(PROF).mean(); ours = (pr == "coded_by_us").mean()
    nav = (pr == "navigation").mean(); ev = known - prof - ours - nav
    ones = (pe[f] == 1).mean()
    users = int((P[f"pages_{short}"] > 0).sum()) if f"pages_{short}" in P else None
    s = (f"Known for {known:.1%} of the {NPV:,} pageviews: {prof:.0%} your coding, {ours:.0%} coded by us "
         f"where your sheet was blank, {nav:.0%} navigation pages (0), {max(ev, 0):.0%} set from the event "
         f"(download type, Dashboard, audio clips). Equals 1 on {ones:.1%} of pageviews")
    if known < 1:
        s += f"; unknown {1 - known:.2%} (downloads whose type cannot be determined)"
    if users is not None:
        s += f"; {users:,} of {NU:,} borrowers viewed at least one such page."
    return s + ("" if users is not None else ".")


def users(col, what="have a value"):
    n = int(P[col].notna().sum()); return f"{n:,} of {NU:,} borrowers ({n/NU:.1%}) {what}."


FULL, CLOSE, PART, NONE = "Full match", "Close match", "Partial / inferred", "Not available"
PAGE_FLAG_OURS = "{f} (pageview level); pages_{s}, time_{s}, unknown_{s} (borrower level)"
WHERE_PV = "User Data sheet (borrower level); phase2_events.parquet (pageview level)"
doc = pe.path.str.startswith("/Download/LoanDocument/")
src = pe.download_type_source[doc].value_counts()
typed = int(pe.download_type[doc].notna().sum())
ours_note = ("Pages where your sheet was blank were coded by us from the page's content, following your "
             "pattern; please confirm them (dictionary_review_for_professor.xlsx, Page_templates tab).")
DL = (f"{int(doc.sum()):,} document downloads; type known for {typed/doc.sum():.1%}: "
      f"{int(src.get('page', 0)):,} from the page they were clicked from, {int(src.get('doc match', 0)):,} "
      f"from the same document elsewhere, {int(src.get('number order', 0)):,} from document number order "
      f"(LE before CD); {int(doc.sum()) - typed:,} unknown.")

rows = {
 "Personalized": (FULL, None, flag("Personalized"),
    "Same 0/1 flag: the borrower's own loan (LE and CD pages, payment slideshows, Dashboard, their own downloads).",
    ours_note),
 "GeneralFinancial": (CLOSE, None, flag("GeneralFinancial"),
    "Same 0/1 flag. Following your pattern: money-skills pages (budgeting, credit, rates) are 1; own-loan and "
    "mortgage-process pages 0. Your two exceptions are kept.",
    "Budgeting FAQs and the rates infographic are 0 in your sheet while the rest of their topic is 1: intended?"),
 "MortgageRelated": (FULL, None, flag("MortgageRelated"), "Same 0/1 flag.", ours_note),
 "ProcessRelated": (PART, "ProcessRelated_provisional",
    flag("ProcessRelated_provisional", "ProcessRelated"),
    "Your values exactly as coded, labelled provisional because of the '????' in your sheet. Pages you did not "
    "code follow your coded page of the same kind. It is NOT Borrower + Lender process combined.",
    "Your definition: the union of Borrower and Lender process, a separate tag, or drop it?"),
 "BorrowerMortgageProcessRelated": (CLOSE, None, flag("BorrowerMortgageProcessRelated"),
    "Same 0/1 flag. Blank pages coded by us, following your coding of each page's own audio clips where you coded them; "
    "the CD page follows the LE page.", ours_note),
 "LenderMortgageProcessRelated": (CLOSE, None, flag("LenderMortgageProcessRelated"),
    "Same 0/1 flag, coded as for BorrowerMortgageProcessRelated.", ours_note),
 "LoanTermsRelated": (CLOSE, None, flag("LoanTermsRelated"),
    "Same 0/1 flag. You coded it only on audio clips; pages were coded by us using the Loan Estimate's own "
    "'Loan Terms' section (rate, amount, payment).", "Confirm the page coding."),
 "LoanEstimateRelated": (FULL, None, flag("LoanEstimateRelated"),
    "Same 0/1 flag. The Dashboard (which shows the LE) and LE downloads are 1.", ours_note),
 "CDRelated": (FULL, None, flag("CDRelated"),
    "Same 0/1 flag. CD downloads are 1; the Dashboard is 1 once the borrower has a CD.", ours_note),
 "LEDocument": (CLOSE, None, flag("LEDocument"),
    "1 when the borrower is looking at their own Loan Estimate: the LE page, the Dashboard, or a downloaded LE. " + DL,
    "A document-number → type table from the lender would replace the typing evidence for downloads."),
 "CDDocument": (CLOSE, None, flag("CDDocument"),
    "1 when the borrower is looking at their own Closing Disclosure: the CD page, a downloaded CD, or the Dashboard "
    "once the borrower has a CD (0 before: no CD exists yet).",
    "The same document-number table."),
 "Download": (FULL, None, flag("Download"),
    "Same 0/1 flag: a download, or a page offering one (as your 1 on the LE and CD pages).", ""),
 "LEDownload": (CLOSE, "LEDownload (pageview level); pages_LEDownload, time_LEDownload, downloads_typed_by_page / _doc_match / _number_order, downloads_type_unknown (borrower level)",
    f"{int((pe.LEDownload == 1).sum()):,} LE downloads; {int((P.pages_LEDownload > 0).sum()):,} borrowers have at least one.",
    "Your formula, Download AND LEDocument. " + DL + " Each borrower's count of each kind of evidence is a column.",
    "The document-number table would make every download measured."),
 "CDDownload": (CLOSE, "CDDownload (pageview level); pages_CDDownload, time_CDDownload, downloads_typed_by_*, downloads_type_unknown (borrower level)",
    f"{int((pe.CDDownload == 1).sum()):,} CD downloads; {int((P.pages_CDDownload > 0).sum()):,} borrowers have at least one.",
    "Your formula, Download AND CDDocument. A 'CD' by number order less than 3 days after the LE was sent is rejected "
    "(a CD cannot exist that soon). Typed CDs fall a median ~30 days after the LE was sent.",
    "The document-number table."),
 "AudioMp3": (FULL, "AudioMp3 (pageview level); pages_AudioMp3, time_AudioMp3 (borrower level)",
    f"100% of pageviews. {int(pe.AudioMp3.sum()):,} audio clips played by {int((P.audio_clips_clicked>0).sum()):,} borrowers.",
    "1 if the row is an mp3 file. time_AudioMp3 adds actual listening time.", ""),
 "Audio": (CLOSE, "Audio (pageview level)",
    f"100% of pageviews. {(pe.Audio > 0).mean():.1%} of pageviews are pages with audio clips.",
    "Built as you define it: the NUMBER of audio clips on the page (e.g. Application Documents Explained 23, "
    "Closing Documents Overview 28, Taxes & Insurance FAQs 6), measured from which page each clip is played from. "
    "An audio clip itself is 0.",
    "Eight pages you coded 1 have no clips played from them (e.g. the LE and CD pages); did you mean the video narration?"),
 "Video": (FULL, None, flag("Video"),
    "Same 0/1 flag: the /Module/ pages embed a video (confirmed on the demo site).",
    "You coded the VA fact sheet 1; no fact sheet has a video on the demo site. Intended?"),
 "English(Y/N)": (FULL, "English_YN (pageview level); english_webpages_visited, language_switches_to_english (borrower level)",
    "100% of pageviews.",
    "As you describe: starts from the account's language, then each page is in the language last loaded. The app loads "
    "/translations/en alone in English and both files at once in Spanish, so a load containing /es is Spanish.", ""),
 "Spanish (Y/N)": (FULL, "Spanish_YN (pageview level); spanish_webpages_visited, language_switches_to_spanish, used_language_toggle (borrower level)",
    f"100% of pageviews. {int(pe.Spanish_YN.sum()):,} Spanish pageviews by {int((P.spanish_webpages_visited>0).sum()):,} borrowers.",
    "Same rule as English; the two always add up to the total.", ""),
 "Goal_to_inform": (CLOSE, None, flag("Goal_to_inform"),
    "Same 0/1 flag. Every page you coded informs, so every content page is 1; navigation and downloads 0.", ours_note),
 "Goal_to_Advise": (CLOSE, None, flag("Goal_to_Advise"),
    "Same 0/1 flag: 1 where the page, or your coding of its clips, recommends action (credit, People & Process, budgeting FAQs).",
    ours_note),
 "Time spent on page": (FULL, "time_on_page (pageview level); pages_time_not_observable (borrower level)",
    f"{pe.time_on_page.notna().mean():.0%} of pageviews. The last page of each visit is blank: it has no next click, so its "
    "time cannot be known, and it is not estimated.",
    "Your formula, Timestamp(T+1) − Timestamp(T), in seconds, within the same visit.", ""),
 "Session start": (FULL, "session_start, session_start_ts (pageview level)", "100% of pageviews.",
    "Your formula. You did not specify the threshold; we use 30 minutes and report results at 5–240 minutes (session_timeout_sensitivity.csv).",
    "Confirm the 30-minute threshold."),
 "Session end": (FULL, "session_end, session_end_ts (pageview level)", "100% of pageviews.", "Your formula, same 30-minute threshold.", "Confirm the 30-minute threshold."),
 "Session time duration": (FULL, "session_duration (session level); total_session_time, mean_session_duration, median_session_duration (borrower level)",
    f"100% of {len(S):,} sessions.", "Last click minus first click in the visit, in seconds. A one-page visit is 0.", ""),
 "Inter-session elapsed time": (FULL, "inter_session_elapsed (session level); mean_inter_session_elapsed (borrower level)",
    f"{S.inter_session_elapsed.notna().mean():.0%} of sessions (a borrower's first visit has no previous one). " + users("mean_inter_session_elapsed", "have 2+ visits"),
    "You gave no formula; we use the end of one visit to the start of the next, in seconds.", "Confirm the definition."),
 "Number of webpages viewed in the session": (FULL, "pages_in_session (session level); mean_pages_in_session, single_page_sessions (borrower level)",
    f"100% of {len(S):,} sessions.", "Count of pageviews in the visit.", ""),
 "Number of webpages visited": (FULL, "webpages_visited", users("webpages_visited"),
    "Count of pageviews. Excludes automatic background requests (/favicon.ico, /cart.json) and the language files a page loads (/translations/en, /es), which are not pages the borrower opened. Two pre-pilot test accounts are removed.", ""),
 "Number of Spanish webpages visited": (FULL, "spanish_webpages_visited", users("spanish_webpages_visited"), "Sum of Spanish (Y/N).", ""),
 "Number of English webpages visited": (FULL, "english_webpages_visited", users("english_webpages_visited"), "Sum of English (Y/N).", ""),
 "Number of unique webpages visited": (FULL, "unique_webpages_visited", users("unique_webpages_visited"), "Distinct page addresses visited.", ""),
 "Number of webpages visited for each webpage characteristic": (FULL, "pages_<characteristic> (18 columns) + unknown_<characteristic>",
    users("pages_MortgageRelated"),
    "One column per 0/1 characteristic, counting confirmed 1s. Each has an unknown_ companion counting pages that could not be classified, so a low count is never a hidden 'unknown'.", ""),
 "Total Time of webpage visited for each webpage characteristic": (FULL, "time_<characteristic> (18 columns)",
    users("time_MortgageRelated"),
    "Seconds on pages with that characteristic. As your note says, audio time is credited to the page that played the clip. "
    "Columns overlap (a page can have several characteristics), so they do not add up to total time.", ""),
 "Number of audio clips clicked on": (FULL, "audio_clips_clicked", users("audio_clips_clicked"), "Sum of AudioMp3, as you define it.", ""),
 "Number of sessions": (FULL, "num_sessions", users("num_sessions"), "Distinct visits at the 30-minute threshold.", "Confirm the 30-minute threshold."),
 "Total # days account accessed": (FULL, "days_accessed", users("days_accessed"), "Distinct calendar dates with any activity.", ""),
 "Total time elapsed from activation to last account access": (FULL, "t_activation_to_last_access (+ first_access, last_access)",
    users("t_activation_to_last_access"), "Seconds from first to last Talkuments access. Activation = first access, as you define it.", ""),
 'Time from "Application_Date" to account activation': (FULL, "t_application_to_activation", users("t_application_to_activation"),
    f"Seconds from the application date to first access, from the borrower's earliest loan. {int((P.milestone_blank_reason == 'not in loan extract').sum())} borrowers have no loan in the loan file; milestone_blank_reason says why any timer is blank.",
    "The loan dates have no time of day, so results are accurate to the day."),
 'Time from account activation to "LE_TIL_Sent_Date"': (CLOSE, "t_activation_to_le_sent", users("t_activation_to_le_sent"),
    "Built as defined. In practice the LE is usually sent BEFORE the borrower first logs in, so this is negative for most borrowers. "
    "For 23% the two happen on the same day, and with no time of day on the LE date the order is unknowable.",
    "Consider reading it as 'LE sent → activation' instead."),
 'Time from "LE_TIL_Sent_Date" to LE page visits': (FULL, "t_le_sent_to_first_le_visit", users("t_le_sent_to_first_le_visit"),
    "Seconds from the LE being sent to the borrower's first Loan Estimate page visit on or after that date. Blank if they never visited one after the send date.", ""),
 'Time from account activation to "Lock_Date"': (FULL, "t_activation_to_lock", users("t_activation_to_lock"),
    "Seconds from first access to the rate lock. Blank when the loan was never locked.", ""),
 'Time from last account access to "Current_Status_Date"': (FULL, "t_last_access_to_current_status", users("t_last_access_to_current_status"),
    "Seconds from last access to the loan's final status date.", ""),
}
assert set(rows) == set(dic["var"].str.strip()), set(dic["var"].str.strip()) ^ set(rows)

FILLS = {FULL: "C6EFCE", CLOSE: "FFEB9C", PART: "F8CBAD", NONE: "FF9B9B"}
F = "Arial"; thin = Side(style="thin", color="BFBFBF"); B = Border(left=thin, right=thin, top=thin, bottom=thin)
wrap = Alignment(wrap_text=True, vertical="top")
wb = Workbook(); ws = wb.active; ws.title = "Variable Coverage"

ws["A1"] = "Clickstream coding dictionary — what the dataset delivers"; ws["A1"].font = Font(name=F, bold=True, size=14)
ws["A2"] = (f"Each row is one variable from your coding_dictionary tab, in its original order. Coverage figures are from "
            f"output/user_level_dataset.xlsx: {NU:,} borrowers, {NPV:,} pageviews.")
ws["A2"].font = Font(name=F, italic=True, size=10)
ws.merge_cells("A2:J2")
legend = [(FULL, "Built as you described"), (CLOSE, "Built, with a caveat or small change"),
          (PART, "Only partly possible, or inferred rather than measured"), (NONE, "Cannot be built from the current data")]
ws["B4"] = "Legend"; ws["B4"].font = Font(name=F, bold=True)
ws["E4"] = "Variables"; ws["E4"].font = Font(name=F, bold=True)
for i, (k, v) in enumerate(legend):
    r = 5 + i
    c = ws.cell(r, 2, k); c.fill = PatternFill("solid", fgColor=FILLS[k]); c.font = Font(name=F, bold=True); c.border = B
    ws.cell(r, 3, v).font = Font(name=F); ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
    c = ws.cell(r, 5, f'=COUNTIF($E$11:$E$52,B{r})'); c.font = Font(name=F); c.alignment = Alignment(horizontal="left")

H = ["#", "Original variable", "Level (your sheet)", "Your description", "How well we match",
     "Our variable(s)", "Where to find it", "What our variable is", "Data coverage", "What would close the gap"]
HR = 10
for j, h in enumerate(H, 1):
    c = ws.cell(HR, j, h); c.font = Font(name=F, bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3864")
    c.alignment = Alignment(wrap_text=True, vertical="center"); c.border = B
for i, rec in dic.iterrows():
    v = rec["var"].strip()
    st, ours, cov, what, gap = rows[v]
    if ours is None:
        ours = PAGE_FLAG_OURS.format(f=v, s=v)
    where = ("Loan timers: User Data sheet" if v.startswith("Time from") else
             "phase2_sessions.parquet / User Data sheet" if v.startswith(("Session time", "Inter-session", "Number of webpages viewed")) else
             "phase2_events.parquet (pageview level)" if v in ("Time spent on page", "Session start", "Session end") else
             "User Data sheet" if v.startswith(("Number", "Total")) else WHERE_PV)
    if v == "Audio": where = "phase2_events.parquet (pageview level)"
    vals = [i + 1, v, rec["level"], rec["desc"] if pd.notna(rec["desc"]) else "(no description given)", st, ours, where, what, cov, gap]
    r = HR + 1 + i
    for j, val in enumerate(vals, 1):
        c = ws.cell(r, j, val); c.font = Font(name=F, size=10, bold=(j in (2, 5))); c.alignment = wrap; c.border = B
    ws.cell(r, 5).fill = PatternFill("solid", fgColor=FILLS[st])
dv = DataValidation(type="list", formula1='"%s"' % ",".join(FILLS), allow_blank=False)
ws.add_data_validation(dv); dv.add(f"E{HR+1}:E{HR+42}")
WID = [5, 24, 14, 30, 14, 30, 22, 42, 38, 32]
for j, w in enumerate(WID, 1):
    ws.column_dimensions[get_column_letter(j)].width = w
def fit(sheet, first, last, widths, size=10):
    # Excel does not auto-size rows written by openpyxl: set each height from the
    # longest wrapped cell (approx. chars per line = width * 1.15 at 10pt Arial).
    for r in range(first, last + 1):
        lines = 1
        for j, w in enumerate(widths, 1):
            v = sheet.cell(r, j).value
            if v is None: continue
            cpl = max(1, int(w * 1.05))
            n = sum(max(1, len(textwrap.wrap(part, cpl, break_long_words=True))) for part in str(v).split("\n"))
            lines = max(lines, n)
        sheet.row_dimensions[r].height = lines * 13 + 8
fit(ws, HR + 1, HR + 42, WID)
ws.row_dimensions[HR].height = 30
ws.freeze_panes = ws.cell(HR + 1, 3)
ws.auto_filter.ref = f"A{HR}:J{HR+42}"
ws.sheet_view.zoomScale = 90

# ---- extras
ws2 = wb.create_sheet("Added beyond the dictionary")
extra = [
 ("pilot_bucket, pilot_bucket_label", "Pilot group: 1 = no Talkuments, 2 = Talkuments (English), 3 = Talkuments multilingual. Blank if the borrower's loans fall in different groups.", "pilot_bucket"),
 ("pilot_bucket_conflicting", "TRUE if the borrower's loans fall in more than one pilot group (excluded from group comparisons).", "pilot_bucket_conflicting"),
 ("loan_status, loan_originated", "Final outcome of the borrower's earliest loan, and a TRUE/FALSE for 'Loan Originated'.", "loan_status"),
 ("loans_in_pilot", "How many of the borrower's loans are in the loan file.", "loans_in_pilot"),
 ("hmda_loan_type, hmda_loan_purpose", "FHA / Conventional / VA …, and purchase / refinance …", "hmda_loan_type"),
 ("coapplicant, credit_score, interest_rate, apr", "Loan details from the lender (rate and APR exist only once the loan is locked).", "credit_score"),
 ("borrower_language", "Lender's language field for the loan. Best for loan-level work; reproduces the paper's Table 2 exactly.", "borrower_language"),
 ("language_preference", "Applicant's stated language. Best per-person language variable.", "language_preference"),
 ("provided_language", "Talkuments account language. NOT a background variable: it reflects which group the borrower was in.", "provided_language"),
 ("activated_talkument", "Lender's activation flag (blank = unknown, not 'no').", "activated_talkument"),
 ("expertise_level, account_enabled, state", "Account and applicant details.", "expertise_level"),
 ("language_switches_to_spanish, language_switches_to_english, used_language_toggle", "How often the borrower's site language changed.", "language_switches_to_spanish"),
 ("unknown_<characteristic>", "Pageviews where that characteristic could not be determined — read beside each pages_ column.", "unknown_MortgageRelated"),
 ("downloads_typed_by_page, _doc_match, _number_order, downloads_type_unknown", "How each of the borrower's downloads was typed, and how many could not be.", "downloads_type_unknown"),
 ("pages_time_not_observable", "Pageviews whose time cannot be observed (last page of each visit).", "pages_time_not_observable"),
 ("milestone_blank_reason", "Why any loan timer is blank (not in loan extract, never locked, ...).", "milestone_blank_reason"),
 ("pages_unattributable, time_unattributable, pct_pages_classified", "Pageviews and time with no characteristic known, and the share classified.", "pct_pages_classified"),
 ("total_time_observed, zero_dwell_pages", "Total measurable time; pageviews lasting 0 seconds (mostly redirects).", "total_time_observed"),
 ("first_access, last_access", "Timestamps of first and last Talkuments access.", "first_access"),
]
hdr = ["Our variable(s)", "What it is", "Coverage"]
for j, h in enumerate(hdr, 1):
    c = ws2.cell(1, j, h); c.font = Font(name=F, bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3864"); c.border = B
for i, (n, d, col) in enumerate(extra, 2):
    nn = int(P[col].notna().sum())
    for j, val in enumerate([n, d, f"{nn:,} of {NU:,} borrowers ({nn/NU:.1%})"], 1):
        c = ws2.cell(i, j, val); c.font = Font(name=F, size=10); c.alignment = wrap; c.border = B
W2 = [30, 50, 26]
for j, w in enumerate(W2, 1): ws2.column_dimensions[get_column_letter(j)].width = w
for j in range(1, 4): ws2.cell(1, j).alignment = Alignment(wrap_text=True, vertical="center")
fit(ws2, 2, len(extra) + 1, W2)
ws2.freeze_panes = "A2"
for sh in (ws, ws2):
    sh.page_setup.orientation = "landscape"; sh.page_setup.fitToWidth = 1; sh.page_setup.fitToHeight = 0
    sh.sheet_properties.pageSetUpPr.fitToPage = True
ws.print_title_rows = f"{HR}:{HR}"
wb.save(R / "Coding_Dictionary_Coverage.xlsx"); print("saved", R / "Coding_Dictionary_Coverage.xlsx")
