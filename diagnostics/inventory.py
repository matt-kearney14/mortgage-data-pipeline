"""
diagnostics/inventory.py

Read-only data inventory and diagnostic report for the mortgage clickstream
pipeline. Touches nothing under data/ or docs/ — it only reads. Writes a
markdown report and two small CSVs to diagnostics/output/ (gitignored).

Answers, with evidence:
  A. Sheet names / columns / dtypes / row counts for every file in data/
     and all four sheets of the coding-scheme workbook.
  B. Six specific structural questions (see README of this file / the
     approved plan for full context):
     1. coding_dictionary vs beta_coding — same headers / key column?
     2. Does talkument_useraccount.xlsx have an activated-language field?
     3. Does it have an activation date distinct from first pageview?
     4. Is eventdate timezone-aware? What zone are the milestone dates in
        talkument_loan_applicants.xlsx?
     5. What's in talkument_pilot_buckets.xlsx? Does it join on user_hash?
     6. user_hash overlap between the event log and each other file.

No sampling is performed anywhere in this script, so CLAUDE.md's
random-seed rule does not apply here — this omission is deliberate, not
an oversight.
"""

from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

# ==========================================
# MASTER VARIABLES — kept consistent with clickstream_processor.py
# ==========================================
USER_COL = "user_hash"
TIME_COL = "eventdate"
URL_COL = "path"
# ==========================================

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DOCS_DIR = REPO_ROOT / "docs"
OUTPUT_DIR = REPO_ROOT / "diagnostics" / "output"

EVENT_LOG_PATH = DATA_DIR / "talkument_userinteractions.xlsx"
USERACCOUNT_PATH = DATA_DIR / "talkument_useraccount.xlsx"
LOAN_APPLICANTS_PATH = DATA_DIR / "talkument_loan_applicants.xlsx"
PILOT_BUCKETS_PATH = DATA_DIR / "talkument_pilot_buckets.xlsx"
CODING_SCHEME_PATH = DOCS_DIR / "Clickstream_path_frequencies_and_coding_scheme.xlsx"

# Sheet names (named constants — no magic strings, no globbing, so the
# stray "~$Clickstream_path_frequencies_and_coding_scheme.xlsx" Office
# lock file is never touched).
SHEET_USER_USAGE = "user_usage"
SHEET_USERS = "users"
SHEET_LOAN_APPLICANTS = "loan_applicants"
SHEET_PILOT_RECORD = "pilot_record"
SHEET_ALPHABETICAL = "alphabetical"
SHEET_MOST_FREQUENT = "mostFrequent"
SHEET_CODING_DICTIONARY = "coding_dictionary"
SHEET_BETA_CODING = "beta_coding"

# Header row offsets (0-indexed, per pandas' `header=`). Two sheets in the
# coding-scheme workbook carry a title row (and coding_dictionary a blank
# row too) before the real header.
HEADER_DEFAULT = 0  # data files, beta_coding
HEADER_ROW2 = 1  # alphabetical, mostFrequent (title row on row 1)
HEADER_ROW3 = 2  # coding_dictionary (title + blank row before header)

CODING_SCHEME_KEY_COL = "CODING SCHEME"  # key column in beta_coding
DATA_VARIABLE_COL = "Data variable"  # key column in coding_dictionary
BETA_CODING_NON_FLAG_COLS = {CODING_SCHEME_KEY_COL, "NOTES", "LINDA NOTE"}

LOAN_NUMBER_COL_APPLICANTS = "loannumber"  # no underscore (loan_applicants)
LOAN_NUMBER_COL_PILOT = "loan_number"  # with underscore (pilot_buckets)

# Inherited from clickstream_processor.py, copied verbatim ONLY for the
# purpose of diffing it against the two coding-scheme sheets — never
# assumed valid by this script.
INHERITED_STATIC_VARS = [
    "Personalized", "Download", "LoanEstimateRelated", "LoanTermsRelated",
    "LenderMortgageProcessRelated", "GeneralFinancial", "Video", "CDRelated",
    "Goal_to_Advise", "ProcessRelated", "BorrowerMortgageProcessRelated",
    "Goal_to_inform", "MortgageRelated", "Audio",
]

