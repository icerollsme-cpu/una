"""
Excel Output Generator for SEO Optimization Agent.

Produces a multi-sheet workbook:
  Sheet 1  — Summary of all pages (one row each, color-coded priority)
  Sheet 2+ — Full 5-section brief per page (one sheet per keyword)
"""

import re
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Color palette ────────────────────────────────────────────────────────────
C_SECTION   = "1F3864"   # Dark navy  — section headers
C_COL_HDR   = "2E75B6"   # Medium blue — column/table headers
C_SUB_HDR   = "4472C4"   # Cornflower — sub-section headers
C_ROW_ALT   = "DEEAF1"   # Light blue  — alternating rows
C_KEY_CELL  = "E8F0F8"   # Very light  — KV key column
C_TITLE_BAR = "C55A11"   # Burnt orange — page title bars
C_REPORT_HDR= "1F3864"   # Same as section
C_HIGH      = "FF4C4C"   # Red    — High opportunity / priority
C_MED       = "FFC000"   # Amber  — Medium
C_LOW       = "70AD47"   # Green  — Low
C_WIN_YES   = "70AD47"   # Green
C_WIN_NO    = "FF4C4C"   # Red
C_WHITE     = "FFFFFF"
C_LIGHT_GRAY= "F2F2F2"

PRIORITY_COLORS = {"High": C_HIGH, "Med": C_MED, "Low": C_LOW}


def _font(size=9, bold=False, color=C_WHITE, italic=False, name="Calibri"):
    return Font(name=name, size=size, bold=bold, color=color, italic=italic)


def _fill(color: str):
    return PatternFill(fill_type="solid", fgColor=color)


def _align(h="left", v="top", wrap=True, indent=0):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap, indent=indent)


def _thin_border():
    side = Side(style="thin", color="CCCCCC")
    return Border(left=side, right=side, top=side, bottom=side)


