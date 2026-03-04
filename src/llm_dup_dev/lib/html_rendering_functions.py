# Imports

# Standard library

import sys
from dataclasses import asdict
import numpy as np
import math

# Ensure local packages are importable (notebook-friendly)
sys.path.append("..")

# Third-party
import pandas as pd

from html import escape as _escape

# Local packages
from lib.constants import TARGET_DB
from lib.data_extraction import (

    Report,
)

from lib.data_extraction_demographics import (
    fetch_drug_demographics_summary, 
    fetch_reaction_demographics_summary, 
fetch_drug_reaction_demographics_summary)


from lib.util import is_missing


def clean_age_column(df):
    """
    Replace values in the 'age' column greater than 150 with NaN.

    Parameters:
        df (pd.DataFrame): Input DataFrame containing an 'age' column.

    Returns:
        pd.DataFrame: Updated DataFrame with invalid ages replaced by NaN.
    """
    if "age" not in df.columns:
        raise ValueError("DataFrame must contain an 'age' column.")

    df = df.copy()  # avoid modifying original DataFrame
    df.loc[df["age"] > 150, "age"] = np.nan
    return df


# ----------------------------------------------------------------------------------------------


# def create_reports_overview(report1: Report, report2: Report):
#     """
#     Return three HTML snippets:
#       1) drug_reaction_summary_html  -> demographics for reports sharing BOTH common drugs & common AEs
#       2) drug_summary_html           -> demographics for reports sharing common drugs
#       3) reaction_summary_html       -> demographics for reports sharing common AEs
#     """

#     # --- Common drug names ---
#     drug_name_rep1 = {drug.drug_name for drug in report1.drugs}
#     drug_name_rep2 = {drug.drug_name for drug in report2.drugs}
#     common_drug_names = drug_name_rep1 & drug_name_rep2

#     if not common_drug_names:
#         drug_summary_html = (
#             "<section><h3>Common Drugs</h3>"
#             "<p><em>No common drugs found in the two reports.</em></p></section>"
#         )
#         drug_report_ids = set()
#     else:
#         print(f"fetching common drug reports for: {common_drug_names}")
#         common_drug_reports = fetch_report_counts_for_common_drugs(
#             common_drug_names, TARGET_DB
#         )
#         print( " function retunned length: ", len(common_drug_reports))
#         # Ensure we have a set of IDs to intersect later (handles both DF/Series/iterables)
#         drug_report_ids = set(common_drug_reports["ReportID"])
#         print(f"fetched demographics for common drug reports for {len(drug_report_ids)} reports")
#         drug_demo = fetch_demographic_for_give_reports_ids_via_temp_table(
#             common_drug_reports, TARGET_DB
#         )
#         print(len(drug_demo), "demographic records fetched for common drug reports")
#         drug_demo = clean_age_column(drug_demo)  # Clean the age column
#         drug_summary_html = summary_of_demographics_html(
#             drug_demo, common_names=common_drug_names, title_heading="Common Drugs"
#         )

#     # --- Common adverse event (reaction) names ---
#     Reaction_name_rep1 = {React.pt_name for React in report1.reactions}
#     Reaction_name_rep2 = {React.pt_name for React in report2.reactions}
#     common_Reaction_names = Reaction_name_rep1 & Reaction_name_rep2

#     if not common_Reaction_names:
#         reaction_summary_html = (
#             "<section><h3>Common Adverse Events</h3>"
#             "<p><em>No common adverse events found in the two reports.</em></p></section>"
#         )
#         react_report_ids = set()
#     else:
#         print(f"fetching common reaction reports for: {common_Reaction_names}")
#         common_react_reports = fetch_report_counts_for_common_reactions(
#             common_Reaction_names, TARGET_DB
#         )
#         print(f"fetching demographics for common reaction reports")
#         react_report_ids = set(common_react_reports["ReportID"])
#         reaction_demo = fetch_demographic_for_give_reports_ids_via_temp_table(
#             common_react_reports, TARGET_DB
#         )
#         reaction_demo = clean_age_column(reaction_demo)  # Clean the age column
#         reaction_summary_html = summary_of_demographics_html(
#             reaction_demo,
#             common_names=common_Reaction_names,
#             title_heading="Common Adverse Events",
#         )

#     # --- Common drug–AE pairs (intersection of report IDs) ---
#     both_ids = drug_report_ids & react_report_ids
#     if not both_ids:
#         drug_reaction_summary_html = (
#             "<section><h3>Common Drug–Adverse Event Pairs</h3>"
#             "<p><em>No common drug–adverse event pairs found in the two reports.</em></p></section>"
#         )
#     else:
#         # If your temp-table helper accepts an iterable of IDs directly, pass both_ids;
#         # otherwise wrap as the expected structure (e.g., DataFrame/Series with 'ReportID').
#         drug_reaction_demo = fetch_demographic_for_give_reports_ids_via_temp_table(
#             both_ids, TARGET_DB
#         )
#         drug_reaction_demo = clean_age_column(
#             drug_reaction_demo
#         )  # Clean the age column
#         # both_names = common_drug_names.union(common_Reaction_names)
#         both_names = list(common_drug_names) + list(common_Reaction_names)

#         drug_reaction_summary_html = summary_of_demographics_html(
#             drug_reaction_demo,
#             common_names=both_names,
#             title_heading="Common Drug–Adverse Event",
#         )

#     # Return the three pieces so you can place them independently
#     return drug_reaction_summary_html, drug_summary_html, reaction_summary_html


# ======================================================================
# HTML RENDERER  (unchanged from previous refactor)
# ======================================================================