EXPECTED_MILESTONE_COLS = [
    "Application_Date", "LE_TIL_Sent_Date", "Lock_Date", "Current_Status_Date",
]
MILESTONE_DATE_HINT_SUBSTRINGS = ["date", "application", "lock", "status"]

REPORT_PATH = OUTPUT_DIR / "inventory_report.md"
Q1_DIFF_CSV_PATH = OUTPUT_DIR / "coding_dictionary_vs_beta_coding_variable_diff.csv"
Q6_OVERLAP_CSV_PATH = OUTPUT_DIR / "user_hash_overlap_detail.csv"


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_event_log() -> pd.DataFrame:
    df = pd.read_excel(EVENT_LOG_PATH, sheet_name=SHEET_USER_USAGE, header=HEADER_DEFAULT)
    df[TIME_COL] = pd.to_datetime(df[TIME_COL])
    return df


def load_useraccount() -> pd.DataFrame:
    df = pd.read_excel(USERACCOUNT_PATH, sheet_name=SHEET_USERS, header=HEADER_DEFAULT)
    for col in ("first_login", "last_login"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def load_loan_applicants() -> pd.DataFrame:
    return pd.read_excel(LOAN_APPLICANTS_PATH, sheet_name=SHEET_LOAN_APPLICANTS, header=HEADER_DEFAULT)


def load_pilot_buckets() -> pd.DataFrame:
    return pd.read_excel(PILOT_BUCKETS_PATH, sheet_name=SHEET_PILOT_RECORD, header=HEADER_DEFAULT)


def load_coding_dictionary() -> pd.DataFrame:
    return pd.read_excel(CODING_SCHEME_PATH, sheet_name=SHEET_CODING_DICTIONARY, header=HEADER_ROW3)


def load_beta_coding() -> pd.DataFrame:
    return pd.read_excel(CODING_SCHEME_PATH, sheet_name=SHEET_BETA_CODING, header=HEADER_DEFAULT)


def load_alphabetical() -> pd.DataFrame:
    return pd.read_excel(CODING_SCHEME_PATH, sheet_name=SHEET_ALPHABETICAL, header=HEADER_ROW2)


def load_most_frequent() -> pd.DataFrame:
    return pd.read_excel(CODING_SCHEME_PATH, sheet_name=SHEET_MOST_FREQUENT, header=HEADER_ROW2)


# --------------------------------------------------------------------------
# Section A — general inventory
# --------------------------------------------------------------------------

def inventory_one_sheet(path: Path, sheet_name: str, header: int, actual_sheets: set) -> dict:
    if sheet_name not in actual_sheets:
        return {"sheet": sheet_name, "status": "MISSING — not found in workbook"}
    df = pd.read_excel(path, sheet_name=sheet_name, header=header)
    return {
        "sheet": sheet_name,
        "status": "OK",
        "row_count": len(df),
        "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "null_counts": df.isna().sum().to_dict(),
    }


def build_general_inventory() -> dict:
    inventory = {}

    data_file_specs = [
        (EVENT_LOG_PATH, [(SHEET_USER_USAGE, HEADER_DEFAULT)]),
        (USERACCOUNT_PATH, [(SHEET_USERS, HEADER_DEFAULT)]),
        (LOAN_APPLICANTS_PATH, [(SHEET_LOAN_APPLICANTS, HEADER_DEFAULT)]),
        (PILOT_BUCKETS_PATH, [(SHEET_PILOT_RECORD, HEADER_DEFAULT)]),
        (
            CODING_SCHEME_PATH,
            [
                (SHEET_ALPHABETICAL, HEADER_ROW2),
                (SHEET_MOST_FREQUENT, HEADER_ROW2),
                (SHEET_CODING_DICTIONARY, HEADER_ROW3),
                (SHEET_BETA_CODING, HEADER_DEFAULT),
            ],
        ),
    ]

    for path, sheet_specs in data_file_specs:
        if not path.exists():
            inventory[str(path)] = [{"status": f"FILE MISSING: {path}"}]
            continue
        actual_sheets = set(pd.ExcelFile(path).sheet_names)
        expected_sheets = {name for name, _ in sheet_specs}
        unexpected = actual_sheets - expected_sheets
        sheet_results = [
            inventory_one_sheet(path, name, header, actual_sheets) for name, header in sheet_specs
        ]
        if unexpected:
            sheet_results.append(
                {"status": f"NOTE — workbook also contains unexpected sheet(s) not inventoried: {sorted(unexpected)}"}
            )
        inventory[str(path)] = sheet_results

    return inventory


# --------------------------------------------------------------------------
# Section B — the six specific questions
# --------------------------------------------------------------------------

def q1_coding_dictionary_vs_beta_coding(coding_dict_df: pd.DataFrame, beta_df: pd.DataFrame) -> dict:
    cd_cols = list(coding_dict_df.columns)
    bc_cols = list(beta_df.columns)
    cd_has_key = CODING_SCHEME_KEY_COL in cd_cols
    bc_has_key = CODING_SCHEME_KEY_COL in bc_cols

    cd_variable_names = set(
        coding_dict_df[DATA_VARIABLE_COL].dropna().astype(str).str.strip()
    ) if DATA_VARIABLE_COL in cd_cols else set()
    bc_flag_columns = set(bc_cols) - BETA_CODING_NON_FLAG_COLS

    variables_in_both = cd_variable_names & bc_flag_columns
    only_in_coding_dict = cd_variable_names - bc_flag_columns
    only_in_beta_columns = bc_flag_columns - cd_variable_names

    static_vars_set = set(INHERITED_STATIC_VARS)
    static_vars_missing_from_coding_dict = static_vars_set - cd_variable_names
    static_vars_missing_from_beta_columns = static_vars_set - bc_flag_columns

    # Audio: true count, or effectively still binary in the source data?
    audio_result = None
    if "Audio" in bc_cols:
        audio_series = pd.to_numeric(beta_df["Audio"], errors="coerce")
        audio_result = {
            "value_counts": audio_series.value_counts(dropna=False).sort_index().to_dict(),
            "max_value": audio_series.max(),
            "any_value_over_1": bool((audio_series > 1).any()),
            "non_numeric_or_null_count": int(audio_series.isna().sum()),
        }

    return {
        "coding_dictionary_columns": cd_cols,
        "beta_coding_columns": bc_cols,
        "coding_dictionary_has_CODING_SCHEME_key": cd_has_key,
        "beta_coding_has_CODING_SCHEME_key": bc_has_key,
        "coding_dictionary_row_count": len(coding_dict_df),
        "beta_coding_row_count": len(beta_df),
        "variable_names_in_both": sorted(variables_in_both),
        "variable_names_only_in_coding_dictionary": sorted(only_in_coding_dict),
        "column_names_only_in_beta_coding": sorted(only_in_beta_columns),
        "inherited_static_vars_missing_from_coding_dictionary": sorted(static_vars_missing_from_coding_dict),
        "inherited_static_vars_missing_from_beta_coding_columns": sorted(static_vars_missing_from_beta_columns),
        "audio_column_check": audio_result,
    }


def q2_activated_language_field(useracct_df: pd.DataFrame) -> dict:
    col = "provided_language"
    present = col in useracct_df.columns
    total = len(useracct_df)
    if not present or total == 0:
        return {"present": present, "total_rows": total}
    non_null = int(useracct_df[col].notna().sum())
    coverage = non_null / total
    value_distribution = useracct_df[col].value_counts(dropna=False).to_dict()
    return {
        "present": present,
        "non_null_count": non_null,
        "total_rows": total,
        "coverage_rate": coverage,
        "value_distribution": value_distribution,
    }


def q3_activation_vs_first_pageview(useracct_df: pd.DataFrame, event_df: pd.DataFrame) -> dict:
    # CLAUDE.md sort key applied before any groupby, for methodological
    # consistency with the rest of the pipeline — the tiebreak does not
    # change a min() result, but the convention is followed anyway.
    sorted_events = event_df.sort_values(
        by=[USER_COL, TIME_COL], kind="mergesort"
    ).reset_index(drop=True)

    first_pageview = sorted_events.groupby(USER_COL)[TIME_COL].min().rename("first_pageview")

    if "first_login" not in useracct_df.columns:
        return {"error": "'first_login' column not present in talkument_useraccount.xlsx"}

    merged = useracct_df[[USER_COL, "first_login"]].merge(
        first_pageview, on=USER_COL, how="inner"
    )

    excluded_useraccount_only = len(useracct_df) - len(merged)
    excluded_event_log_only = len(first_pageview) - len(merged)

    diff_seconds = (merged["first_login"] - merged["first_pageview"]).dt.total_seconds()

    result = {
        "users_compared": len(merged),
        "useraccount_rows_excluded_no_event_log_match": excluded_useraccount_only,
        "event_log_users_excluded_no_useraccount_match": excluded_event_log_only,
    }
    if len(diff_seconds) > 0:
        desc = diff_seconds.describe()
        result.update(
            {
                "diff_seconds_describe": desc.to_dict(),
                "count_exactly_equal": int((diff_seconds == 0).sum()),
                "count_activation_before_first_pageview": int((diff_seconds < 0).sum()),
                "count_activation_after_first_pageview": int((diff_seconds > 0).sum()),
                "p05": float(diff_seconds.quantile(0.05)),
                "p95": float(diff_seconds.quantile(0.95)),
            }
        )
    return result


def q4_timezone_and_milestone_dates(event_df: pd.DataFrame, loan_applicants_df: pd.DataFrame, all_columns_by_file: dict) -> dict:
    ts = event_df[TIME_COL]
    is_tz_aware = isinstance(ts.dtype, pd.DatetimeTZDtype)
    tz = str(ts.dt.tz) if is_tz_aware else None

    actual_cols = list(loan_applicants_df.columns)
    missing = [c for c in EXPECTED_MILESTONE_COLS if c not in actual_cols]

    hints = {}
    for file_label, cols in all_columns_by_file.items():
        hits = [c for c in cols if any(s in str(c).lower() for s in MILESTONE_DATE_HINT_SUBSTRINGS)]
        if hits:
            hints[file_label] = hits

    return {
        "eventdate_dtype": str(ts.dtype),
        "eventdate_tz_aware": is_tz_aware,
        "eventdate_tz": tz,
        "loan_applicants_expected_milestone_columns_per_project_brief": EXPECTED_MILESTONE_COLS,
        "loan_applicants_actual_columns": actual_cols,
        "loan_applicants_actual_dtypes": {c: str(t) for c, t in loan_applicants_df.dtypes.items()},
        "missing_milestone_columns": missing,
        "possible_milestone_date_like_columns_found": hints,
    }


def _overlap(a: set, b: set) -> dict:
    return {
        "a_size": len(a),
        "b_size": len(b),
        "intersection": len(a & b),
        "a_only": len(a - b),
        "b_only": len(b - a),
    }


def q5_pilot_buckets_join(pilot_df: pd.DataFrame, loan_applicants_df: pd.DataFrame) -> dict:
    has_user_hash = USER_COL in pilot_df.columns

    bridge_result = None
    if LOAN_NUMBER_COL_PILOT in pilot_df.columns and LOAN_NUMBER_COL_APPLICANTS in loan_applicants_df.columns:
        pilot_keys_raw = pilot_df[LOAN_NUMBER_COL_PILOT].dropna()
        applicant_keys_raw = loan_applicants_df[LOAN_NUMBER_COL_APPLICANTS].dropna()

        raw_overlap = _overlap(set(pilot_keys_raw), set(applicant_keys_raw))
        norm_overlap = _overlap(
            set(pilot_keys_raw.astype(str).str.strip().str.lower()),
            set(applicant_keys_raw.astype(str).str.strip().str.lower()),
        )
        bridge_result = {
            "dtype_pilot_loan_number": str(pilot_df[LOAN_NUMBER_COL_PILOT].dtype),
            "dtype_applicants_loannumber": str(loan_applicants_df[LOAN_NUMBER_COL_APPLICANTS].dtype),
            "raw_overlap": raw_overlap,
            "normalized_overlap_trimmed_lowercased": norm_overlap,
        }

    return {
        "pilot_buckets_columns": list(pilot_df.columns),
        "pilot_buckets_row_count": len(pilot_df),
        "bucket_value_distribution": pilot_df["bucket"].value_counts(dropna=False).to_dict()
        if "bucket" in pilot_df.columns else None,
        "has_user_hash_column": has_user_hash,
        "direct_user_hash_join_possible": has_user_hash,
        "loan_number_naming_mismatch": (
            f"pilot_buckets uses '{LOAN_NUMBER_COL_PILOT}', "
            f"loan_applicants uses '{LOAN_NUMBER_COL_APPLICANTS}'"
        ),
        "bridge_via_loan_applicants": bridge_result,
    }


def q6_user_hash_overlap(
    event_df: pd.DataFrame,
    useracct_df: pd.DataFrame,
    loan_applicants_df: pd.DataFrame,
    pilot_df: pd.DataFrame,
) -> dict:
    event_users = set(event_df[USER_COL].dropna())
    results = {"event_log_unique_user_hash_count": len(event_users)}

    for label, other_df in [("useraccount", useracct_df), ("loan_applicants", loan_applicants_df)]:
        if USER_COL not in other_df.columns:
            results[label] = {"has_user_hash_column": False, "note": "cannot compute overlap"}
            continue
        other_users_all = other_df[USER_COL].dropna()
        other_users = set(other_users_all)
        intersection = event_users & other_users
        event_only = event_users - other_users
        other_only = other_users - event_users
        events_missing = event_df[~event_df[USER_COL].isin(other_users)]
        entry = {
            "has_user_hash_column": True,
            "row_count": len(other_df),
            "unique_user_hash_count": len(other_users),
            "duplicate_user_hash_rows": int(len(other_df) - other_users_all.nunique())
            if len(other_df) else 0,
            "intersection_count": len(intersection),
            "event_log_only_count": len(event_only),
            "other_file_only_count": len(other_only),
            "event_rows_with_user_missing_from_other_file": len(events_missing),
            "event_rows_with_user_missing_from_other_file_pct": (
                len(events_missing) / len(event_df) if len(event_df) else float("nan")
            ),
        }
        results[label] = entry

    results["pilot_buckets"] = {
        "has_user_hash_column": USER_COL in pilot_df.columns,
        "note": (
            "talkument_pilot_buckets.xlsx has no user_hash column; "
            "overlap/missing-events computation is not possible on this key. "
            "See Q5 for the indirect loan_number/loannumber bridge."
        ),
    }
    return results


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def _fmt_val(v):
    if isinstance(v, float):
        return f"{v:,.4f}" if abs(v) < 1e6 else f"{v:,.2f}"
    return str(v)


def _dict_table(d: dict) -> str:
    lines = ["| key | value |", "|---|---|"]
    for k, v in d.items():
        lines.append(f"| {k} | {_fmt_val(v)} |")
    return "\n".join(lines)


def _markdown_table(df: pd.DataFrame) -> str:
    """Plain pipe-table builder — does not assume `tabulate` is installed."""
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |"]
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for _, row in df.iterrows():
        cells = [str(row[c]).replace("\n", " ").replace("|", "\\|") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_markdown_report(
    general_inventory: dict,
    q1: dict,
    q2: dict,
    q3: dict,
    q4: dict,
    q5: dict,
    q6: dict,
    coding_dict_df: pd.DataFrame,
    out_path: Path,
):
    lines = []
    lines.append("# Data Inventory & Diagnostic Report")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("Script: `diagnostics/inventory.py` (read-only; no source files modified)")
    lines.append("")
    lines.append(
        "Scope note: Section A inventories all four sheets of the coding-scheme "
        "workbook (`alphabetical`, `mostFrequent`, `coding_dictionary`, `beta_coding`), "
        "not only the two named in the specific questions below, since it is free "
        "once the workbook is open and matches the 'every file' scope of the request. "
        "Section B's Q1 concerns only `coding_dictionary` and `beta_coding`."
    )
    lines.append("")

    # --- Section A ---
    lines.append("## A. General Inventory")
    for file_path, sheets in general_inventory.items():
        lines.append(f"### `{file_path}`")
        for sheet in sheets:
            if sheet.get("status", "").startswith("NOTE") or sheet.get("status", "").startswith("FILE MISSING"):
                lines.append(f"- {sheet['status']}")
                continue
            lines.append(f"#### sheet: `{sheet['sheet']}`")
            if sheet["status"] != "OK":
                lines.append(f"- **{sheet['status']}**")
                continue
            lines.append(f"- row_count: **{sheet['row_count']}**")
            lines.append(f"- columns: {sheet['columns']}")
            lines.append("- dtypes / null_counts:")
            lines.append("")
            lines.append("| column | dtype | null_count |")
            lines.append("|---|---|---|")
            for col in sheet["columns"]:
                lines.append(
                    f"| {col} | {sheet['dtypes'].get(col)} | {sheet['null_counts'].get(col)} |"
                )
            lines.append("")

    # --- Section B ---
    lines.append("## B. Diagnostic Questions")

    lines.append("### Q1. `coding_dictionary` vs `beta_coding` — structural comparison")
    lines.append(
        f"`coding_dictionary` has `CODING SCHEME` column: **{q1['coding_dictionary_has_CODING_SCHEME_key']}**. "
        f"`beta_coding` has `CODING SCHEME` column: **{q1['beta_coding_has_CODING_SCHEME_key']}**."
    )
    if not q1["coding_dictionary_has_CODING_SCHEME_key"]:
        lines.append(
            "> `coding_dictionary` has no `CODING SCHEME` column and is not a per-path "
            "lookup table — it is a variable glossary (columns: `Data variable`, "
            "`Hard coded or calculated?`, `Description`), one row per variable name. "
            "`beta_coding` is the per-path binary-flag matrix keyed on `CODING SCHEME`. "
            "These are not parallel tables. `clickstream_processor.py` currently reads "
            "`beta_coding`, which CLAUDE.md flags as the wrong sheet "
            "(`coding_dictionary` is stated as authoritative)."
        )
    lines.append("")
    lines.append(f"- `coding_dictionary` columns: {q1['coding_dictionary_columns']} ({q1['coding_dictionary_row_count']} rows)")
    lines.append(f"- `beta_coding` columns: {q1['beta_coding_columns']} ({q1['beta_coding_row_count']} rows)")
    lines.append(f"- Variable names present in both: {q1['variable_names_in_both']}")
    lines.append(f"- Variable names only in `coding_dictionary`'s `Data variable` column: {q1['variable_names_only_in_coding_dictionary']}")
    lines.append(f"- Column names only in `beta_coding` (not matched by a `coding_dictionary` variable row): {q1['column_names_only_in_beta_coding']}")
    lines.append(f"- Inherited `static_vars` (from `clickstream_processor.py`) missing from `coding_dictionary`: {q1['inherited_static_vars_missing_from_coding_dictionary']}")
    lines.append(f"- Inherited `static_vars` missing from `beta_coding` columns: {q1['inherited_static_vars_missing_from_beta_coding_columns']}")
    lines.append("")

    if q1["audio_column_check"] is not None:
        ac = q1["audio_column_check"]
        verdict = "TRUE COUNT (values >1 present)" if ac["any_value_over_1"] else "effectively binary — all values are 0/1"
        lines.append(f"**`beta_coding`'s `Audio` column: {verdict}.**")
        lines.append(f"- value_counts: {ac['value_counts']}")
        lines.append(f"- max value: {ac['max_value']}")
        lines.append(f"- non-numeric/null cells: {ac['non_numeric_or_null_count']}")
        lines.append("")

    lines.append("#### `coding_dictionary` — full contents (all rows, professor's own definitions, verbatim)")
    lines.append("")
    lines.append(_markdown_table(coding_dict_df))
    lines.append("")

    lines.append("### Q2. Activated-language field (`talkument_useraccount.xlsx`)")
    lines.append(_dict_table({k: v for k, v in q2.items() if k != "value_distribution"}))
    if "value_distribution" in q2:
        lines.append("")
        lines.append(f"Value distribution: {q2['value_distribution']}")
    lines.append("")

    lines.append("### Q3. Activation date (`first_login`) vs. first pageview")
    lines.append(
        "Sort key `user_hash`, `eventdate` asc, stable row-order tiebreak applied "
        "before computing first pageview per user, per CLAUDE.md convention "
        "(does not change a `min()` result, kept for methodological consistency)."
    )
    lines.append("")
    lines.append(_dict_table(q3))
    lines.append("")

    lines.append("### Q4. Timezone of `eventdate`; milestone-date columns in `loan_applicants`")
    lines.append(_dict_table({k: v for k, v in q4.items() if k not in ("loan_applicants_actual_dtypes",)}))
    lines.append("")
    if q4["missing_milestone_columns"]:
        lines.append(
            f"> `docs/Project_Brief.md` claims `talkument_loan_applicants.xlsx` contains "
            f"{EXPECTED_MILESTONE_COLS}. **None of these columns exist in the file as loaded.** "
            f"Actual columns: {q4['loan_applicants_actual_columns']}. This blocks variables "
            f"37–42 (milestone timers) as currently specified; a human must locate the "
            f"correct source before that work proceeds. The timezone half of this question "
            f"is therefore unanswerable for this file — not guessed."
        )
    lines.append("")
    lines.append("Hint only — not a conclusion (substring scan for milestone-date-like column names across all files):")
    lines.append(f"{q4['possible_milestone_date_like_columns_found'] or '(none found)'}")
    lines.append("")

    lines.append("### Q5. `talkument_pilot_buckets.xlsx` contents and `user_hash` joinability")
    lines.append(_dict_table({k: v for k, v in q5.items() if k != "bridge_via_loan_applicants"}))
    lines.append("")
    if not q5["has_user_hash_column"]:
        lines.append(
            "> `talkument_pilot_buckets.xlsx` has no `user_hash` column; a direct join "
            "between the event log and pilot buckets on `user_hash` is structurally "
            "impossible. A join is only reachable by bridging through "
            "`talkument_loan_applicants.xlsx` on `loan_number` (pilot_buckets) / "
            "`loannumber` (loan_applicants) — note the naming inconsistency. The overlap "
            "below is reported as a secondary, indirect finding; it is not evidence the "
            "bridge is semantically correct (same loan, same time period, etc.), only "
            "that the value sets intersect."
        )
    if q5["bridge_via_loan_applicants"]:
        lines.append("")
        lines.append("Bridge via `loan_number` / `loannumber`:")
        lines.append(_dict_table(q5["bridge_via_loan_applicants"]))
    lines.append("")

    lines.append("### Q6. `user_hash` overlap: event log vs. each other file")
    lines.append(f"Event log unique `user_hash` count: **{q6['event_log_unique_user_hash_count']}**")
    for label in ("useraccount", "loan_applicants", "pilot_buckets"):
        lines.append("")
        lines.append(f"#### {label}")
        lines.append(_dict_table(q6[label]))
    lines.append("")

    lines.append("## Summary of Findings Requiring Human Decision")
    lines.append(
        "- `talkument_loan_applicants.xlsx` has **no milestone-date columns** "
        "(`Application_Date`, `LE_TIL_Sent_Date`, `Lock_Date`, `Current_Status_Date`) "
        "despite `docs/Project_Brief.md`'s claim — vars 37–42 are blocked until a human "
        "locates the real source."
    )
    lines.append(
        "- `talkument_pilot_buckets.xlsx` cannot join to the event log on `user_hash` "
        "directly — it has no such column. Only an indirect `loan_number`/`loannumber` "
        "bridge through `loan_applicants` is possible, and that bridge's semantic "
        "validity has not been verified here."
    )
    lines.append(
        "- `coding_dictionary` and `beta_coding` are structurally different tables — "
        "a 45-ish-row variable glossary vs. a per-path binary-flag matrix — with "
        "`coding_dictionary` lacking any `CODING SCHEME` key or per-URL rows at all."
    )
    lines.append(
        "- **CLAUDE.md's Classification-dictionary section is itself wrong given this "
        "structure.** It calls `coding_dictionary` \"authoritative\" and implies "
        "`clickstream_processor.py` should read it in place of `beta_coding`, but "
        "`coding_dictionary`'s structure (glossary, no `CODING SCHEME` key, no per-URL "
        "rows) means it cannot structurally serve as a drop-in replacement for "
        "`beta_coding` in the pipeline's merge. This is a documentation defect for a "
        "human to fix; this script does not edit CLAUDE.md."
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_q1_diff_csv(q1: dict, out_path: Path):
    rows = []
    all_vars = (
        set(q1["variable_names_in_both"])
        | set(q1["variable_names_only_in_coding_dictionary"])
        | set(q1["column_names_only_in_beta_coding"])
        | set(INHERITED_STATIC_VARS)
    )
    in_both = set(q1["variable_names_in_both"])
    in_cd_only = set(q1["variable_names_only_in_coding_dictionary"])
    in_bc_only = set(q1["column_names_only_in_beta_coding"])
    static_vars_set = set(INHERITED_STATIC_VARS)
    for var in sorted(all_vars):
        rows.append(
            {
                "variable_name": var,
                "in_coding_dictionary": var in in_both or var in in_cd_only,
                "in_beta_coding_columns": var in in_both or var in in_bc_only,
                "in_inherited_static_vars": var in static_vars_set,
            }
        )
    pd.DataFrame(rows).to_csv(out_path, index=False)


def write_q6_overlap_csv(q6: dict, out_path: Path):
    rows = []
    for label in ("useraccount", "loan_applicants", "pilot_buckets"):
        entry = q6[label]
        row = {"file": label}
        row.update(
            {
                "has_user_hash_column": entry.get("has_user_hash_column"),
                "unique_user_hash_count": entry.get("unique_user_hash_count"),
                "intersection_with_event_log": entry.get("intersection_count"),
                "event_log_only": entry.get("event_log_only_count"),
                "other_file_only": entry.get("other_file_only_count"),
                "event_log_rows_missing_from_other_file": entry.get(
                    "event_rows_with_user_missing_from_other_file"
                ),
            }
        )
        rows.append(row)
    pd.DataFrame(rows).to_csv(out_path, index=False)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    event_df = load_event_log()
    useracct_df = load_useraccount()
    loan_applicants_df = load_loan_applicants()
    pilot_df = load_pilot_buckets()
    coding_dict_df = load_coding_dictionary()
    beta_df = load_beta_coding()

    general_inventory = build_general_inventory()

    all_columns_by_file = {
        "talkument_userinteractions.xlsx": list(event_df.columns),
        "talkument_useraccount.xlsx": list(useracct_df.columns),
        "talkument_loan_applicants.xlsx": list(loan_applicants_df.columns),
        "talkument_pilot_buckets.xlsx": list(pilot_df.columns),
        "coding_dictionary": list(coding_dict_df.columns),
        "beta_coding": list(beta_df.columns),
    }

    q1 = q1_coding_dictionary_vs_beta_coding(coding_dict_df, beta_df)
    q2 = q2_activated_language_field(useracct_df)
    q3 = q3_activation_vs_first_pageview(useracct_df, event_df)
    q4 = q4_timezone_and_milestone_dates(event_df, loan_applicants_df, all_columns_by_file)
    q5 = q5_pilot_buckets_join(pilot_df, loan_applicants_df)
    q6 = q6_user_hash_overlap(event_df, useracct_df, loan_applicants_df, pilot_df)

    write_markdown_report(general_inventory, q1, q2, q3, q4, q5, q6, coding_dict_df, REPORT_PATH)
    write_q1_diff_csv(q1, Q1_DIFF_CSV_PATH)
    write_q6_overlap_csv(q6, Q6_OVERLAP_CSV_PATH)

    # Console summary
    print("=== diagnostics/inventory.py — summary ===")
    print(f"Event log rows: {len(event_df):,}")
    print(f"coding_dictionary has CODING SCHEME key: {q1['coding_dictionary_has_CODING_SCHEME_key']}")
    print(f"beta_coding has CODING SCHEME key: {q1['beta_coding_has_CODING_SCHEME_key']}")
    if q1["audio_column_check"] is not None:
        print(f"beta_coding Audio any value >1: {q1['audio_column_check']['any_value_over_1']}")
    print(f"useraccount provided_language coverage: {q2.get('coverage_rate')}")
    print(f"eventdate tz-aware: {q4['eventdate_tz_aware']} (tz={q4['eventdate_tz']})")
    print(f"loan_applicants missing milestone columns: {q4['missing_milestone_columns']}")
    print(f"pilot_buckets has user_hash: {q5['has_user_hash_column']}")
    print(f"user_hash overlap (useraccount): {q6['useraccount']}")
    print(f"user_hash overlap (loan_applicants): {q6['loan_applicants']}")
    print(f"Report written to: {REPORT_PATH}")
    print(f"CSV written to: {Q1_DIFF_CSV_PATH}")
    print(f"CSV written to: {Q6_OVERLAP_CSV_PATH}")


if __name__ == "__main__":
    main()
