"""
PNR Seat Assignment & Change Report
-----------------------------------
Reads a PNR Admin page, finds the reservation date and the payment date on the page
itself, keeps the Reservation History between those two moments, and shows each
coupon's initial seat and the seat it was changed to.

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import re
from datetime import datetime, time

import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import seat_parser as sp

st.set_page_config(page_title="PNR Seat Change Report", page_icon="✈️", layout="wide")

# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
st.sidebar.header("1. PNR page")
uploaded = st.sidebar.file_uploader("Upload the PNR page as .txt", type=["txt", "log", "csv"])
pasted = st.sidebar.text_area(
    "…or paste the PNR page here",
    height=200,
    placeholder="Open the PNR, select all (Ctrl+A), copy, paste here.",
)
whole_doc = st.sidebar.checkbox(
    "Scan the whole page",
    value=False,
    help="By default only the part after the 'Reservation History' heading is read. "
         "Tick this if your paste doesn't include that heading.",
)

raw_text = ""
if uploaded is not None:
    raw_text = uploaded.getvalue().decode("utf-8", errors="ignore")
elif pasted.strip():
    raw_text = pasted

st.title("✈️ PNR Seat Assignment & Change Report")

if not raw_text.strip():
    st.info(
        "Upload or paste the PNR page in the sidebar.\n\n"
        "The app reads the **reservation date** (Ticket Time / installment payment row) "
        "and the **payment date** (the Ticket Sale row) off the page, then lists every "
        "seat assignment and seat change between them."
    )
    st.stop()

# --------------------------------------------------------------------------- #
# Dates read off the page
# --------------------------------------------------------------------------- #
pnr_m = re.search(r"PNR:\s*([A-Z0-9]{5,7})", raw_text)
pnr = pnr_m.group(1) if pnr_m else ""

detected = sp.detect_dates(raw_text)
res_dt = detected["reservation"]
pay_dt = detected["payment"]

d1, d2 = st.columns(2)
d1.metric("Reservation date (from page)",
          res_dt.strftime("%d-%b-%Y %I:%M %p") if res_dt else "not found")
d2.metric("Payment date (from page)",
          pay_dt.strftime("%d-%b-%Y %I:%M %p") if pay_dt else "not found")

use_detected = st.checkbox(
    "Use the dates found on the page", value=bool(res_dt and pay_dt)
)

if use_detected and res_dt and pay_dt:
    start_dt, end_dt = res_dt, pay_dt
else:
    st.caption("Set the range manually:")
    c1, c2, c3, c4 = st.columns(4)
    sd = c1.date_input("From date", (res_dt or datetime(2026, 6, 30)).date())
    stime = c2.time_input("From time", (res_dt or datetime(2026, 6, 30, 23, 17)).time())
    ed = c3.date_input("To date", (pay_dt or datetime(2026, 8, 4)).date())
    etime = c4.time_input("To time", (pay_dt or datetime(2026, 8, 4, 14, 52)).time())
    start_dt = datetime.combine(sd, stime)
    end_dt = datetime.combine(ed, etime)

if start_dt > end_dt:
    st.error("The start date/time is after the end date/time.")
    st.stop()

# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #
o1, o2, o3 = st.columns([2, 2, 1])
view = o1.radio(
    "Table",
    ["Summary (one row per coupon)", "Full seat event log"],
    horizontal=True,
)
date_style = o2.selectbox("Date format", ["%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"])
only_changed = o3.checkbox("Only changed", value=False)

# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
all_events = sp.extract_events(raw_text, whole_document=whole_doc)
events = sp.filter_events(all_events, start_dt, end_dt)

if not all_events:
    st.warning(
        "No seat entries were found. If your paste doesn't include the "
        "'Reservation History' heading, tick **Scan the whole page** in the sidebar."
    )
    st.stop()

summary = sp.build_summary(events, date_fmt=date_style)
if only_changed and not summary.empty:
    summary = summary[summary["Status"] == "Changed"].reset_index(drop=True)
    summary["S No"] = range(1, len(summary) + 1)

event_log = sp.build_event_log(events)
table = summary if view.startswith("Summary") else event_log

m1, m2, m3, m4 = st.columns(4)
m1.metric("Seat entries in range", len(events))
m2.metric("Coupons", summary["Coupon Number"].nunique() if not summary.empty else 0)
m3.metric("Seats changed",
          int((summary["Status"] == "Changed").sum()) if not summary.empty else 0)
m4.metric("Entries outside range", len(all_events) - len(events))

st.caption(
    (f"PNR {pnr} — " if pnr else "")
    + f"reservation history from **{start_dt:%d-%b-%Y %I:%M %p}** "
      f"to **{end_dt:%d-%b-%Y %I:%M %p}**"
)

if table.empty:
    st.warning("No seat entries fall inside this range.")
    st.stop()

st.dataframe(table, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- #
# Excel export
# --------------------------------------------------------------------------- #
def to_excel(df: pd.DataFrame, title: str, subtitle: str) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Seat Report", startrow=2)
        ws = writer.sheets["Seat Report"]

        ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=13)
        ws.cell(row=2, column=1, value=subtitle).font = Font(italic=True, size=9)

        thin = Side(style="thin", color="000000")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        head_fill = PatternFill("solid", fgColor="D9D9D9")
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_row = 3

        for col in range(1, len(df.columns) + 1):
            c = ws.cell(row=header_row, column=col)
            c.font, c.fill, c.border, c.alignment = Font(bold=True), head_fill, border, center

        for row in range(header_row + 1, header_row + 1 + len(df)):
            for col in range(1, len(df.columns) + 1):
                c = ws.cell(row=row, column=col)
                c.border, c.alignment = border, center

        for col, name in enumerate(df.columns, start=1):
            longest = max([len(str(name))] + [len(str(v)) for v in df[name].tolist()])
            ws.column_dimensions[get_column_letter(col)].width = min(max(longest + 4, 12), 60)

        ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    return buf.getvalue()


subtitle = (
    (f"PNR {pnr} | " if pnr else "")
    + f"Reservation {start_dt:%d-%b-%Y %I:%M %p}  →  Payment {end_dt:%d-%b-%Y %I:%M %p}"
)
sheet_title = ("Seat Assignment & Change Report" if view.startswith("Summary")
               else "Seat Event Log")
stamp = datetime.now().strftime("%Y%m%d_%H%M")
base = f"seat_report_{pnr or 'PNR'}_{stamp}"

b1, b2 = st.columns([1, 4])
b1.download_button(
    "⬇️ Download Excel",
    data=to_excel(table, sheet_title, subtitle),
    file_name=f"{base}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
b2.download_button(
    "⬇️ Download CSV",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name=f"{base}.csv",
    mime="text/csv",
)

# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
with st.expander("Diagnostics — what was read, what was skipped"):
    st.write(f"Seat entries found on the whole page: **{len(all_events)}**")
    if all_events:
        st.write(
            f"Earliest: {all_events[0].when:%d-%b-%Y %I:%M %p} · "
            f"Latest: {all_events[-1].when:%d-%b-%Y %I:%M %p}"
        )
    missed = sp.unmatched_lines(raw_text, whole_document=whole_doc)
    if missed:
        st.write("Lines mentioning a coupon and a seat that produced no entry:")
        st.code("\n".join(missed))
    else:
        st.write("No seat-related lines were skipped.")

with st.expander("How the report is built"):
    st.markdown(
        """
- The page is scanned for date/time stamps; each record is read from the stamp that
  precedes it, so tabs, columns and line breaks don't matter.
- **Reservation date** comes from `Ticket Time`, falling back to the installment
  payment row. **Payment date** comes from the `Ticket Sale` row.
- Only entries between those two moments are used.
- Per coupon: the earliest entry gives *Initial Seat* and *Initial Assignment Date*;
  the last entry that moves the seat gives *New Seat* and *Seat Change Date*.
- `SSR added/removed` rows are ignored — they don't move a seat.
- The website writes each reassignment twice at the same second; the duplicate is
  dropped so a change is counted once.
        """
    )