def summary_of_demographics_html_from_summary(
    summary_df: pd.DataFrame,
    common_names: list[str],
    title_heading: str,
    reporter_countries: tuple[str, ...] = (),   # highlight these in the countries table
) -> str:
    names_str = ", ".join(common_names) if common_names else "Selection"

    if summary_df is None or summary_df.empty:
        return (
            f"<section style='margin-top:1.5em'>"
            f"  <h3>Demographics for {title_heading} ({_escape(names_str)})</h3>"
            f"  <p><em>No records available.</em></p>"
            f"</section>"
        )

    def _get(section, metric):
        row = summary_df[
            (summary_df["section"] == section) & (summary_df["metric"] == metric)
        ]
        return None if row.empty else row.iloc[0]["value"]

    def fmt(x):
        return "NA" if x is None or (isinstance(x, float) and np.isnan(x)) else str(int(x))

    def _render_overview(section_prefix):
        total_reports = _get(section_prefix, "total_reports")
        avg_age       = _get(section_prefix, "avg_age")
        q1            = _get(section_prefix, "q1_age")
        q3            = _get(section_prefix, "q3_age")
        return total_reports, avg_age, q1, q3

    def _render_sex(section_prefix):
        sex_df = summary_df[summary_df["section"] == section_prefix].copy()
        def sex_pct(label):
            row = sex_df[sex_df["metric"].str.title() == label]
            return int(row.iloc[0]["pct"]) if not row.empty else 0
        return sex_pct("Male"), sex_pct("Female"), sex_pct("Unknown")

    total_reports, avg_age, q1, q3 = _render_overview("overview")
    pct_male, pct_female, pct_unknown = _render_sex("sex")

    countries_df = summary_df[summary_df["section"] == "country"].sort_values("metric")
    years_df     = summary_df[summary_df["section"] == "year"].sort_values("metric")

    html = []
    html.append("<section style='margin-top:1.5em'>")
    html.append(f"<h3>Demographics for {title_heading} ({_escape(names_str)})</h3>")

    html.append("  <table border='1' cellspacing='0' cellpadding='5' style='margin-top:0.5em'>")
    html.append("    <thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>")
    html.append(f"      <tr><td>Total Reports</td><td>{_escape(fmt(total_reports))}</td></tr>")
    html.append(
        f"      <tr><td>Average Age (Q1–Q3)</td>"
        f"<td>{_escape(fmt(avg_age))} (Q1={_escape(fmt(q1))}, Q3={_escape(fmt(q3))})</td></tr>"
    )
    html.append("    </tbody></table>")

    html.append("  <h4 style='margin-top:1em'>Gender Distribution</h4>")
    html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
    html.append("    <thead><tr><th>Male</th><th>Female</th><th>Unknown</th></tr></thead>")
    html.append(
        f"    <tbody><tr><td>{pct_male}%</td><td>{pct_female}%</td><td>{pct_unknown}%</td></tr></tbody>"
    )
    html.append("  </table>")

    html.append("  <h4 style='margin-top:1em'>Top Reporting Countries</h4>")
    if countries_df.empty:
        html.append("  <p><em>No country data.</em></p>")
    else:
        # normalise reporter countries for comparison
        rc_set = {c for c in reporter_countries if c}
        # split into top-3 and reporter-country-only rows
        all_countries = countries_df.copy()
        top3 = all_countries[all_countries["metric"].astype(int) <= 3]
        rc_rows = all_countries[
            all_countries["label"].isin(rc_set) &
            (all_countries["metric"].astype(int) > 3)
        ] if rc_set else all_countries.iloc[0:0]

        html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
        html.append("    <thead><tr><th>Country</th><th>Share of records</th><th>Count</th></tr></thead><tbody>")

        # render top 3
        for _, row in top3.iterrows():
            country_name = str(row['label'])
            style = " style='background:#fffbdd'" if country_name in rc_set else ""
            tag = f" <small><em>(reporter country)</em></small>" if country_name in rc_set else ""
            html.append(
                f"      <tr{style}><td>{_escape(country_name)}{tag}</td>"
                f"<td>{int(row['pct'])}%</td><td>{int(row['value'])}</td></tr>"
            )

        # render reporter countries outside top 3 with a separator row
        if not rc_rows.empty:
            html.append(
                "      <tr><td colspan='3' style='background:#f0f0f0; font-size:0.8em; "                "padding:2px 5px'><em>Reporter countries (outside top 3)</em></td></tr>"
            )
            for _, row in rc_rows.iterrows():
                country_name = str(row['label'])
                html.append(
                    f"      <tr style='background:#fffbdd'><td>{_escape(country_name)}"                    f" <small><em>(reporter country)</em></small></td>"
                    f"<td>{int(row['pct'])}%</td><td>{int(row['value'])}</td></tr>"
                )

        html.append("    </tbody></table>")

    html.append("  <h4 style='margin-top:1em'>Top Years</h4>")
    if years_df.empty:
        html.append("  <p><em>No year data.</em></p>")
    else:
        html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
        html.append("    <thead><tr><th>Year</th><th>Share of records</th><th>Count</th></tr></thead><tbody>")
        for _, row in years_df.iterrows():
            html.append(
                f"      <tr><td>{_escape(str(row['label']))}</td>"
                f"<td>{int(row['pct'])}%</td><td>{int(row['value'])}</td></tr>"
            )
        html.append("    </tbody></table>")

    html.append("</section>")
    return "".join(html)


# ======================================================================
# MAIN ENTRY POINT  — drop-in replacement for create_reports_overview()
# Same signature, same return value, no temp tables, no Python set ops.
# ======================================================================