class OutputGenerator:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.wb = Workbook()
        self.wb.remove(self.wb.active)  # drop default blank sheet

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, results: list):
        """Build summary + per-page sheets and save the workbook."""
        self._build_summary(results)
        for result in results:
            if not isinstance(result, dict):
                continue
            self._build_page_sheet(result)
        self.wb.save(self.output_path)

    # ── Summary sheet ─────────────────────────────────────────────────────────

    def _build_summary(self, results: list):
        ws = self.wb.create_sheet("Summary", 0)

        # ── Report title bar
        ws.merge_cells("A1:L1")
        c = ws["A1"]
        c.value = f"SEO Optimization Report  ·  Generated {datetime.now().strftime('%B %d, %Y at %H:%M')}"
        c.font = _font(size=13, bold=True)
        c.fill = _fill(C_REPORT_HDR)
        c.alignment = _align("center", "center", wrap=False)
        ws.row_dimensions[1].height = 26

        ws.append([""])  # blank row 2

        # ── Column headers (row 3)
        headers = [
            "Page URL", "Target Keyword", "Pos.", "Volume", "Difficulty",
            "Intent", "Opp.", "Effort", "Quick Win?",
            "Recommended Action", "Action Detail", "Flags / Errors",
        ]
        ws.append(headers)
        hrow = ws.max_row
        ws.row_dimensions[hrow].height = 22
        for col, _ in enumerate(headers, 1):
            c = ws.cell(hrow, col)
            c.font = _font(size=9, bold=True)
            c.fill = _fill(C_COL_HDR)
            c.alignment = _align("center", "center", wrap=True)
            c.border = _thin_border()

        # ── Data rows
        for idx, result in enumerate(results):
            rd = result.get("_raw_data", {})
            ps = result.get("priority_score", {})
            op = result.get("on_page", {})
            fl = result.get("flags", result.get("_flags", []))

            url      = result.get("url") or rd.get("Page URL", "")
            kw       = result.get("target_keyword") or rd.get("Target Keyword (primary)", "")
            pos      = ps.get("current_position") or rd.get("Current Position (avg)", "")
            volume   = rd.get("Monthly Search Volume", "")
            diff     = rd.get("Keyword Difficulty (0-100)", "")
            intent   = op.get("search_intent", "")
            opp      = ps.get("opportunity_size", "")
            effort   = ps.get("estimated_effort", "")
            qw_flag  = ps.get("quick_win_available")
            qw_desc  = ps.get("quick_win_description", "")
            qw_cell  = ("YES — " + qw_desc) if qw_flag else ("NO" if qw_flag is False else "")
            action   = ps.get("recommended_action", "")
            detail   = ps.get("action_detail", "")
            err_text = result.get("error", "")
            flag_str = " | ".join(fl)
            notes    = (f"ERROR: {err_text} | " if err_text else "") + flag_str

            row_vals = [url, kw, pos, volume, diff, intent, opp, effort, qw_cell, action, detail, notes]
            ws.append(row_vals)

            drow = ws.max_row
            bg = C_ROW_ALT if idx % 2 == 0 else C_WHITE
            ws.row_dimensions[drow].height = 50

            for col in range(1, len(headers) + 1):
                c = ws.cell(drow, col)
                c.font = _font(size=9, color="000000")
                c.fill = _fill(bg)
                c.alignment = _align("left", "top", wrap=True)
                c.border = _thin_border()

            # Colour-code opportunity column (col 7)
            if opp in PRIORITY_COLORS:
                c7 = ws.cell(drow, 7)
                c7.fill = _fill(PRIORITY_COLORS[opp])
                c7.font = _font(size=9, bold=True, color=C_WHITE)
                c7.alignment = _align("center", "center")

            # Colour-code quick win column (col 9)
            if qw_flag is True:
                c9 = ws.cell(drow, 9)
                c9.fill = _fill(C_WIN_YES)
                c9.font = _font(size=9, bold=True, color=C_WHITE)

        # ── Column widths
        widths = [40, 22, 7, 9, 10, 16, 7, 7, 30, 22, 35, 30]
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w

        ws.freeze_panes = "A4"

    # ── Per-page sheet ────────────────────────────────────────────────────────

    def _build_page_sheet(self, result: dict):
        rd   = result.get("_raw_data", {})
        url  = result.get("url") or rd.get("Page URL", "")
        kw   = result.get("target_keyword") or rd.get("Target Keyword (primary)", "Unknown")

        # Sheet name: sanitised keyword, max 28 chars, unique
        raw_name = re.sub(r'[\\/*?:\[\]]', "", kw)[:26].strip() or "Page"
        used = {s.title for s in self.wb.worksheets}
        sheet_name = raw_name
        counter = 2
        while sheet_name in used:
            sheet_name = raw_name[:23] + f" {counter}"
            counter += 1

        ws = self.wb.create_sheet(sheet_name)
        row = 1  # running row pointer

        # ── Page title banner
        ws.merge_cells(f"A{row}:G{row}")
        c = ws.cell(row, 1)
        c.value = f"SEO Brief: {kw}"
        c.font = _font(size=13, bold=True)
        c.fill = _fill(C_TITLE_BAR)
        c.alignment = _align("center", "center", wrap=False)
        ws.row_dimensions[row].height = 24
        row += 1

        # URL row
        ws.merge_cells(f"A{row}:G{row}")
        c = ws.cell(row, 1)
        c.value = url
        c.font = Font(name="Calibri", size=9, italic=True, color="0563C1", underline="single")
        c.alignment = _align("left", "center", wrap=False)
        row += 1

        # Metadata row
        rd_pos  = rd.get("Current Position (avg)", "")
        rd_vol  = rd.get("Monthly Search Volume", "")
        rd_diff = rd.get("Keyword Difficulty (0-100)", "")
        rd_type = rd.get("Page Type", "")
        ws.merge_cells(f"A{row}:G{row}")
        c = ws.cell(row, 1)
        c.value = f"Position: {rd_pos}  |  Volume: {rd_vol}/mo  |  Difficulty: {rd_diff}/100  |  Type: {rd_type}"
        c.font = _font(size=9, color="555555", bold=False)
        c.alignment = _align("left", "center", wrap=False)
        row += 1

        # Error notice
        if result.get("error"):
            row += 1
            ws.merge_cells(f"A{row}:G{row}")
            c = ws.cell(row, 1)
            c.value = f"⚠️  {result['error']}"
            c.font = _font(size=10, bold=True, color="CC0000")
            c.fill = _fill("FFF0F0")
            row += 2

        row += 1  # spacer

        # ── Section 0: Page Audit
        row = self._section_header(ws, row, "0.  PAGE AUDIT")
        audit = result.get("audit") or {}
        if audit:
            scrape = result.get("scrape_status", "unknown").upper()
            row = self._kv(ws, row, "Scrape Status", scrape)
            row = self._kv(ws, row, "Title Tag",         audit.get("title_tag", "N/A"))
            row = self._kv(ws, row, "Meta Description",  audit.get("meta_description", "N/A"))
            row = self._kv(ws, row, "H1",                audit.get("h1", "N/A"))
            h2s = audit.get("h2s") or []
            row = self._kv(ws, row, "H2s",  "\n".join(f"• {h}" for h in h2s) if h2s else "None found")
            h3s = audit.get("h3s") or []
            row = self._kv(ws, row, "H3s",  "\n".join(f"• {h}" for h in h3s) if h3s else "None found")
            row = self._kv(ws, row, "Word Count",        str(audit.get("word_count", "N/A")))
            row = self._kv(ws, row, "Page Type Inferred",audit.get("page_type_inferred", "N/A"))
            row = self._kv(ws, row, "Schema Markup",     audit.get("schema_markup", "None detected"))
        else:
            row = self._note(ws, row, "Page audit data unavailable")

        row += 1

        # ── Section 1: On-Page Optimizations
        row = self._section_header(ws, row, "1.  ON-PAGE OPTIMIZATIONS")
        op = result.get("on_page") or {}
        if op:
            row = self._kv(ws, row, "Search Intent",      (op.get("search_intent") or "N/A").upper())
            row = self._kv(ws, row, "SERP Features",      ", ".join(op.get("serp_features") or []) or "None detected")
            row = self._kv(ws, row, "Title Tag Pattern",  op.get("title_tag_pattern", "N/A"))
            row = self._kv(ws, row, "Winning Format",     op.get("winning_content_format", "N/A"))
            row += 1

            recs = op.get("recommendations") or []
            if recs:
                row = self._table_header(ws, row, ["Element", "Current", "Recommended", "Rationale"],
                                         col_widths=[14, 32, 32, 38])
                for j, rec in enumerate(recs):
                    bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                    row = self._data_row(ws, row,
                        [rec.get("element",""), rec.get("current",""),
                         rec.get("recommended",""), rec.get("rationale","")], bg)
        else:
            row = self._note(ws, row, "On-page analysis unavailable")

        row += 1

        # ── Section 2: Content Gaps
        row = self._section_header(ws, row, "2.  CONTENT GAPS")
        gaps = result.get("content_gaps") or []
        if gaps:
            row = self._table_header(ws, row,
                ["Missing Subtopic / Angle", "Covered By", "Suggested Section Title", "Priority"],
                col_widths=[28, 30, 30, 10])
            for j, g in enumerate(gaps):
                bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                priority = g.get("priority", "")
                row = self._data_row(ws, row,
                    [g.get("missing_subtopic",""), g.get("covered_by",""),
                     g.get("suggested_section_title",""), priority], bg)
                # Colour the priority cell (col 4, previous row)
                if priority in PRIORITY_COLORS:
                    pc = ws.cell(row - 1, 4)
                    pc.fill = _fill(PRIORITY_COLORS[priority])
                    pc.font = _font(size=9, bold=True, color=C_WHITE)
                    pc.alignment = _align("center", "center")
        else:
            row = self._note(ws, row, "No content gaps identified")

        row += 1

        # ── Section 3: Keyword Expansion
        row = self._section_header(ws, row, "3.  KEYWORD EXPANSION")
        ke = result.get("keyword_expansion") or {}
        if ke:
            sem = ke.get("semantic_keywords") or []
            if sem:
                row = self._sub_header(ws, row, "Semantic Keywords to Add")
                row = self._table_header(ws, row, ["Keyword", "Where to Place", "Intent"],
                                         col_widths=[28, 32, 20])
                for j, k in enumerate(sem):
                    bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                    row = self._data_row(ws, row,
                        [k.get("keyword",""), k.get("where_to_place",""), k.get("intent","")], bg)

            row += 1

            paa = ke.get("paa_targets") or []
            if paa:
                row = self._sub_header(ws, row, "PAA Targets")
                row = self._table_header(ws, row,
                    ["Exact PAA Question", "Recommended Heading", "Answer Format"],
                    col_widths=[40, 20, 18])
                for j, q in enumerate(paa):
                    bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                    row = self._data_row(ws, row,
                        [q.get("question",""), q.get("recommended_heading",""),
                         q.get("answer_format","")], bg)

            row += 1

            fso = ke.get("featured_snippet_opportunity") or {}
            if fso and fso.get("trigger_query"):
                row = self._sub_header(ws, row, "Featured Snippet Opportunity")
                row = self._kv(ws, row, "Trigger Query",       fso.get("trigger_query", ""))
                row = self._kv(ws, row, "Current Status",      fso.get("current_status", ""))
                row = self._kv(ws, row, "Recommended Format",  fso.get("recommended_format", ""))
                row = self._kv(ws, row, "Implementation",      fso.get("implementation", ""))
        else:
            row = self._note(ws, row, "Keyword expansion data unavailable")

        row += 1

        # ── Section 4: Schema & Technical
        row = self._section_header(ws, row, "4.  SCHEMA & TECHNICAL QUICK WINS")
        st = result.get("schema_technical") or {}
        if st:
            schema_types = st.get("recommended_schema_types") or []
            row = self._kv(ws, row, "Recommended Schema Types", ", ".join(schema_types) or "None")

            lc = st.get("length_check") or {}
            if lc:
                tlen = lc.get("title_length", "N/A")
                tok  = lc.get("title_ok", True)
                mlen = lc.get("meta_length", "N/A")
                mok  = lc.get("meta_ok", True)
                tstr = f"{tlen} chars — {'✓ OK' if tok else '⚠️ TOO LONG (>60 chars)'}"
                mstr = f"{mlen} chars — {'✓ OK' if mok else '⚠️ TOO LONG (>160 chars)'}"
                row = self._kv(ws, row, "Title Tag Length", tstr,
                               val_color=("FFF0F0" if not tok else None))
                row = self._kv(ws, row, "Meta Description Length", mstr,
                               val_color=("FFF0F0" if not mok else None))

            row += 1

            faq = st.get("faq_qa_pairs") or []
            if faq:
                row = self._sub_header(ws, row, "FAQ Schema — Q&A Pairs")
                row = self._table_header(ws, row, ["Question", "Answer"], col_widths=[38, 48])
                for j, qa in enumerate(faq):
                    bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                    row = self._data_row(ws, row, [qa.get("question",""), qa.get("answer","")], bg)
                row += 1

            links = st.get("internal_linking") or []
            if links:
                row = self._sub_header(ws, row, "Internal Linking Opportunities")
                row = self._table_header(ws, row,
                    ["Source Page (describe by topic/type)", "Anchor Text", "Reason"],
                    col_widths=[32, 22, 38])
                for j, lk in enumerate(links):
                    bg = C_ROW_ALT if j % 2 == 0 else C_WHITE
                    row = self._data_row(ws, row,
                        [lk.get("source_description",""), lk.get("anchor_text",""),
                         lk.get("reason","")], bg)
        else:
            row = self._note(ws, row, "Schema & technical data unavailable")

        row += 1

        # ── Section 5: Priority Score
        row = self._section_header(ws, row, "5.  PRIORITY SCORE & EFFORT ESTIMATE")
        ps = result.get("priority_score") or {}
        if ps:
            row = self._kv(ws, row, "Current Position",   str(ps.get("current_position", "")))
            opp = ps.get("opportunity_size", "")
            row = self._kv(ws, row, "Opportunity Size",   opp,
                           val_color=PRIORITY_COLORS.get(opp))
            row = self._kv(ws, row, "Opportunity Reason", ps.get("opportunity_reason", ""))
            row = self._kv(ws, row, "Estimated Effort",   ps.get("estimated_effort", ""))
            row = self._kv(ws, row, "Effort Reason",      ps.get("effort_reason", ""))
            qw   = ps.get("quick_win_available")
            qw_s = "YES" if qw else ("NO" if qw is False else "Unknown")
            row = self._kv(ws, row, "Quick Win?", qw_s,
                           val_color=(C_WIN_YES if qw else C_WIN_NO if qw is False else None))
            row = self._kv(ws, row, "Quick Win Description", ps.get("quick_win_description", ""))
            row = self._kv(ws, row, "Recommended Action",    ps.get("recommended_action", ""))
            row = self._kv(ws, row, "Action Detail",         ps.get("action_detail", ""))
        else:
            row = self._note(ws, row, "Priority score unavailable")

        # ── Column widths for the page sheet
        for col_letter, width in [("A", 22), ("B", 52), ("C", 52), ("D", 38),
                                   ("E", 12), ("F", 12), ("G", 12)]:
            ws.column_dimensions[col_letter].width = width

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _section_header(self, ws, row: int, title: str, ncols: int = 7) -> int:
        ws.merge_cells(f"A{row}:{get_column_letter(ncols)}{row}")
        c = ws.cell(row, 1)
        c.value = f"  {title}"
        c.font = _font(size=11, bold=True)
        c.fill = _fill(C_SECTION)
        c.alignment = _align("left", "center", wrap=False, indent=1)
        ws.row_dimensions[row].height = 20
        return row + 1

    def _sub_header(self, ws, row: int, title: str, ncols: int = 7) -> int:
        ws.merge_cells(f"A{row}:{get_column_letter(ncols)}{row}")
        c = ws.cell(row, 1)
        c.value = title
        c.font = _font(size=10, bold=True)
        c.fill = _fill(C_COL_HDR)
        c.alignment = _align("left", "center", wrap=False, indent=1)
        ws.row_dimensions[row].height = 18
        return row + 1

    def _table_header(self, ws, row: int, columns: list, col_widths: list = None) -> int:
        for col, header in enumerate(columns, 1):
            c = ws.cell(row, col)
            c.value = header
            c.font = _font(size=9, bold=True)
            c.fill = _fill(C_SUB_HDR)
            c.alignment = _align("center", "center", wrap=True)
            c.border = _thin_border()
            if col_widths and col - 1 < len(col_widths):
                ws.column_dimensions[get_column_letter(col)].width = col_widths[col - 1]
        ws.row_dimensions[row].height = 18
        return row + 1

    def _data_row(self, ws, row: int, values: list, bg: str = C_WHITE) -> int:
        for col, val in enumerate(values, 1):
            c = ws.cell(row, col)
            c.value = str(val) if val is not None else ""
            c.font = _font(size=9, color="000000")
            c.fill = _fill(bg)
            c.alignment = _align("left", "top", wrap=True)
            c.border = _thin_border()
        ws.row_dimensions[row].height = 50
        return row + 1

    def _kv(self, ws, row: int, key: str, value, val_color: str = None) -> int:
        """Write a key-value row spanning columns A-B:G."""
        kc = ws.cell(row, 1)
        kc.value = key
        kc.font = _font(size=9, bold=True, color="000000")
        kc.fill = _fill(C_KEY_CELL)
        kc.alignment = _align("left", "top", wrap=True)
        kc.border = _thin_border()

        ws.merge_cells(f"B{row}:G{row}")
        vc = ws.cell(row, 2)
        vc.value = str(value) if value is not None else ""
        vc.font = _font(size=9, color="000000")
        vc.fill = _fill(val_color or C_WHITE)
        vc.alignment = _align("left", "top", wrap=True)
        vc.border = _thin_border()

        ws.row_dimensions[row].height = 38
        return row + 1

    def _note(self, ws, row: int, text: str, ncols: int = 7) -> int:
        ws.merge_cells(f"A{row}:{get_column_letter(ncols)}{row}")
        c = ws.cell(row, 1)
        c.value = f"  ℹ  {text}"
        c.font = Font(name="Calibri", size=9, italic=True, color="777777")
        c.fill = _fill(C_LIGHT_GRAY)
        c.alignment = _align("left", "center", wrap=False, indent=1)
        ws.row_dimensions[row].height = 18
        return row + 1
