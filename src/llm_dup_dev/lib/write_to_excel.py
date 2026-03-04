# pip install xlsxwriter
from dataclasses import asdict
import xlsxwriter


def is_missing(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def normalize_key(name):
    return (name or "").strip().casefold()


def fmt_dates(start, end):
    if not is_missing(start) and not is_missing(end):
        return f" ({start} - {end})"
    if not is_missing(start):
        return f" ({start})"
    return ""


def fmt_dosage(dosage):
    if not is_missing(dosage):
        return f" [{dosage}]"
    return ""


def get_base_info(report):
    # Match your HTML fields & keys
    return {
        "Sex": report.get("sex", ""),
        "Age": report.get("age", ""),
        "Weight": report.get("weight", ""),
        "Height": report.get("height", ""),
        "Country": report.get("country", ""),
        "Report Date": report.get("report_date", ""),
        "Narrative": report.get("narrative", ""),
        "Literature Reference": report.get("literatureeference", ""),
        "Study Name": report.get("studyname", ""),
    }


def index_drugs(drug_list):
    idx = {}
    for d in drug_list or []:
        name = d.get("drug_name")
        if not name:
            continue
        key = normalize_key(name)
        idx.setdefault(key, []).append(d)
    return idx


def index_reactions(reaction_list):
    idx = {}
    for r in reaction_list or []:
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


def union_display_map(items, key_name, display_getter):
    """
    Build {normalized: first_seen_display} for a list of dicts
    """
    out = {}
    for it in items:
        disp = display_getter(it)
        if not disp:
            continue
        key = normalize_key(disp)
        if key not in out:
            out[key] = disp
    return out


def build_columns(report1, report2):
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
    info1 = get_base_info(report1)
    info2 = get_base_info(report2)

    base_columns = [
        k for k in fields if not (is_missing(info1.get(k)) and is_missing(info2.get(k)))
    ]

    drugs1 = report1.get("drugs", []) or []
    drugs2 = report2.get("drugs", []) or []
    drug_display = {}
    drug_display.update(
        union_display_map(drugs1, "drug_name", lambda d: d.get("drug_name"))
    )
    for d in drugs2:
        name = d.get("drug_name")
        if name and normalize_key(name) not in drug_display:
            drug_display[normalize_key(name)] = name
    drug_columns = sorted(drug_display.items(), key=lambda kv: kv[1].casefold())

    rx1 = report1.get("reactions", []) or []
    rx2 = report2.get("reactions", []) or []
    ae_display = {}
    ae_display.update(union_display_map(rx1, "pt_name", lambda r: r.get("pt_name")))
    for r in rx2:
        pt = r.get("pt_name")
        if pt and normalize_key(pt) not in ae_display:
            ae_display[normalize_key(pt)] = pt
    ae_columns = sorted(ae_display.items(), key=lambda kv: kv[1].casefold())

    return base_columns, drug_columns, ae_columns


def make_drug_cell(parts):
    """
    parts: list of (name, role, dates_str, dosage_str)
    Returns plain_text
    """
    lines = []
    for name, role, dates_str, dosage_str in parts:
        name = "" if name is None else str(name)
        dates_str = "" if dates_str is None else str(dates_str)
        dosage_str = "" if dosage_str is None else str(dosage_str)

        # Just include role as literal text if you want "Suspected", "Interacting", etc.
        if role == "Suspected":
            line = f"{role} {name}{dates_str}{dosage_str}".strip()
        else:
            line = f"{name}{dates_str}{dosage_str}".strip()
        if line:
            lines.append(line)

    return "\n".join(lines) if lines else ""


def write_pair_table(ws, start_row, workbook, report1, report2, title=None):
    """
    Writes a single 2-row table starting at start_row.
    Returns the next empty row (start_row for the next block).
    """
    # Formats
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#F2F2F2",
            "border": 1,
            "text_wrap": True,
            "align": "center",
            "valign": "vcenter",
        }
    )
    border_wrap = workbook.add_format({"border": 1, "text_wrap": True, "valign": "top"})
    zebra = workbook.add_format(
        {"border": 1, "text_wrap": True, "valign": "top", "bg_color": "#FAFAFA"}
    )
    col0_fmt = workbook.add_format({"border": 1, "bold": True, "text_wrap": True})
    bold_fmt = workbook.add_format({"bold": True})
    ital_fmt = workbook.add_format({"italic": True})
    base_fmt = workbook.add_format()  # neutral for rich strings

    # Title
    row = start_row
    if title:
        ws.write(row, 0, title, workbook.add_format({"bold": True, "font_size": 12}))
        row += 1

    # Normalize inputs if they're dataclasses
    if not isinstance(report1, dict):
        report1 = asdict(report1)
    if not isinstance(report2, dict):
        report2 = asdict(report2)

    base_cols, drug_cols, ae_cols = build_columns(report1, report2)

    # Header
    headers = (
        ["Report"] + base_cols + [d for _, d in drug_cols] + [d for _, d in ae_cols]
    )
    for col, h in enumerate(headers):
        ws.write(row, col, h, header_fmt)
    row += 1

    # Indexes
    idx_drugs_1 = index_drugs(report1.get("drugs"))
    idx_drugs_2 = index_drugs(report2.get("drugs"))
    idx_rx_1 = index_reactions(report1.get("reactions"))
    idx_rx_2 = index_reactions(report2.get("reactions"))
    info1 = get_base_info(report1)
    info2 = get_base_info(report2)

    # For autosizing
    max_widths = [len(str(h)) for h in headers]

    def write_report_line(label, info, dindex_drugs, dindex_rx, is_zebra=False):
        nonlocal row, max_widths
        ws.write(row, 0, label, col0_fmt)
        max_widths[0] = max(max_widths[0], len(label))

        # base columns
        col = 1
        for c in base_cols:
            val = "" if is_missing(info.get(c)) else str(info.get(c))
            ws.write(row, col, val, zebra if is_zebra else border_wrap)
            max_widths[col] = max(max_widths[col], len(val))
            col += 1

        # drug columns
        for key, _disp in drug_cols:
            entries = dindex_drugs.get(key, [])
            if not entries:
                ws.write(row, col, "", zebra if is_zebra else border_wrap)
            else:
                parts = []
                for d in entries:
                    name = d.get("drug_name", "")
                    role = d.get("drug_role", "")
                    dates = fmt_dates(d.get("start_date"), d.get("end_date"))
                    dose = fmt_dosage(d.get("dosage"))
                    parts.append((name, role, dates, dose))
                    plain_text = make_drug_cell(parts)
                    cell_fmt = zebra if is_zebra else border_wrap
                    ws.write(row, col, plain_text, cell_fmt)
                max_widths[col] = max(max_widths[col], len(plain_text))
            col += 1

        # AE columns
        for key, _disp in ae_cols:
            entries = dindex_rx.get(key, [])
            if not entries:
                ws.write(row, col, "", zebra if is_zebra else border_wrap)
            else:
                # join as "PT (dates)" lines
                lines = []
                for e in entries:
                    lines.append(f"{e['pt_name']}{fmt_dates(e['start'], e['end'])}")
                val = "\n".join(lines)
                ws.write(row, col, val, zebra if is_zebra else border_wrap)
                max_widths[col] = max(
                    max_widths[col], max((len(x) for x in lines), default=0)
                )
            col += 1

        row += 1

    write_report_line("Report 1", info1, idx_drugs_1, idx_rx_1, is_zebra=False)
    write_report_line("Report 2", info2, idx_drugs_2, idx_rx_2, is_zebra=True)

    # Autosize columns (roughly)
    for c, w in enumerate(max_widths):
        # Scale character count to Excel width; add padding and cap
        ws.set_column(c, c, min(60, max(10, w + 2)))

    # Add an empty spacer row before next block
    row += 1
    return row


def export_report_pairs_to_excel(pairs, xlsx_path, one_sheet_per_pair=True):
    """
    pairs: iterable of (report1_dict, report2_dict, optional_title)
           optional_title can be None
    """
    wb = xlsxwriter.Workbook(xlsx_path)
    try:
        if one_sheet_per_pair:
            for i, item in enumerate(pairs, start=1):
                if len(item) == 3:
                    r1, r2, title = item
                else:
                    r1, r2 = item
                    title = f"Report pair {i}"
                ws = wb.add_worksheet(f"Pair {i}")
                ws.freeze_panes(2, 1)  # freeze header row + first column
                write_pair_table(ws, 0, wb, r1, r2, title=title)
        else:
            ws = wb.add_worksheet("All pairs")
            ws.freeze_panes(0, 0)
            row = 0
            for i, item in enumerate(pairs, start=1):
                if len(item) == 3:
                    r1, r2, title = item
                else:
                    r1, r2 = item
                    title = f"Report pair {i}"
                ws.freeze_panes(0, 0)  # (re)ensure
                row = write_pair_table(ws, row, wb, r1, r2, title=title)
    finally:
        wb.close()