def create_reports_overview(report1: Report, report2: Report):
    """
    Return three HTML snippets:
      1) drug_reaction_summary_html  — demographics for reports with BOTH common drugs & AEs
      2) drug_summary_html           — demographics for reports with common drugs
      3) reaction_summary_html       — demographics for reports with common AEs

    Reporter countries from report1/report2 are highlighted in the
    Top Reporting Countries table of each section.
    """

    # ---- common names ------------------------------------------------
    common_drug_names = (
        {d.drug_name for d in report1.drugs} & {d.drug_name for d in report2.drugs}
    )
    common_reaction_names = (
        {r.pt_name for r in report1.reactions} & {r.pt_name for r in report2.reactions}
    )

    # sort so joblib cache key is consistent regardless of set ordering
    drug_names     = sorted(common_drug_names)
    reaction_names = sorted(common_reaction_names)

    # ---- reporter countries — for highlighting only, not SQL params -----
    def _clean_country(c):
        if not c or str(c).lower() in ("unknown", "none", ""):
            return None
        return c

    reporter_countries = tuple(
        c for c in (_clean_country(getattr(report1, "country", None)),
                    _clean_country(getattr(report2, "country", None)))
        if c is not None
    )

    # ---- drugs -------------------------------------------------------
    if not drug_names:
        drug_summary_html = (
            "<section><h3>Common Drugs</h3>"
            "<p><em>No common drugs found in the two reports.</em></p></section>"
        )
    else:
        print(f"Fetching drug demographics for: {drug_names}")
        df = fetch_drug_demographics_summary(tuple(drug_names), TARGET_DB)
        drug_summary_html = summary_of_demographics_html_from_summary(
            df, common_names=drug_names, title_heading="Common Drugs",
            reporter_countries=reporter_countries,
        )

    # ---- reactions ---------------------------------------------------
    if not reaction_names:
        reaction_summary_html = (
            "<section><h3>Common Adverse Events</h3>"
            "<p><em>No common adverse events found in the two reports.</em></p></section>"
        )
    else:
        print(f"Fetching reaction demographics for: {reaction_names}")
        df = fetch_reaction_demographics_summary(tuple(reaction_names), TARGET_DB)
        reaction_summary_html = summary_of_demographics_html_from_summary(
            df, common_names=reaction_names, title_heading="Common Adverse Events",
            reporter_countries=reporter_countries,
        )

    # ---- intersection ------------------------------------------------
    if not drug_names or not reaction_names:
        drug_reaction_summary_html = (
            "<section><h3>Common Drug–Adverse Event Pairs</h3>"
            "<p><em>No common drug–adverse event pairs found in the two reports.</em></p></section>"
        )
    else:
        print("Fetching drug+reaction intersection demographics")
        df = fetch_drug_reaction_demographics_summary(
            tuple(drug_names), tuple(reaction_names), TARGET_DB
        )
        both_names = drug_names + reaction_names
        drug_reaction_summary_html = summary_of_demographics_html_from_summary(
            df, common_names=both_names, title_heading="Common Drug–Adverse Event",
            reporter_countries=reporter_countries,
        )

    return drug_reaction_summary_html, drug_summary_html, reaction_summary_html

def _sanitize_text(val):
    """Remove LaTeX-style backslash escapes and normalize to plain text."""
    if val is None:
        return "None"
    s = str(val)
    # common LaTeX-ish escapes
    s = (
        s.replace("\\ ", " ")
        .replace("\\,", ",")
        .replace("\\.", ".")
        .replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\-", "-")
        .replace("\\/", "/")
        .replace("\\:", ":")
    )
    # if anything else remains, drop lone backslashes
    s = s.replace("\\", "")
    return s.strip()


def _esc(val):
    """Sanitize then HTML-escape."""
    return _escape(_sanitize_text(val), quote=True)


def render_report_html(report, title="Case Report"):
    base_info = {
        "Sex": report.get("sex"),
        "Age": report.get("age"),
        "Country": report.get("country"),
    }

    # Narrative
    raw_narrative = report.get("narrative", "")
    if not raw_narrative or (
        isinstance(raw_narrative, str) and raw_narrative.strip() == ""
    ):
        narrative_html = "<em>No narrative provided</em>"
    else:
        narrative_html = _esc(raw_narrative)  # safe & no stray backslashes
    # Study html
    study_name = report.get("studyname", "")
    if not study_name or (isinstance(study_name, str) and study_name.strip() == ""):
        study_html = "<em>No study name provided</em>"
    else:
        study_html = _esc(study_name)  # safe & no stray backslashes
    # Literature reference html
    literature_reference = report.get("literatureeference", "")
    if not literature_reference or (
        isinstance(literature_reference, str) and literature_reference.strip() == ""
    ):
        literature_html = "<em>No literature reference provided</em>"
    else:
        literature_html = _esc(literature_reference)  # safe & no stray backslashes

        narrative_html = _esc(raw_narrative)  # safe & no stray backslashes

    # Start HTML
    html = "<div style='margin-bottom: 2em; border: 1px solid #ccc; padding: 10px;'>"
    # html += "<h3>Case Report</h3><ul>"
    html += f"<h3>{_esc(title)}</h3><ul>"

    # Base info list
    for k, v in base_info.items():
        html += f"<li><strong>{_esc(k)}:</strong> {_esc(v)}</li>"

    html += "</ul>"

    # Tables (make sure format_table uses _esc internally, see below)
    html += format_table(
        "Reactions",
        report.get("reactions", []),
        ["pt_name", "start_date", "end_date", "outcome"],
    )
    html += format_table(
        "Drugs",
        report.get("drugs", []),
        [
            "drug_name",
            "drug_role",
            "start_date",
            "end_date",
            "indication",
            "dosageaction_taken",
            "administration_route",
        ],
    )
    html += format_table(
        "Medical History",
        report.get("medical_history_episodes", []),
        ["term", "start_date", "end_date", "continuing"],
    )
    html += format_table(
        "Test Results",
        report.get("Test_history", []),
        [
            "TestName",
            "TestResult",
            "Testunit",
            "LowTestRange",
            "HighTestRange",
            "CommentsOnTestandProcedures",
        ],
    )
    html += format_table(
        "Drug History",
        report.get("Drughistory", []),
        ["drug_name", "indication", "reaction", "start_date", "end_date"],
    )

    html += "</div>"
    html += "<div class='section'>"
    html += "<h3>Narrative</h3>"
    html += f"<div class='narrative'>{narrative_html}</div>"
    html += "<h3>Study Name</h3>"
    html += f"<div class='narrative'>{study_html}</div>"
    html += "<h3>Literature Reference</h3>"
    html += f"<div class='narrative'>{literature_html}</div>"
    html += "</div>"

    return html


def render_report_pair_html(report1, report2, link=None):
    # Render succinct HTML summarizing information from two reports
    # Only include features with a value for at least one of the reports - otherwise omit

    # ---- helpers ----
    # Extract available values per report
    def get_base_info(report):
        return {
            "Sex": report.get("sex", ""),
            "Age": report.get("age", ""),
            "Weight": report.get("weight", ""),
            "Height": report.get("height", ""),
            "Country": report.get("country", ""),
            "Report Date": report.get("report_date", ""),
            "Narrative": _esc(report.get("narrative", "")),
            "Literature Reference": _esc(report.get("literatureeference", "")),
            "Study Name": _esc(report.get("studyname", "")),
        }

    def fmt_dates(start, end):
        if not is_missing(start) and not is_missing(end):
            return f" ({_esc(start)} - {_esc(end)})"
        if not is_missing(start):
            return f" ({_esc(start)})"
        return ""

    def fmt_dosage(dosage):
        if not is_missing(dosage):
            return f" [{_esc(dosage)}]"
        return ""

    def fmt_name_by_role(name, role, presence_status=None):
        n = _esc(name)
        if role == "Suspected":
            inner = f"<strong>{n}</strong>"
        elif role == "Interacting":
            inner = f"<em>{n}</em>"
        else:
            inner = n
        return wrap_html(inner, presence_status)

    def normalize_key(name):
        return (name or "").strip().casefold()

    def norm(v):
        if is_missing(v):
            return None
        return str(v).strip().casefold()
    
    def cmp_status(v1, v2):
        n1, n2 = norm(v1), norm(v2)
        if n1 is None or n2 is None:
            return "neutral"   # one missing -> neutral
        return "match" if n1 == n2 else "mismatch"

    def wrap_html(inner_html, status):
        if status == "match":
            return f"<span class='match'>{inner_html}</span>"
        if status == "mismatch":
            return f"<span class='mismatch'>{inner_html}</span>"
        return inner_html

    def wrap_cell_value(val, status):
        if is_missing(val):
            return ""
        txt = _esc(val)
        if status == "match":
            return f"<span class='match'>{txt}</span>"
        if status == "mismatch":
            return f"<span class='mismatch'>{txt}</span>"
        return txt
    
    def fmt_dates_colored(start, end, other_start, other_end):
        st_start = cmp_status(start, other_start)
        st_end   = cmp_status(end, other_end)

        if not is_missing(start) and not is_missing(end):
            return f" ({wrap_cell_value(start, st_start)} - {wrap_cell_value(end, st_end)})"
        if not is_missing(start):
            return f" ({wrap_cell_value(start, st_start)})"
        return ""

    def fmt_dosage_colored(dosage, other_dosage):
        st = cmp_status(dosage, other_dosage)
        if not is_missing(dosage):
            return f" [{wrap_cell_value(dosage, st)}]"
        return ""

    def pair_by_similarity(list1, list2, fields):
        """Greedy pairing for multiple entries per drug/PT."""
        remaining2 = list(list2)
        pairs = []

        for a in list1:
            if not remaining2:
                pairs.append((a, None))
                continue

            best_j, best_score = 0, -1
            for j, b in enumerate(remaining2):
                score = 0
                for f in fields:
                    if cmp_status(a.get(f), b.get(f)) == "match":
                        score += 1
                if score > best_score:
                    best_score, best_j = score, j

            pairs.append((a, remaining2.pop(best_j)))

        for b in remaining2:
            pairs.append((None, b))

        return pairs

    def render_drug_line(d_self, d_other, presence_status):
        other = d_other or {}
        return (
            fmt_name_by_role(d_self.get("drug_name", ""), d_self.get("drug_role"), presence_status)
            + fmt_dates_colored(
                d_self.get("start_date"), d_self.get("end_date"),
                other.get("start_date"), other.get("end_date")
            )
            + fmt_dosage_colored(d_self.get("dosage"), other.get("dosage"))
        )

    def render_ae_line(e_self, e_other, presence_status):
        other = e_other or {}
        name_html = wrap_html(_esc(e_self.get("pt_name", "")), presence_status)
        return (
            name_html
            + fmt_dates_colored(
                e_self.get("start"), e_self.get("end"),
                other.get("start"), other.get("end")
            )
        )

    # ---- begin script ----
    if not isinstance(report1, dict):
        report1 = asdict(report1)
    if not isinstance(report2, dict):
        report2 = asdict(report2)

    # Enumerate fields of interest
    fields = [
        "Sex",
        "Age",
        "Weight",
        "Height",
        "Country",
        "Narrative",
        "Report Date",
        "Literature Reference",
        "Study Name",
    ]

    # Get data for the two reports of interest
    info1 = get_base_info(report1)
    info2 = get_base_info(report2)

    # Determine which base columns to include: present in at least one
    base_columns = [
        k
        for k in fields
        # if (info1.get(k) is not None) or (info2.get(k) is not None)
        if (not is_missing(info1.get(k)) or not is_missing(info2.get(k)))
    ]
    base_status = {col: cmp_status(info1.get(col), info2.get(col)) for col in base_columns}

    # ---- collect drug columns from both reports ----
    drugs1 = report1.get("drugs", [])
    drugs2 = report2.get("drugs", [])

    # Union of drug names (normalized), but keep a nice display name from first occurrence
    drug_display_by_key = {}
    for d in drugs1 + drugs2:
        drug_name = d["drug_name"]
        if not drug_name:
            continue
        key = normalize_key(drug_name)
        drug_display_by_key.setdefault(
            key, drug_name
        )  # preserve first-seen capitalization

    # Stable, readable order (alphabetical by display name, casefolded)
    drug_columns = sorted(drug_display_by_key.items(), key=lambda kv: kv[1].casefold())
    # drug_columns is a list of (key, display_name)

    # ---- collect adverse event columns from both reports ----
    reactions1 = report1.get("reactions", [])
    reactions2 = report2.get("reactions", [])

    # Union of PT names (normalized), preserve first-seen display
    ae_display_by_key = {}
    for r in reactions1 + reactions2:
        pt = r.get("pt_name")
        if not pt:  # or: if is_missing(pt): continue
            continue
        key = normalize_key(pt)
        ae_display_by_key.setdefault(key, pt)

    # Stable, readable order (alphabetical by display name)
    ae_columns = sorted(ae_display_by_key.items(), key=lambda kv: kv[1].casefold())
    # ae_columns is a list of (key, display_name)

    # presence sets (normalized keys)
    drug_keys1 = {normalize_key(d.get("drug_name")) for d in drugs1 if d.get("drug_name")}
    drug_keys2 = {normalize_key(d.get("drug_name")) for d in drugs2 if d.get("drug_name")}
    common_drug_keys = drug_keys1 & drug_keys2

    ae_keys1 = {normalize_key(r.get("pt_name")) for r in reactions1 if r.get("pt_name")}
    ae_keys2 = {normalize_key(r.get("pt_name")) for r in reactions2 if r.get("pt_name")}
    common_ae_keys = ae_keys1 & ae_keys2

    # Start HTML
    html = """
    <style>
    .match { color:#0a7f2e; font-weight:600; }
    .mismatch { color:#b42318; font-weight:600; }

    .rp-wrap { margin-bottom:2em; border:1px solid #ccc; padding:10px; box-sizing:border-box; max-width:100%; }
    .rp-scroll { overflow-x:auto; }
    .rp-table { border-collapse:collapse; }
    .rp-table th, .rp-table td { border:1px solid #ccc; padding:4px; vertical-align:top; word-break:break-word; white-space:normal; }
    </style>
    <div class="rp-wrap">
    """.strip()
    if link is not None :
        html += (
            "<h3>Report pair comparison "
            f"<a href=\"{link}\" target=\"_blank\">(vigilyze link)</a>"
            "</h3>"
        )
    else:
        html += "<h3>Report pair comparison</h3>"
    html += '<div class="rp-scroll">'
    html += '<table class="rp-table">'

    # Build HTML table
    html += "<table border='1' cellspacing='0' cellpadding='4'>"

    # header
    html += "<thead><tr><th>Report</th>"
    for col in base_columns:
        html += f"<th>{_esc(col)}</th>"
    for _, disp in drug_columns:
        html += f"<th>{_esc(disp)}</th>"
    for _, disp in ae_columns:
        html += f"<th>{_esc(disp)}</th>"
    html += "</tr></thead><tbody>"

    # quick index of drugs per report by normalized name
    def index_drugs(drug_list):
        idx = {}
        for d in drug_list:
            drug_name = d["drug_name"]
            if not drug_name:
                continue
            key = normalize_key(drug_name)
            idx.setdefault(key, []).append(d)
        return idx

    idx1 = index_drugs(drugs1)
    idx2 = index_drugs(drugs2)

    # quick index of PTs per report by normalized name
    def index_reactions(reaction_list):
        idx = {}
        for r in reaction_list:
            pt = r.get("pt_name")
            if not pt:  # or: if is_missing(pt): continue
                continue
            key = normalize_key(pt)
            idx.setdefault(key, []).append(
                {
                    "pt_name": pt,
                    "start": r.get("start_date"),
                    "end": r.get("end_date"),
                }
            )
        return idx

    ridx1 = index_reactions(reactions1)
    ridx2 = index_reactions(reactions2)

    # --- precompute drug cells with aligned pairing ---
    drug_cell_1, drug_cell_2 = {}, {}
    DRUG_PAIR_FIELDS = ["drug_role", "start_date", "end_date", "dosage"]

    for key, _disp in drug_columns:
        presence = "match" if key in common_drug_keys else "mismatch"
        pairs = pair_by_similarity(idx1.get(key, []), idx2.get(key, []), DRUG_PAIR_FIELDS)

        lines1, lines2 = [], []
        for d1, d2 in pairs:
            if d1 is not None:
                lines1.append(render_drug_line(d1, d2, presence))
            if d2 is not None:
                lines2.append(render_drug_line(d2, d1, presence))

        drug_cell_1[key] = "<br>".join(lines1)
        drug_cell_2[key] = "<br>".join(lines2)

    # --- precompute AE cells with aligned pairing ---
    ae_cell_1, ae_cell_2 = {}, {}
    AE_PAIR_FIELDS = ["start", "end"]

    for key, _disp in ae_columns:
        presence = "match" if key in common_ae_keys else "mismatch"
        pairs = pair_by_similarity(ridx1.get(key, []), ridx2.get(key, []), AE_PAIR_FIELDS)

        lines1, lines2 = [], []
        for e1, e2 in pairs:
            if e1 is not None:
                lines1.append(render_ae_line(e1, e2, presence))
            if e2 is not None:
                lines2.append(render_ae_line(e2, e1, presence))

        ae_cell_1[key] = "<br>".join(lines1)
        ae_cell_2[key] = "<br>".join(lines2)

    for i, (info, dindex_drugs, dindex_reactions) in enumerate(
        [(info1, idx1, ridx1), (info2, idx2, ridx2)], start=1
    ):
        html += "<tr>"
        html += f"<td><strong>Report {i}</strong></td>"

        drug_cells = drug_cell_1 if i == 1 else drug_cell_2
        ae_cells   = ae_cell_1   if i == 1 else ae_cell_2

        # base columns
        for col in base_columns:
            val = info.get(col)
            html += f"<td>{wrap_cell_value(val, base_status[col])}</td>"
            # html += f"<td>{_esc(val) if not is_missing(val) else ''}</td>"

        # drug columns
        for key, _disp in drug_columns:
            html += f"<td>{drug_cells.get(key, '')}</td>"
            #for key, _disp in drug_columns:
            #entries = dindex_drugs.get(key, [])
            #if not entries:
            #    html += "<td></td>"
            #    continue
            ## join multiple entries (same drug name) with line breaks
            #parts = []
            #for d in entries:
            #    part = (
            #        fmt_name_by_role(d["drug_name"], d["drug_role"])
            #        + fmt_dates(d["start_date"], d["end_date"])
            #        + fmt_dosage(d["dosage"])
            #    )
            #    parts.append(part)
            #html += f"<td>{'<br>'.join(parts)}</td>"

        # adverse event columns
        for key, _disp in ae_columns:
            html += f"<td>{ae_cells.get(key, '')}</td>"
        # for key, _disp in ae_columns:
        #     entries = dindex_reactions.get(key, [])
        #     if not entries:
        #         html += "<td></td>"
        #         continue

        #     # Multiple entries for the same PT in one report? Join with <br>
        #     parts = []
        #     for e in entries:
        #         # no role formatting for AEs; just name + dates
        #         part = f"{_esc(e['pt_name'])}{fmt_dates(e['start'], e['end'])}"
        #         parts.append(part)

        #     html += f"<td>{'<br>'.join(parts)}</td>"

        html += "</tr>"

    html += "</tbody></table></div></div>"

    return html


def format_table(title, rows, columns):
    """Generate an HTML table with a title."""
    if not rows:
        return f"<h4>{_esc(title)}</h4><p><em>No data available</em></p>"

    html = f"<h4>{_esc(title)}</h4><table border='1' cellpadding='5'><tr>"
    html += "".join(f"<th>{_esc(col)}</th>" for col in columns)
    html += "</tr>"

    for row in rows:
        html += (
            "<tr>"
            + "".join(f"<td>{_esc(row.get(col, ''))}</td>" for col in columns)
            + "</tr>"
        )

    html += "</table>"
    return html


# def add_html_section(report_html: str, heading: str, data: dict) -> str:
#     """
#     Insert a new <section> with a heading and key/value content
#     right after the last </table> in report_html. Falls back to
#     before </body> or end of string if no table is found.
#     """
#     # Build the section HTML
#     parts = []
#     parts.append("<section id='ae-response' style='margin-top:1em;'>")
#     parts.append(f"<h3>{_escape(heading)}</h3>")
#     # If you want only specific keys, adjust here:
#     for k, v in data.items():
#         label = k.replace("_", " ").capitalize()
#         parts.append(f"<p><strong>{_escape(label)}:</strong> {_escape(str(v) if v is not None else '')}</p>")
#     parts.append("</section>")
#     section_html = "".join(parts)

#     # Find insertion point: after the last </table>
#     idx = report_html.rfind("</table>")
#     if idx != -1:
#         insert_at = idx + len("</table>")
#         return report_html[:insert_at] + section_html + report_html[insert_at:]

#     # Fallback: before </body>
#     body_idx = report_html.lower().rfind("</body>")
#     if body_idx != -1:
#         return report_html[:body_idx] + section_html + report_html[body_idx:]

#     # Last resort: append
#     return report_html + section_html


def add_html_section(report_html: str, heading: str, data: dict) -> str:
    """
    Insert a new <section> with a heading and key/value content
    at the end of the HTML (before </body> if present).
    """
    # Build the section HTML
    parts = []
    parts.append("<section id='ae-response' style='margin-top:1em;'>")
    parts.append(f"<h3>{_escape(heading)}</h3>")
    for k, v in data.items():
        label = k.replace("_", " ").capitalize()
        parts.append(
            f"<p><strong>{_escape(label)}:</strong> "
            f"{_escape(str(v) if v is not None else '')}</p>"
        )
    parts.append("</section>")
    section_html = "".join(parts)

    # Prefer to insert before </body>
    body_idx = report_html.lower().rfind("</body>")
    if body_idx != -1:
        return report_html[:body_idx] + section_html + report_html[body_idx:]

    # Fallback: just append at the very end
    return report_html + section_html


def summary_of_demographics_html(demo, common_names, title_heading) -> str:
    """
    Summarize demographics with country %, age IQR, and top-3 years %, as HTML.
    Safe to concatenate after render_report_pair_html's output.
    """
    names_str = ", ".join(common_names) if common_names else "Selection"

    # No data case
    if demo is None or len(demo) == 0:
        return (
            f"<section style='margin-top:1.5em'>"
            f"  <h3>Demographics for {title_heading} ({_escape(names_str)})</h3>"
            f"  <p><em>No records available.</em></p>"
            f"</section>"
        )

    ages = pd.to_numeric(demo["age"], errors="coerce")
    db_dates = pd.to_datetime(demo["dbDate"], errors="coerce")
    years = db_dates.dt.year

    n_rows = len(demo)
    total_reports = int(
        getattr(demo["ReportID"], "nunique", lambda: len(pd.Series(dtype="int64")))()
    )

    if ages.notna().any():
        avg_age = int(round(ages.mean()))
        q1 = int(round(ages.quantile(0.25)))
        q3 = int(round(ages.quantile(0.75)))
    else:
        avg_age = q1 = q3 = None

    sex = (
        demo["sex"]
        .astype(str)
        .str.strip()
        .str.title()
        .replace({"Nan": "Unknown", "None": "Unknown", "": "Unknown"})
    )
    pct_male = int(round((sex == "Male").mean() * 100)) if n_rows else 0
    pct_female = int(round((sex == "Female").mean() * 100)) if n_rows else 0
    pct_unknown = max(0, 100 - pct_male - pct_female)

    countries = demo["SourceCountry"].fillna("Unknown").astype(str).str.strip()
    top_countries_pct = (
        (countries.value_counts(normalize=True) * 100).round().astype(int).head(3)
    )

    year_counts = years.value_counts(dropna=True)
    top3_years = year_counts.head(3)
    top3_years_pct = ((top3_years / n_rows) * 100).round().astype(int)

    def fmt_int_or_na(x):
        return (
            "NA" if x is None or (isinstance(x, float) and np.isnan(x)) else str(int(x))
        )

    # ---- HTML rendering ----
    html = []
    html.append("<section style='margin-top:1.5em'>")
    html.append(f"<h3>Demographics for {title_heading} ({_escape(names_str)})</h3>")

    # Overview table
    html.append(
        "  <table border='1' cellspacing='0' cellpadding='5' style='margin-top:0.5em'>"
    )
    html.append("    <thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>")
    html.append(
        f"      <tr><td>Total Reports</td><td>{_escape(str(total_reports))}</td></tr>"
    )
    html.append(
        "      <tr><td>Average Age (Q1–Q3)</td>"
        f"<td>{_escape(fmt_int_or_na(avg_age))} "
        f"(Q1={_escape(fmt_int_or_na(q1))}, Q3={_escape(fmt_int_or_na(q3))})</td></tr>"
    )
    html.append("    </tbody></table>")

    # Sex distribution table
    html.append("  <h4 style='margin-top:1em'>Gender Distribution</h4>")
    html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
    html.append(
        "    <thead><tr><th>Male</th><th>Female</th><th>Unknown</th></tr></thead>"
    )
    html.append(
        f"    <tbody><tr><td>{pct_male}%</td><td>{pct_female}%</td><td>{pct_unknown}%</td></tr></tbody>"
    )
    html.append("  </table>")

    # Top countries table
    html.append("  <h4 style='margin-top:1em'>Top Reporting Countries</h4>")
    if top_countries_pct.empty:
        html.append("  <p><em>No country data.</em></p>")
    else:
        html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
        html.append(
            "    <thead><tr><th>Country</th><th>Share of records</th></tr></thead><tbody>"
        )
        for c, p in top_countries_pct.items():
            html.append(f"      <tr><td>{_escape(str(c))}</td><td>{int(p)}%</td></tr>")
        html.append("    </tbody></table>")

    # Top years table
    html.append("  <h4 style='margin-top:1em'>Top Years</h4>")
    if top3_years.empty:
        html.append("  <p><em>No year data.</em></p>")
    else:
        html.append("  <table border='1' cellspacing='0' cellpadding='5'>")
        html.append(
            "    <thead><tr><th>Year</th><th>Share of records</th><th>Count</th></tr></thead><tbody>"
        )
        for y, cnt, pct in zip(
            top3_years.index, top3_years.values, top3_years_pct.values
        ):
            html.append(
                f"      <tr><td>{_escape(str(int(y)))}</td><td>{int(pct)}%</td><td>{int(cnt)}</td></tr>"
            )
        html.append("    </tbody></table>")

    html.append("</section>")
    return "".join(html)


def render_report_pair_html_columnwise(report1, report2):
    # Render succinct HTML summarizing information from two reports
    # Only include features with a value for at least one of the reports - otherwise omit

    # ---- helpers ----
    def get_base_info(report):
        return {
            "Sex": report.get("sex", ""),
            "Age": report.get("age", ""),
            "Weight": report.get("weight", ""),
            "Height": report.get("height", ""),
            "Country": report.get("country", ""),
            "Report Date": report.get("report_date", ""),
            "Narrative": _esc(report.get("narrative", "")),
            "Literature Reference": _esc(report.get("literatureeference", "")),
            "Study Name": _esc(report.get("studyname", "")),
        }

    def fmt_dates(start, end):
        if not is_missing(start) and not is_missing(end):
            return f" ({_esc(start)} - {_esc(end)})"
        if not is_missing(start):
            return f" ({_esc(start)})"
        return ""

    def fmt_dosage(dosage):
        if not is_missing(dosage):
            return f" [{_esc(dosage)}]"
        return ""

    def fmt_name_by_role(name, role):
        n = _esc(name)
        if role == "Suspected":
            return f"<strong>{n}</strong>"
        if role == "Interacting":
            return f"<em>{n}</em>"
        return n

    def normalize_key(name):
        return (name or "").strip().casefold()

    if not isinstance(report1, dict):
        report1 = asdict(report1)
    if not isinstance(report2, dict):
        report2 = asdict(report2)

    # Enumerate fields of interest
    fields = [
        "Sex",
        "Age",
        "Weight",
        "Height",
        "Country",
        "Narrative",
        "Report Date",
        "Literature Reference",
        "Study Name",
    ]

    # Get data
    info1 = get_base_info(report1)
    info2 = get_base_info(report2)

    # Determine base rows to include (present in at least one)
    base_rows = [
        k
        for k in fields
        if (not is_missing(info1.get(k)) or not is_missing(info2.get(k)))
    ]

    # ---- collect drug columns from both reports ----
    drugs1 = report1.get("drugs", [])
    drugs2 = report2.get("drugs", [])

    drug_display_by_key = {}
    for d in drugs1 + drugs2:
        drug_name = d.get("drug_name")
        if not drug_name:
            continue
        key = normalize_key(drug_name)
        drug_display_by_key.setdefault(key, drug_name)

    drug_rows = sorted(
        drug_display_by_key.items(), key=lambda kv: kv[1].casefold()
    )  # list[(key, display_name)]

    # ---- collect adverse event columns from both reports ----
    reactions1 = report1.get("reactions", [])
    reactions2 = report2.get("reactions", [])

    ae_display_by_key = {}
    for r in reactions1 + reactions2:
        pt = r.get("pt_name")
        if not pt:
            continue
        key = normalize_key(pt)
        ae_display_by_key.setdefault(key, pt)

    ae_rows = sorted(
        ae_display_by_key.items(), key=lambda kv: kv[1].casefold()
    )  # list[(key, display_name)]

    # quick index of drugs per report by normalized name
    def index_drugs(drug_list):
        idx = {}
        for d in drug_list:
            drug_name = d.get("drug_name")
            if not drug_name:
                continue
            key = normalize_key(drug_name)
            idx.setdefault(key, []).append(d)
        return idx

    idx1 = index_drugs(drugs1)
    idx2 = index_drugs(drugs2)

    # quick index of PTs per report by normalized name
    def index_reactions(reaction_list):
        idx = {}
        for r in reaction_list:
            pt = r.get("pt_name")
            if not pt:
                continue
            key = normalize_key(pt)
            idx.setdefault(key, []).append(
                {
                    "pt_name": pt,
                    "start": r.get("start_date"),
                    "end": r.get("end_date"),
                }
            )
        return idx

    ridx1 = index_reactions(reactions1)
    ridx2 = index_reactions(reactions2)

    # ---- HTML (column layout: Report 1 | Report 2) ----
    html = """
<div style='margin-bottom: 2em; border: 1px solid #ccc; padding: 10px;'>
  <h3>Report pair comparison</h3>
  <table style="width:100%; border-collapse:collapse; table-layout:fixed;" cellspacing="0" cellpadding="4">
    <colgroup>
      <col style="width:24%;">
      <col style="width:38%;">
      <col style="width:38%;">
    </colgroup>
    <thead>
      <tr>
        <th style="text-align:left; border:1px solid #ccc;">Feature</th>
        <th style="text-align:left; border:1px solid #ccc;">Report 1</th>
        <th style="text-align:left; border:1px solid #ccc;">Report 2</th>
      </tr>
    </thead>
    <tbody>
""".strip()

    def td(val):
        return f"<td style='vertical-align:top; border:1px solid #ccc; word-break:break-word; white-space:normal;'>{_esc(val) if not is_missing(val) else ''}</td>"

    def th_row(label):
        return f"<th scope='row' style='text-align:left; vertical-align:top; border:1px solid #ccc; background:#fafafa;'>{_esc(label)}</th>"

    # Base rows
    for col in base_rows:
        html += "<tr>"
        html += th_row(col)
        html += td(info1.get(col))
        html += td(info2.get(col))
        html += "</tr>"

    # Section: Drugs (only if any)
    if drug_rows:
        html += f"<tr><th colspan='3' style='text-align:left; border:1px solid #ccc; background:#f0f0f0;'>Drugs</th></tr>"
        for key, disp in drug_rows:
            # Report 1 entries
            parts1 = []
            for d in idx1.get(key, []):
                parts1.append(
                    fmt_name_by_role(d.get("drug_name"), d.get("drug_role"))
                    + fmt_dates(d.get("start_date"), d.get("end_date"))
                    + fmt_dosage(d.get("dosage"))
                )
            # Report 2 entries
            parts2 = []
            for d in idx2.get(key, []):
                parts2.append(
                    fmt_name_by_role(d.get("drug_name"), d.get("drug_role"))
                    + fmt_dates(d.get("start_date"), d.get("end_date"))
                    + fmt_dosage(d.get("dosage"))
                )
            html += "<tr>"
            html += th_row(disp)
            html += f"<td style='vertical-align:top; border:1px solid #ccc; word-break:break-word;'>{'<br>'.join(parts1)}</td>"
            html += f"<td style='vertical-align:top; border:1px solid #ccc; word-break:break-word;'>{'<br>'.join(parts2)}</td>"
            html += "</tr>"

    # Section: Adverse Events (only if any)
    if ae_rows:
        html += f"<tr><th colspan='3' style='text-align:left; border:1px solid #ccc; background:#f0f0f0;'>Adverse Events</th></tr>"
        for key, disp in ae_rows:
            parts1 = []
            for e in ridx1.get(key, []):
                parts1.append(f"{_esc(e['pt_name'])}{fmt_dates(e['start'], e['end'])}")
            parts2 = []
            for e in ridx2.get(key, []):
                parts2.append(f"{_esc(e['pt_name'])}{fmt_dates(e['start'], e['end'])}")
            html += "<tr>"
            html += th_row(disp)
            html += f"<td style='vertical-align:top; border:1px solid #ccc; word-break:break-word;'>{'<br>'.join(parts1)}</td>"
            html += f"<td style='vertical-align:top; border:1px solid #ccc; word-break:break-word;'>{'<br>'.join(parts2)}</td>"
            html += "</tr>"

    html += """
    </tbody>
  </table>
</div>
""".strip()

    return html
